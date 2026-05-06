"""Structured task-envelope extraction for Hermes route decisions.

This module is intentionally deterministic and dependency-light. It gives the
router a compact work-shape description before ROI scoring, without pulling more
capability into the Feishu foreground prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class TaskEnvelope:
    """A compact structured interpretation of one inbound task."""

    intent_summary: str
    work_units: tuple[str, ...]
    artifact_targets: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    evidence_required: bool
    estimated_duration_class: str
    risk_class: str
    external_side_effects: bool
    dependencies: tuple[str, ...]
    ambiguities: tuple[str, ...]
    foreground_only: bool
    matched_signals: tuple[str, ...]


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().replace("_", " ").replace("-", " ").split())


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    return any(needle and needle in text for needle in needles)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)


_FOREGROUND_ONLY_MARKERS = (
    "轻量判断", "只判断", "不用查文件", "不查文件", "不用读文件", "不读文件",
    "不用看代码", "不看代码", "quick take", "quick judgment",
)
_SELF_GOVERNANCE_SUBJECTS = (
    "hermes", "lira", "route", "routing", "router", "dispatcher", "wrapper", "worker", "lane",
    "wake manifest", "background wakeups", "receipt", "session lifecycle", "memory governance",
    "自我治理", "路由", "分派", "分诊", "调度", "协议",
)
_CODEBASE_SIGNALS = (
    "代码库", "codebase", "repo", "repository", "github", ".py", "gateway/", "docs/protocols",
    "protocol", "protocols", "validator", "script", "scripts", "patch", "diff", "测试", "test", "tests",
    "cron", "skill", "skills", "state", "多文件", "文件", "代码",
)
_RESEARCH_SIGNALS = (
    "开源社区", "社区先进", "先进案例", "最佳实践", "best practice", "best practices",
    "benchmark", "benchmarks", "oss", "open source", "公开资料", "来源", "资料", "research",
)
_ORCHESTRATION_SIGNALS = (
    "并行", "多 agent", "多agent", "多个 agent", "多个worker", "多个 worker", "multi agent",
    "multi-agent", "orchestration", "分头", "拆给", "体系化", "完整", "全面", "提升计划",
)
_EVIDENCE_SIGNALS = (
    "审查", "检查", "验证", "来源", "公开资料", "阅读", "benchmark", "证据", "receipts",
    "diff", "test", "tests", "测试", "audit",
)
_INTERNAL_WRITE_SIGNALS = (
    "patch", "diff", "修改", "修", "补", "实现", "落实", "制定", "写入", "创建", "新增",
    "提升计划", "机制设计", "validator", "cron", "protocol",
)
_EXTERNAL_WRITE_SIGNALS = (
    "发送", "发到", "发给", "发布", "公开", "分享链接", "共享", "外部群", "邮件", "发邮件",
    "send", "publish", "post", "share", "permission", "permissions",
)
_DESTRUCTIVE_SIGNALS = (
    "删除", "移除", "清空", "销毁", "delete", "remove", "drop", "destroy", "rm -rf",
)
_DOC_SIGNALS = ("文档", "飞书文档", "云文档", "google doc", "google docs", "doc", "memo")
_PPT_SIGNALS = ("ppt", "pptx", "deck", "slides", "presentation", "幻灯片", "演示文稿")
_AUTOMATION_SIGNALS = ("cron", "自动化", "定时", "schedule", "scheduled", "每天", "每周", "每月")


def infer_task_envelope(prompt: str) -> TaskEnvelope:
    """Infer a deterministic task envelope from raw user text."""

    normalized = _normalize(prompt)
    signals: list[str] = []
    capabilities: list[str] = []
    artifacts: list[str] = []
    work_units: list[str] = []
    dependencies: list[str] = []
    ambiguities: list[str] = []

    foreground_only = _contains_any(normalized, _FOREGROUND_ONLY_MARKERS)
    self_governance = _contains_any(normalized, _SELF_GOVERNANCE_SUBJECTS)
    codebase = _contains_any(normalized, _CODEBASE_SIGNALS) or self_governance and _contains_any(normalized, _INTERNAL_WRITE_SIGNALS)
    research = _contains_any(normalized, _RESEARCH_SIGNALS)
    automation = _contains_any(normalized, _AUTOMATION_SIGNALS)
    doc = _contains_any(normalized, _DOC_SIGNALS)
    ppt = _contains_any(normalized, _PPT_SIGNALS)

    if foreground_only:
        signals.append("foreground_only")
    if self_governance:
        signals.append("self_governance")
    if codebase and not foreground_only:
        capabilities.append("codebase_inspection")
        work_units.append("repo")
        signals.append("codebase")
    if research and not foreground_only:
        capabilities.append("external_research")
        work_units.append("research")
        signals.append("research")
    if automation and not foreground_only:
        capabilities.append("automation")
        work_units.append("automation")
        signals.append("automation")
    if doc and not foreground_only:
        capabilities.append("document_generation")
        work_units.append("document")
        artifacts.append("document")
    if ppt and not foreground_only:
        capabilities.append("presentation_generation")
        work_units.append("presentation")
        artifacts.append("presentation")

    if (
        _contains_any(normalized, _ORCHESTRATION_SIGNALS)
        or len({item for item in work_units if item in {"repo", "research", "automation", "document", "presentation"}}) > 1
    ) and not foreground_only:
        capabilities.append("orchestration")
        signals.append("orchestration")

    if self_governance and (codebase or research) and not foreground_only:
        artifacts.append("route_mechanism_plan")
    if "patch" in normalized or "diff" in normalized:
        artifacts.append("patch")
    if research and codebase and not foreground_only:
        dependencies.append("synthesize_repo_and_research_outputs")

    evidence_required = bool(_contains_any(normalized, _EVIDENCE_SIGNALS) and not foreground_only)
    external_side_effects = _contains_any(normalized, _EXTERNAL_WRITE_SIGNALS)
    destructive = _contains_any(normalized, _DESTRUCTIVE_SIGNALS)
    internal_write = _contains_any(normalized, _INTERNAL_WRITE_SIGNALS) or bool(artifacts)

    if destructive:
        risk_class = "destructive"
    elif external_side_effects:
        risk_class = "external_write"
    elif internal_write and not foreground_only:
        risk_class = "internal_write"
    else:
        risk_class = "read_only"

    if not normalized.strip():
        ambiguities.append("empty_prompt")
    if not foreground_only and not work_units and not external_side_effects:
        ambiguities.append("no_route_signals")

    if foreground_only:
        duration = "tiny"
        capabilities = []
        work_units = []
        artifacts = []
        dependencies = []
        evidence_required = False
    elif _contains_any(normalized, ("完整", "全面", "深入", "体系化", "提升计划", "多文件", "end to end", "deep dive")):
        duration = "long"
    elif len(work_units) > 1 or evidence_required:
        duration = "medium"
    elif work_units:
        duration = "short"
    else:
        duration = "tiny"

    summary = " ".join(str(prompt or "").strip().split())[:160]
    return TaskEnvelope(
        intent_summary=summary,
        work_units=_dedupe(work_units),
        artifact_targets=_dedupe(artifacts),
        required_capabilities=_dedupe(capabilities),
        evidence_required=evidence_required,
        estimated_duration_class=duration,
        risk_class=risk_class,
        external_side_effects=external_side_effects,
        dependencies=_dedupe(dependencies),
        ambiguities=_dedupe(ambiguities),
        foreground_only=foreground_only,
        matched_signals=_dedupe(signals),
    )
