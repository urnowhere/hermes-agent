"""Tests for FormSy memory provider shared identity snapshot behavior."""

import asyncio

from agent.runtime_identity import ResolvedIdentitySnapshot
from plugins.memory.formsy_memory.config import MemoryConfig
from plugins.memory.formsy_memory.provider import FormSyMemoryProvider
from plugins.formsy.models import ArtifactType


class _CaptureMemoryClient:
    def __init__(self, fail=False):
        self.sync_calls = []
        self.fail = fail

    async def sync_turn(
        self,
        workspace_id,
        session_id,
        turn_id,
        messages,
        identity=None,
        sync_mode=None,
        coding_summary=None,
        artifacts=None,
    ):
        if self.fail:
            raise RuntimeError("simulated network failure")
        self.sync_calls.append(
            {
                "workspace_id": workspace_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "messages": messages,
                "identity": identity,
                "sync_mode": sync_mode,
                "coding_summary": coding_summary,
                "artifacts": artifacts,
            }
        )


def _make_provider(workspace_id="ws_formsy", session_id="session-1", revision="rev-1"):
    provider = FormSyMemoryProvider()
    provider._config = MemoryConfig(workspace_id=workspace_id)
    provider._memory_client = _CaptureMemoryClient()
    provider._run_async = lambda coro: asyncio.run(coro)
    provider._session_id = session_id
    provider._turn_id = f"{session_id}:turn:1"
    provider._identity_snapshot = ResolvedIdentitySnapshot(
        workspace_id=workspace_id,
        session_id=session_id,
        turn_id=f"{session_id}:turn:1",
        repo_id="repo__demo",
        revision=revision,
    )
    return provider


def test_formsy_memory_sync_turn_uses_updated_shared_snapshot_revision():
    provider = _make_provider(revision="rev-old")
    provider._identity_snapshot.revision = "rev-new"

    provider.sync_turn("user message", "assistant message")

    call = provider._memory_client.sync_calls[0]
    assert call["workspace_id"] == "ws_formsy"
    assert call["session_id"] == "session-1"
    assert call["turn_id"] == "session-1:turn:1"
    assert call["identity"] == {
        "repo_id": "repo__demo",
        "revision": "rev-new",
    }


def test_sync_turn_passes_coding_summary_when_accepted_targets_provided():
    provider = _make_provider()

    provider.sync_turn(
        "fix the bug",
        "done",
        accepted_targets=["src/foo.py"],
        changed_files=["src/foo.py"],
    )

    call = provider._memory_client.sync_calls[0]
    cs = call["coding_summary"]
    assert cs is not None
    assert cs.accepted_targets == ["src/foo.py"]
    assert cs.changed_files == ["src/foo.py"]


def test_sync_turn_no_coding_summary_when_no_kwargs():
    provider = _make_provider()

    provider.sync_turn("hello", "world")

    call = provider._memory_client.sync_calls[0]
    assert call["coding_summary"] is None


def test_sync_turn_includes_artifact_refs_from_context_artifacts():
    provider = _make_provider()
    provider.record_context_artifacts(["artifact-abc", "artifact-xyz"])

    provider.sync_turn("user query", "response")

    call = provider._memory_client.sync_calls[0]
    artifacts = call["artifacts"]
    assert artifacts is not None
    assert len(artifacts) == 2
    ids = {a.artifact_id for a in artifacts}
    assert ids == {"artifact-abc", "artifact-xyz"}
    for a in artifacts:
        assert a.artifact_type == ArtifactType.CODE_CONTEXT
        assert a.workspace_id == "ws_formsy"


def test_sync_turn_no_artifacts_when_none_recorded():
    provider = _make_provider()

    provider.sync_turn("user", "assistant")

    call = provider._memory_client.sync_calls[0]
    assert call["artifacts"] is None


def test_record_context_artifacts_deduplicates():
    provider = _make_provider()

    provider.record_context_artifacts(["art-1", "art-2", "art-1"])
    provider.record_context_artifacts(["art-2", "art-3"])

    assert provider._context_artifact_ids == ["art-1", "art-2", "art-3"]


