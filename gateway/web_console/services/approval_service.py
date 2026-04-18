"""Human approval and clarification coordination for the Hermes Web Console."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_HUMAN_REQUESTS = 200
TERMINAL_REQUEST_STATUSES = {"resolved", "expired"}


@dataclass(slots=True)
class PendingHumanRequest:
    request_id: str
    kind: str
    session_id: str | None
    run_id: str | None
    title: str
    prompt: str
    choices: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = False
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    status: str = "pending"
    response: Any = None
    response_queue: queue.Queue[Any] = field(default_factory=queue.Queue)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "title": self.title,
            "prompt": self.prompt,
            "choices": list(self.choices),
            "metadata": dict(self.metadata),
            "sensitive": self.sensitive,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "response": self.response,
        }


class ApprovalService:
    """Tracks pending human approval/clarification requests and resolves them."""

    def __init__(self) -> None:
        self._requests: dict[str, PendingHumanRequest] = {}
        self._lock = threading.Lock()

    def _prune_requests_if_needed(self) -> None:
        while len(self._requests) > MAX_HUMAN_REQUESTS:
            evicted = False
            for request_id, request in list(self._requests.items()):
                if request.status in TERMINAL_REQUEST_STATUSES:
                    del self._requests[request_id]
                    evicted = True
                    break
            if evicted:
                continue
            oldest_request_id = min(self._requests.items(), key=lambda item: item[1].created_at)[0]
            del self._requests[oldest_request_id]

    def _create_request(
        self,
        *,
        kind: str,
        session_id: str | None,
        run_id: str | None,
        title: str,
        prompt: str,
        choices: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        sensitive: bool = False,
        timeout: float = 60.0,
    ) -> PendingHumanRequest:
        request = PendingHumanRequest(
            request_id=str(uuid.uuid4()),
            kind=kind,
            session_id=session_id,
            run_id=run_id,
            title=title,
            prompt=prompt,
            choices=list(choices or []),
            metadata=dict(metadata or {}),
            sensitive=sensitive,
            expires_at=time.time() + timeout,
        )
        with self._lock:
            self._requests[request.request_id] = request
            self._prune_requests_if_needed()
        return request

    def _finish_request(self, request: PendingHumanRequest, *, status: str, response: Any) -> None:
        request.status = status
        request.response = response
        request.response_queue.put(response)

    def list_pending(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            pending = []
            for request in self._requests.values():
                if request.status == "pending" and request.expires_at <= now:
                    request.status = "expired"
                    request.response = None
                if request.status == "pending":
                    pending.append(request.to_dict())
            pending.sort(key=lambda item: item["created_at"])
            return pending

    def get_request(self, request_id: str) -> PendingHumanRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def resolve_approval(self, request_id: str, decision: str) -> dict[str, Any] | None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.kind != "approval" or request.status != "pending":
                return None
            self._finish_request(request, status="resolved", response=decision)
            return request.to_dict()

    def resolve_clarification(self, request_id: str, response: str) -> dict[str, Any] | None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.kind != "clarify" or request.status != "pending":
                return None
            self._finish_request(request, status="resolved", response=response)
            return request.to_dict()

    def deny_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.status != "pending":
                return None
            denied_value = "deny" if request.kind == "approval" else None
            self._finish_request(request, status="resolved", response=denied_value)
            return request.to_dict()

    def create_approval_callback(self, *, session_id: str | None = None, run_id: str | None = None) -> Callable[..., str]:
        def callback(command: str, description: str, *, allow_permanent: bool = True) -> str:
            choices = ["once", "session", "deny"]
            if allow_permanent:
                choices.insert(2, "always")
            request = self._create_request(
                kind="approval",
                session_id=session_id,
                run_id=run_id,
                title="Approve dangerous command",
                prompt=description,
                choices=choices,
                metadata={"command": command, "description": description},
                timeout=60.0,
            )
            try:
                result = request.response_queue.get(timeout=60.0)
                return result if isinstance(result, str) else "deny"
            except queue.Empty:
                request.status = "expired"
                request.response = "deny"
                return "deny"

        return callback

    def create_clarify_callback(self, *, session_id: str | None = None, run_id: str | None = None) -> Callable[[str, list[str] | None], str]:
        def callback(question: str, choices: list[str] | None) -> str:
            request = self._create_request(
                kind="clarify",
                session_id=session_id,
                run_id=run_id,
                title="Clarification needed",
                prompt=question,
                choices=list(choices or []),
                timeout=120.0,
            )
            try:
                result = request.response_queue.get(timeout=120.0)
                return result if isinstance(result, str) else (
                    "The user did not provide a response within the time limit. "
                    "Use your best judgement to make the choice and proceed."
                )
            except queue.Empty:
                request.status = "expired"
                request.response = None
                return (
                    "The user did not provide a response within the time limit. "
                    "Use your best judgement to make the choice and proceed."
                )

        return callback
