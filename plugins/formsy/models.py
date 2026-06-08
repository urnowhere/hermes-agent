"""Pydantic models for the FormalCC Runtime API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class SceneType(str, Enum):
    AUTO = "auto"
    CODING = "coding"
    VISION_DOC = "vision_doc"
    MEMORY_RECALL = "memory_recall"
    GENERAL = "general"


class TaskType(str, Enum):
    BUGFIX = "bugfix"
    PATCH_GENERATION = "patch_generation"
    DOC_QA = "doc_qa"
    UNANSWERABLE_DOC_QA = "unanswerable_doc_qa"
    MEMORY_RECALL = "memory_recall"
    GENERAL = "general"


class AttachmentKind(str, Enum):
    PDF = "pdf"
    IMAGE = "image"
    DOCX = "docx"
    TEXT = "text"
    OTHER = "other"


class EvidenceKind(str, Enum):
    CODE_SYMBOL = "code_symbol"
    CODE_SNIPPET = "code_snippet"
    TEST_CASE = "test_case"
    DOCUMENT_PAGE = "document_page"
    OCR_SPAN = "ocr_span"
    MEMORY_FACT = "memory_fact"
    CONSTRAINT = "constraint"
    OTHER = "other"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecommendedAction(str, Enum):
    ANSWER = "answer"
    CAUTIOUS_ANSWER = "cautious_answer"
    ABSTAIN = "abstain"
    PATCH = "patch"
    REQUEST_MORE_CONTEXT = "request_more_context"
    NONE = "none"


class ArtifactType(str, Enum):
    CODE_CONTEXT = "code_context"
    VISION_CONTEXT = "vision_context"
    MEMORY_SNAPSHOT = "memory_snapshot"
    PATCH = "patch"
    ADVISORY = "advisory"
    REPO_SCAN = "repo_scan"
    EPISODE_STORE = "episode_store"
    FACT_STORE = "fact_store"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContentBlockType(str, Enum):
    TEXT = "text"
    IMAGE_URL = "image_url"


class SyncMode(str, Enum):
    ASYNC_BEST_EFFORT = "async_best_effort"
    SYNC_REQUIRED = "sync_required"


class ArtifactRef(BaseModel):
    artifact_id: str
    artifact_type: ArtifactType
    workspace_id: str
    scene: Optional[SceneType] = None
    version: Optional[str] = None
    lineage: Optional[List[str]] = None
    ttl_s: int = Field(default=3600, ge=0)
    created_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class Identity(BaseModel):
    user_id: Optional[str] = None
    repo_id: Optional[str] = None
    branch: Optional[str] = None
    revision: Optional[str] = None
    document_id: Optional[str] = None
    document_version: Optional[str] = None
    profile_id: Optional[str] = None


class TestNames(BaseModel):
    fail_to_pass: List[str] = Field(default_factory=list)
    pass_to_pass: List[str] = Field(default_factory=list)


class Task(BaseModel):
    instruction: str = Field(min_length=1)
    task_type: Optional[TaskType] = None
    problem_statement: Optional[str] = None
    question: Optional[str] = None
    hint_text: Optional[str] = None
    test_names: Optional[TestNames] = None


class Attachment(BaseModel):
    attachment_id: str
    kind: AttachmentKind
    name: Optional[str] = None
    mime_type: Optional[str] = None
    storage_uri: Optional[str] = None


class Budget(BaseModel):
    token_budget: Optional[int] = Field(default=None, ge=256)
    latency_budget_ms: Optional[int] = Field(default=None, ge=50)
    budget_pages: Optional[int] = Field(default=None, ge=1)
    top_k: Optional[int] = Field(default=None, ge=1)


class Hints(BaseModel):
    bypass_router: bool = False
    return_patch: bool = False
    return_advisory: bool = True
    focus_topic: Optional[str] = None


class TextContentBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageUrl(BaseModel):
    url: str
    detail: Optional[Literal["auto", "low", "high"]] = None


class ImageUrlContentBlock(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: ImageUrl


ContentBlock = Union[TextContentBlock, ImageUrlContentBlock]
MessageContent = Union[str, List[ContentBlock]]


class CompiledMessage(BaseModel):
    role: MessageRole
    content: MessageContent
    name: Optional[str] = None
    tool_call_id: Optional[str] = None


class RuntimeRequest(BaseModel):
    scene: SceneType = SceneType.AUTO
    workspace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    tenant_id: Optional[str] = None
    identity: Optional[Identity] = None
    task: Task
    attachments: Optional[List[Attachment]] = None
    budget: Optional[Budget] = None
    hints: Optional[Hints] = Field(default_factory=Hints)
    artifacts: Optional[List[ArtifactRef]] = None


class CompileResponse(BaseModel):
    bundle: "CompileBundle"


class CompileRequest(BaseModel):
    scene: SceneType = SceneType.AUTO
    workspace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    identity: Optional[Dict[str, Any]] = None
    task: Optional[Union[Task, Dict[str, Any]]] = None
    hints: Optional[Dict[str, Any]] = None


class MemoryPrefetchRequest(BaseModel):
    workspace_id: str
    session_id: str
    turn_id: str
    identity: Optional[Identity] = None
    query: str = Field(min_length=1)
    conversation_window: Optional[List[Dict[str, Any]]] = None
    budget: Optional[Dict[str, int]] = None


class MemoryPrefetchResponse(BaseModel):
    memory_block: str
    metrics: Optional[Dict[str, Any]] = None
    retrieved_facts: Optional[List[Dict[str, Any]]] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    advisory: Optional[Dict[str, Any]] = None

    @property
    def elapsed_ms(self) -> int:
        """Compatibility accessor — server nests elapsed_ms inside metrics."""
        if isinstance(self.metrics, dict):
            return int(self.metrics.get("elapsed_ms") or 0)
        return 0

    @property
    def retrieved_count(self) -> int:
        return len(self.retrieved_facts) if self.retrieved_facts else 0


class CodingSummary(BaseModel):
    task_type: Optional[str] = None
    problem_summary: Optional[str] = None
    accepted_targets: List[str] = Field(default_factory=list)
    changed_files: List[str] = Field(default_factory=list)
    changed_symbols: List[str] = Field(default_factory=list)
    root_cause: Optional[str] = None
    patch_summary: Optional[str] = None
    tests_run: List[str] = Field(default_factory=list)
    test_result: Optional[str] = None
    failure_lessons: List[str] = Field(default_factory=list)
    context_query: Optional[str] = None
    retrieval_state: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    completion_gate_decision: Optional[str] = None
    completion_audit: Optional[Dict[str, Any]] = None
    workspace_fingerprint: Optional[str] = None


class MemorySyncTurnRequest(BaseModel):
    workspace_id: str
    session_id: str
    turn_id: str
    identity: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, Any]]
    sync_mode: SyncMode = SyncMode.ASYNC_BEST_EFFORT
    coding_summary: Optional[CodingSummary] = None
    artifacts: Optional[List[ArtifactRef]] = None


class SessionEndRequest(BaseModel):
    workspace_id: str
    session_id: str
    identity: Optional[Dict[str, Any]] = None
    summary_hint: Optional[str] = None


class MemorySearchRequest(BaseModel):
    workspace_id: str
    session_id: str
    query: str = Field(min_length=1)
    turn_id: Optional[str] = None
    identity: Optional[Identity] = None
    top_k: int = Field(default=5, ge=1)


class RepoRef(BaseModel):
    repo_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    storage_uri: Optional[str] = None


class RepoOptions(BaseModel):
    scan: bool = True
    ingest: bool = True
    enable_w2: bool = True


class EnsureRepoReadyRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    repo_ref: RepoRef
    options: Optional[RepoOptions] = Field(default_factory=RepoOptions)


class DocumentRef(BaseModel):
    document_id: str = Field(min_length=1)
    document_version: Optional[str] = None
    storage_uri: Optional[str] = None


class DocumentOptions(BaseModel):
    ocr: bool = True
    index: bool = True
    mode: Literal["hybrid", "text", "vision"] = "hybrid"


class EnsureDocumentReadyRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    document_ref: DocumentRef
    options: Optional[DocumentOptions] = Field(default_factory=DocumentOptions)


class Citation(BaseModel):
    source_type: str
    source_id: str
    locator: str


class EvidenceUnit(BaseModel):
    evidence_id: str
    kind: EvidenceKind
    summary: str
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    citations: Optional[List[Citation]] = None
    payload: Optional[Dict[str, Any]] = None


class RiskSignal(BaseModel):
    code: str
    level: RiskLevel
    message: Optional[str] = None


class Advisory(BaseModel):
    recommended_action: Optional[RecommendedAction] = None
    answerability_score: Optional[float] = Field(default=None, ge=0, le=1)
    citation_risk: Optional[RiskLevel] = None
    rationale_tail: Optional[str] = None


class BudgetStats(BaseModel):
    token_budget: Optional[int] = None
    tokens_used: Optional[int] = None
    budget_pages: Optional[int] = None
    pages_used: Optional[int] = None


class Metrics(BaseModel):
    elapsed_ms: int = Field(ge=0)
    scene_router_elapsed_ms: Optional[int] = Field(default=None, ge=0)
    engine_elapsed_ms: Optional[int] = Field(default=None, ge=0)


class Routing(BaseModel):
    confidence: float = Field(ge=0, le=1)
    routing_reason: List[str]
    fallback_scene: Optional[str] = None


class CompileBundle(BaseModel):
    scene: SceneType
    compiled_messages: List[CompiledMessage]
    metrics: Metrics
    routing: Optional[Routing] = None
    evidence_units: Optional[List[EvidenceUnit]] = None
    supported_claims: Optional[List[str]] = None
    missing_dependencies: Optional[List[str]] = None
    risk_signals: Optional[List[RiskSignal]] = None
    advisory: Optional[Advisory] = None
    artifacts: Optional[List[ArtifactRef]] = None
    budget_stats: Optional[BudgetStats] = None


class RetrievedFact(BaseModel):
    expression: str
    score: Optional[float] = Field(default=None, ge=0, le=1)
    source_session_id: Optional[str] = None
    fact_id: Optional[str] = None


class MemoryPrefetchBundle(BaseModel):
    memory_block: str
    metrics: Metrics
    retrieved_facts: Optional[List[RetrievedFact]] = None
    artifacts: Optional[List[ArtifactRef]] = None
    advisory: Optional[Dict[str, Any]] = None


class AsyncJobResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    job_id: str
    async_: bool = Field(alias="async", default=True)


class ResourceReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    artifacts: List[ArtifactRef]


class ErrorType(str, Enum):
    INVALID_INPUT = "InvalidInput"
    NOT_FOUND = "NotFound"
    CONFLICT = "Conflict"
    UNSUPPORTED_SCENE = "UnsupportedScene"
    TIMEOUT = "Timeout"
    UPSTREAM_FAILURE = "UpstreamFailure"
    INTERNAL_ERROR = "InternalError"


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    error_type: ErrorType
    error_message: str
    error_details: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None


if "CompileResponse" in globals():
    CompileResponse.model_rebuild()