def test_reset_turn_memory_trace_clears_context_artifacts_and_terminal_calls():
    provider = _make_provider()
    provider.record_context_artifacts(["art-1"])
    provider.record_terminal_call("pytest tests/", "1 passed")

    provider._reset_turn_memory_trace()

    assert provider._context_artifact_ids == []
    assert provider._terminal_calls == []


def test_on_turn_start_resets_per_turn_state():
    provider = _make_provider()
    provider.record_context_artifacts(["art-1"])
    provider.record_terminal_call("make test", "ok")

    provider.on_turn_start(2, "new message")

    assert provider._context_artifact_ids == []
    assert provider._terminal_calls == []
    assert provider._turn_counter == 2


def test_build_coding_summary_with_terminal_calls():
    provider = _make_provider()
    provider.record_terminal_call("pytest tests/foo_test.py", "1 passed")
    provider.record_terminal_call("pytest tests/bar_test.py", "2 passed")

    provider.sync_turn("user", "assistant", task_type="bugfix")

    call = provider._memory_client.sync_calls[0]
    cs = call["coding_summary"]
    assert cs is not None
    assert cs.task_type == "bugfix"
    assert "pytest tests/foo_test.py" in cs.tests_run
    assert "pytest tests/bar_test.py" in cs.tests_run


def test_sync_turn_coding_summary_confidence_clamped():
    provider = _make_provider()

    provider.sync_turn("u", "a", accepted_targets=["f.py"], confidence=1.5)

    call = provider._memory_client.sync_calls[0]
    assert call["coding_summary"].confidence == 1.0

    provider2 = _make_provider()
    provider2.sync_turn("u", "a", accepted_targets=["f.py"], confidence=-0.1)
    call2 = provider2._memory_client.sync_calls[0]
    assert call2["coding_summary"].confidence == 0.0


# ---------------------------------------------------------------------------
# P3-2: Pending sync queue tests
# ---------------------------------------------------------------------------


def _make_failing_provider():
    provider = _make_provider()
    provider._memory_client = _CaptureMemoryClient(fail=True)
    return provider


def test_failed_sync_turn_is_queued():
    provider = _make_failing_provider()

    provider.sync_turn("user", "assistant")

    assert len(provider._pending_sync_queue) == 1
    assert provider._pending_sync_queue[0]["session_id"] == "session-1"


def test_pending_queue_flushed_on_next_sync_turn():
    provider = _make_failing_provider()
    provider.sync_turn("first", "response")
    assert len(provider._pending_sync_queue) == 1

    # Restore a working client
    working_client = _CaptureMemoryClient()
    provider._memory_client = working_client
    provider._run_async = lambda coro: asyncio.run(coro)

    provider.sync_turn("second", "response")

    # Both the queued event and the new event should have been sent
    assert len(provider._pending_sync_queue) == 0
    assert len(working_client.sync_calls) == 2


def test_pending_queue_overflow_drops_oldest():
    provider = _make_failing_provider()
    provider._pending_sync_queue_max = 3

    for i in range(4):
        provider._turn_id = f"session-1:turn:{i + 1}"
        provider.sync_turn(f"user {i}", f"assistant {i}")

    assert len(provider._pending_sync_queue) == 3
    # Oldest (turn:1) should have been dropped; turn:2 is now first
    assert provider._pending_sync_queue[0]["turn_id"] == "session-1:turn:2"


def test_shutdown_flushes_pending_queue():
    provider = _make_failing_provider()
    provider.sync_turn("user", "assistant")
    assert len(provider._pending_sync_queue) == 1

    # Replace client with a working one before shutdown
    working_client = _CaptureMemoryClient()
    provider._memory_client = working_client
    provider._run_async = lambda coro: asyncio.run(coro)
    # Prevent the __aexit__ call from failing (no real client)
    provider._runtime_client = None

    provider.shutdown()

    assert len(provider._pending_sync_queue) == 0
    assert len(working_client.sync_calls) == 1


