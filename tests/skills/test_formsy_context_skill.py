from pathlib import Path

import json

from tools.skills_tool import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, _parse_frontmatter, skills_list


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SKILL_PATH = (
    REPO_ROOT
    / "skills"
    / "software-development"
    / "formsy-context"
    / "SKILL.md"
)


def test_formsy_context_skill_frontmatter_is_loadable():
    content = CANONICAL_SKILL_PATH.read_text(encoding="utf-8")

    frontmatter, body = _parse_frontmatter(content)

    assert frontmatter["name"] == "formsy-context"
    assert len(frontmatter["name"]) <= MAX_NAME_LENGTH
    assert frontmatter["description"].startswith("Use when ")
    assert len(frontmatter["description"]) <= MAX_DESCRIPTION_LENGTH
    assert "context_search" in frontmatter["description"]
    assert "context_read" in frontmatter["description"]
    assert "Completion Verifier" in frontmatter["description"]
    assert body.strip().startswith("# FormSy Context")


def test_formsy_context_skill_keeps_trigger_and_workflow_separate():
    content = CANONICAL_SKILL_PATH.read_text(encoding="utf-8")

    frontmatter, body = _parse_frontmatter(content)
    description = frontmatter["description"].lower()

    assert "need_more_validation" not in description
    assert "git diff" not in description
    assert "seed with" not in description
    assert "follow context_read" not in description
    assert "Retrieval-First Workflow" in body
    assert "NEED_MORE_VALIDATION" in body
    assert "git diff --stat" in body


def test_formsy_context_skill_does_not_own_server_facts():
    content = CANONICAL_SKILL_PATH.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(content)

    forbidden_task_specific_terms = [
        "PlayIterator",
        "HostState",
        "lib/ansible/executor/play_iterator.py",
        "patch_now_threshold",
        "probe_budget",
    ]

    for term in forbidden_task_specific_terms:
        assert term not in body

    assert "Server guidance remains authoritative" in body
    assert "accepted targets" in body


def test_formsy_context_is_the_only_formsy_runtime_skill():
    data = json.loads(skills_list(category="software-development"))
    formsy_names = sorted(
        skill["name"]
        for skill in data["skills"]
        if "formsy" in skill["name"]
    )

    assert formsy_names == ["formsy-context"]
