"""P1 route envelope and ROI decision tests."""

from pathlib import Path

import pytest

import gateway.route_decision as route_decision
import gateway.route_envelope as route_envelope

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _module in (route_decision, route_envelope):
    _module_path = Path(_module.__file__).resolve()
    assert _module_path.is_relative_to(_REPO_ROOT), (
        f"{_module.__name__} resolved outside clean worktree: {_module_path}"
    )

from gateway.background_wakeups import clear_background_wake_manifest_cache, resolve_background_wakeup  # noqa: E402
from gateway.route_decision import (  # noqa: E402
    build_feishu_route_decision_shadow_hint,
    resolve_route_decision,
    should_auto_dispatch_feishu,
)
from gateway.route_envelope import infer_task_envelope  # noqa: E402


ROUTE_AUDIT_PROMPT = "请体系化审查和制定route机制提升计划，阅读开源社区先进案例、Hermes本身机制"
EXTERNAL_WRITE_PROMPT = "帮我把这份报告发布到外部群，并公开分享链接"


@pytest.fixture(autouse=True)
def isolated_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_background_wake_manifest_cache()
    yield
    clear_background_wake_manifest_cache()


def test_task_envelope_extracts_self_governance_repo_research_mix():
    envelope = infer_task_envelope(ROUTE_AUDIT_PROMPT)

    assert envelope.intent_summary
    assert envelope.risk_class == "internal_write"
    assert envelope.estimated_duration_class in {"medium", "long"}
    assert envelope.evidence_required is True
    assert envelope.foreground_only is False
    assert "codebase_inspection" in envelope.required_capabilities
    assert "external_research" in envelope.required_capabilities
    assert "orchestration" in envelope.required_capabilities
    assert "route_mechanism_plan" in envelope.artifact_targets


def test_task_envelope_keeps_explicit_light_judgment_foreground_only():
    envelope = infer_task_envelope("这件事轻量判断一下，不用查文件")

    assert envelope.foreground_only is True
    assert envelope.risk_class == "read_only"
    assert envelope.required_capabilities == ()
    assert envelope.estimated_duration_class == "tiny"


def test_route_decision_shadow_auto_dispatches_safe_internal_mixed_work():
    plan = resolve_background_wakeup(
        ROUTE_AUDIT_PROMPT,
        platform="feishu",
        default_toolsets=["hermes-feishu-work"],
    )

    decision = resolve_route_decision(
        ROUTE_AUDIT_PROMPT,
        platform="feishu",
        active_toolsets=("terminal", "file", "skills", "session_search", "memory", "todo", "clarify"),
        wake_plan=plan,
    )

    assert decision.shadow_mode is True
    assert decision.decision_type == "auto_dispatch"
    assert should_auto_dispatch_feishu(decision) is True
    assert decision.risk_class == "internal_write"
    assert decision.confidence >= 0.75
    assert decision.score.total >= decision.auto_dispatch_threshold
    assert decision.forced_routes == ("research", "repo", "multi_agent")
    assert decision.wrapper_commands == ("/research", "/repo", "/bg")
    assert decision.score.components["quality_gain"] > 0
    assert decision.score.components["parallelism_gain"] > 0

    hint = build_feishu_route_decision_shadow_hint(decision)
    assert "RouteDecision shadow" in hint
    assert "auto_dispatch" in hint
    assert "/repo" in hint
    assert "/research" in hint
    assert "/bg" in hint


def test_route_decision_blocks_external_write_auto_dispatch():
    decision = resolve_route_decision(
        EXTERNAL_WRITE_PROMPT,
        platform="feishu",
        active_toolsets=("terminal", "file", "skills", "session_search", "memory", "todo", "clarify"),
    )

    assert decision.decision_type == "approval_required"
    assert decision.risk_class == "external_write"
    assert should_auto_dispatch_feishu(decision) is False
    assert decision.forced_routes == ()
