"""Tests for project-local skill discovery (auto-discover from working directory).

Covers:
- get_project_skills_dirs() discovery from .hermes/skills/, .agents/skills/, .claude/skills/
- _find_project_root() git root detection and cwd fallback
- Integration with _find_all_skills() — source tagging, precedence
- Integration with skill_view() — project skills are viewable
- Integration with scan_skill_commands() — project skills become slash commands
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def project_root(tmp_path):
    """Create a fake project root with a .git directory."""
    root = tmp_path / "my-project"
    root.mkdir()
    (root / ".git").mkdir()
    return root


@pytest.fixture
def hermes_home(tmp_path):
    """Create a minimal HERMES_HOME with config."""
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "skills").mkdir()
    (home / "config.yaml").write_text("skills:\n  external_dirs: []\n")
    return home


def _create_project_skill(project_root, subdir, name, description):
    """Helper to create a skill in a project-local directory."""
    skill_dir = project_root / subdir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{description}\n"
    )
    return skill_dir


class TestFindProjectRoot:
    def test_finds_git_root(self, project_root):
        subdir = project_root / "src" / "deep" / "nested"
        subdir.mkdir(parents=True)
        with patch("os.getcwd", return_value=str(subdir)):
            from agent.skill_utils import _find_project_root
            result = _find_project_root()
        assert result == project_root.resolve()

    def test_cwd_fallback_when_no_git(self, tmp_path):
        no_git_dir = tmp_path / "no-git-project"
        no_git_dir.mkdir()
        with patch("os.getcwd", return_value=str(no_git_dir)):
            from agent.skill_utils import _find_project_root
            result = _find_project_root()
        assert result == no_git_dir.resolve()

    def test_returns_none_on_oserror(self):
        with patch("pathlib.Path.cwd", side_effect=OSError("no cwd")):
            from agent.skill_utils import _find_project_root
            result = _find_project_root()
        assert result is None


class TestGetProjectSkillsDirs:
    def test_discovers_hermes_skills(self, hermes_home, project_root):
        skills_dir = project_root / ".hermes" / "skills"
        skills_dir.mkdir(parents=True)
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_utils import get_project_skills_dirs
            result = get_project_skills_dirs()
        assert skills_dir.resolve() in [d.resolve() for d in result]

    def test_discovers_agents_skills(self, hermes_home, project_root):
        skills_dir = project_root / ".agents" / "skills"
        skills_dir.mkdir(parents=True)
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_utils import get_project_skills_dirs
            result = get_project_skills_dirs()
        assert skills_dir.resolve() in [d.resolve() for d in result]

    def test_discovers_claude_skills(self, hermes_home, project_root):
        skills_dir = project_root / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_utils import get_project_skills_dirs
            result = get_project_skills_dirs()
        assert skills_dir.resolve() in [d.resolve() for d in result]

    def test_discovers_multiple_dirs(self, hermes_home, project_root):
        (project_root / ".hermes" / "skills").mkdir(parents=True)
        (project_root / ".claude" / "skills").mkdir(parents=True)
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_utils import get_project_skills_dirs
            result = get_project_skills_dirs()
        assert len(result) == 2

    def test_nonexistent_dirs_skipped(self, hermes_home, project_root):
        # No skill subdirs created
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_utils import get_project_skills_dirs
            result = get_project_skills_dirs()
        assert result == []

    def test_local_skills_dir_excluded(self, hermes_home):
        """If project root IS hermes home, the skills dir should be excluded."""
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(hermes_home.parent)),
            patch("agent.skill_utils._find_project_root", return_value=hermes_home.parent),
        ):
            from agent.skill_utils import get_project_skills_dirs
            result = get_project_skills_dirs()
        # .hermes/skills in hermes_home should be excluded as it's the local skills dir
        for d in result:
            assert d.resolve() != (hermes_home / "skills").resolve()

    def test_deduplication(self, hermes_home, project_root):
        """Same resolved path shouldn't appear twice."""
        skills_dir = project_root / ".hermes" / "skills"
        skills_dir.mkdir(parents=True)
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_utils import get_project_skills_dirs
            result = get_project_skills_dirs()
        resolved = [d.resolve() for d in result]
        assert len(resolved) == len(set(resolved))


class TestGetAllSkillsDirsWithProject:
    def test_project_dirs_after_local_before_external(self, hermes_home, project_root, tmp_path):
        # Create project skill dir
        project_skills = project_root / ".claude" / "skills"
        project_skills.mkdir(parents=True)
        # Create external dir
        ext_dir = tmp_path / "external-skills"
        ext_dir.mkdir()
        (hermes_home / "config.yaml").write_text(
            f"skills:\n  external_dirs:\n    - {ext_dir}\n"
        )
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_utils import get_all_skills_dirs
            result = get_all_skills_dirs()
        # Local is first
        assert result[0] == hermes_home / "skills"
        # Project is second
        assert project_skills.resolve() in [d.resolve() for d in result[1:-1]]
        # External is last
        assert result[-1] == ext_dir.resolve()