def test_flush_keeps_still_failing_events():
    provider = _make_failing_provider()
    provider.sync_turn("user", "assistant")
    assert len(provider._pending_sync_queue) == 1

    # Flush attempt also fails
    provider._flush_pending_sync_queue()

    assert len(provider._pending_sync_queue) == 1


# ---------------------------------------------------------------------------
# Terminal-mining fallbacks for changed_files and patch_summary
# ---------------------------------------------------------------------------

SAMPLE_DIFF = (
    "diff --git a/django/contrib/auth/validators.py b/django/contrib/auth/validators.py\n"
    "index abc123..def456 100644\n"
    "--- a/django/contrib/auth/validators.py\n"
    "+++ b/django/contrib/auth/validators.py\n"
    "@@ -9,7 +9,7 @@ class ASCIIUsernameValidator:\n"
    "-    regex = r'^[\\w.@+-]+$'\n"
    "+    regex = r'\\A[\\w.@+-]+\\Z'\n"
)


def test_extract_changed_files_from_terminal_parses_diff():
    provider = _make_provider()
    provider.record_terminal_call("git diff HEAD", SAMPLE_DIFF)

    files = provider._extract_changed_files_from_terminal()

    assert "django/contrib/auth/validators.py" in files


def test_extract_patch_summary_from_terminal_returns_diff():
    provider = _make_provider()
    provider.record_terminal_call("git diff HEAD", SAMPLE_DIFF)

    summary = provider._extract_patch_summary_from_terminal()

    assert summary.startswith("diff --git")
    assert "validators.py" in summary


def test_build_coding_summary_mines_changed_files_from_terminal():
    provider = _make_provider()
    provider.record_terminal_call("git diff HEAD", SAMPLE_DIFF)

    provider.sync_turn("user", "assistant", task_type="bugfix")

    cs = provider._memory_client.sync_calls[0]["coding_summary"]
    assert cs is not None
    assert "django/contrib/auth/validators.py" in cs.changed_files


def test_build_coding_summary_mines_patch_summary_from_terminal():
    provider = _make_provider()
    provider.record_terminal_call("git diff HEAD", SAMPLE_DIFF)

    provider.sync_turn("user", "assistant", task_type="bugfix")

    cs = provider._memory_client.sync_calls[0]["coding_summary"]
    assert cs is not None
    assert cs.patch_summary is not None
    assert "diff --git" in cs.patch_summary


def test_build_coding_summary_caller_kwargs_take_precedence():
    provider = _make_provider()
    provider.record_terminal_call("git diff HEAD", SAMPLE_DIFF)

    provider.sync_turn(
        "user", "assistant",
        changed_files=["explicit/file.py"],
        patch_summary="explicit summary",
    )

    cs = provider._memory_client.sync_calls[0]["coding_summary"]
    assert cs.changed_files == ["explicit/file.py"]
    assert cs.patch_summary == "explicit summary"


def test_build_summary_hint_includes_patch_diff():
    provider = _make_provider()
    provider.record_terminal_call("git diff HEAD", SAMPLE_DIFF)

    messages = [
        {"role": "user", "content": "fix the validator"},
        {"role": "assistant", "content": "I changed the regex to use \\A and \\Z anchors."},
    ]
    hint = provider._build_summary_hint(messages)

    assert "Patch applied:" in hint
    assert "diff --git" in hint
    assert "I changed the regex" in hint


def test_build_summary_hint_no_diff_falls_back_to_assistant_message():
    provider = _make_provider()

    messages = [
        {"role": "user", "content": "fix the validator"},
        {"role": "assistant", "content": "Done."},
    ]
    hint = provider._build_summary_hint(messages)

    assert hint == "Done."


def test_build_summary_hint_empty_messages_returns_empty():
    provider = _make_provider()
    assert provider._build_summary_hint([]) == ""