class TestProjectSkillsInFindAll:
    def test_project_skills_found_with_source_tag(self, hermes_home, project_root):
        _create_project_skill(
            project_root, Path(".claude") / "skills", "langfuse-patterns",
            "Langfuse instrumentation patterns for this project"
        )
        local_skills = hermes_home / "skills"
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("tools.skills_tool.SKILLS_DIR", local_skills),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from tools.skills_tool import _find_all_skills
            skills = _find_all_skills()
        matching = [s for s in skills if s["name"] == "langfuse-patterns"]
        assert len(matching) == 1
        assert matching[0]["source"] == "project"

    def test_local_takes_precedence_over_project(self, hermes_home, project_root):
        """If the same skill name exists locally and in project, local wins."""
        # Create local skill
        local_skill = hermes_home / "skills" / "my-skill"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Local version\n---\n\nLocal.\n"
        )
        # Create project skill with same name
        _create_project_skill(
            project_root, Path(".claude") / "skills", "my-skill",
            "Project version"
        )
        local_skills = hermes_home / "skills"
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("tools.skills_tool.SKILLS_DIR", local_skills),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from tools.skills_tool import _find_all_skills
            skills = _find_all_skills()
        matching = [s for s in skills if s["name"] == "my-skill"]
        assert len(matching) == 1
        assert matching[0]["description"] == "Local version"
        assert matching[0]["source"] == "local"

    def test_project_takes_precedence_over_external(self, hermes_home, project_root, tmp_path):
        """If the same skill name exists in project and external, project wins."""
        # Create project skill
        _create_project_skill(
            project_root, Path(".agents") / "skills", "shared-skill",
            "Project version"
        )
        # Create external skill with same name
        ext_dir = tmp_path / "external-skills"
        ext_skill = ext_dir / "shared-skill"
        ext_skill.mkdir(parents=True)
        (ext_skill / "SKILL.md").write_text(
            "---\nname: shared-skill\ndescription: External version\n---\n\nExternal.\n"
        )
        (hermes_home / "config.yaml").write_text(
            f"skills:\n  external_dirs:\n    - {ext_dir}\n"
        )
        local_skills = hermes_home / "skills"
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("tools.skills_tool.SKILLS_DIR", local_skills),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from tools.skills_tool import _find_all_skills
            skills = _find_all_skills()
        matching = [s for s in skills if s["name"] == "shared-skill"]
        assert len(matching) == 1
        assert matching[0]["description"] == "Project version"
        assert matching[0]["source"] == "project"

    def test_local_skills_tagged_as_local(self, hermes_home, project_root):
        """Local skills should have source='local'."""
        local_skill = hermes_home / "skills" / "local-skill"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text(
            "---\nname: local-skill\ndescription: A local skill\n---\n\nLocal.\n"
        )
        local_skills = hermes_home / "skills"
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("tools.skills_tool.SKILLS_DIR", local_skills),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from tools.skills_tool import _find_all_skills
            skills = _find_all_skills()
        matching = [s for s in skills if s["name"] == "local-skill"]
        assert len(matching) == 1
        assert matching[0]["source"] == "local"


class TestProjectSkillView:
    def test_skill_view_finds_project_skill(self, hermes_home, project_root):
        _create_project_skill(
            project_root, Path(".claude") / "skills", "project-patterns",
            "Project-specific patterns"
        )
        local_skills = hermes_home / "skills"
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("tools.skills_tool.SKILLS_DIR", local_skills),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from tools.skills_tool import skill_view
            result = json.loads(skill_view("project-patterns"))
        assert result["success"] is True
        assert "Project-specific patterns" in result["content"]


class TestProjectSkillCommands:
    def test_scan_finds_project_skills(self, hermes_home, project_root):
        _create_project_skill(
            project_root, Path(".hermes") / "skills", "code-review",
            "Code review guidelines for this project"
        )
        local_skills = hermes_home / "skills"
        with (
            patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}),
            patch("tools.skills_tool.SKILLS_DIR", local_skills),
            patch("os.getcwd", return_value=str(project_root)),
        ):
            from agent.skill_commands import scan_skill_commands
            commands = scan_skill_commands()
        assert "/code-review" in commands
        assert commands["/code-review"]["name"] == "code-review"
