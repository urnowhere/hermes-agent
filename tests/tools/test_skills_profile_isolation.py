import json
import textwrap

from hermes_constants import hermes_home_context


def _write_skill(home, name, description):
    skill_dir = home / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: {description}
            ---

            # {name}

            {description}
            """
        ),
        encoding="utf-8",
    )
    return skill_dir


def _simulate_import_under_gateway(monkeypatch, gateway_home):
    from tools import skill_manager_tool, skills_tool

    for module in (skills_tool, skill_manager_tool):
        monkeypatch.setattr(module, "HERMES_HOME", gateway_home, raising=False)
        monkeypatch.setattr(module, "SKILLS_DIR", gateway_home / "skills", raising=False)
        monkeypatch.setattr(
            module,
            "_IMPORT_SKILLS_DIR",
            gateway_home / "skills",
            raising=False,
        )


def test_skills_tools_resolve_active_profile_after_gateway_import(monkeypatch, tmp_path):
    from tools import skills_tool

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profiles" / "shopping"
    gateway_home.mkdir()
    profile_home.mkdir(parents=True)
    _write_skill(gateway_home, "gateway-only", "gateway skill")
    _write_skill(profile_home, "profile-only", "profile skill")
    _simulate_import_under_gateway(monkeypatch, gateway_home)

    with hermes_home_context(profile_home):
        assert skills_tool.get_skills_dir() == profile_home / "skills"
        listed = json.loads(skills_tool.skills_list())
        names = {item["name"] for item in listed["skills"]}
        assert names == {"profile-only"}

        viewed = json.loads(skills_tool.skill_view("profile-only"))
        assert viewed["success"] is True
        assert viewed["skill_dir"] == str(profile_home / "skills" / "profile-only")

        gateway_view = json.loads(skills_tool.skill_view("gateway-only"))
        assert gateway_view["success"] is False


def test_skill_manage_creates_under_active_profile_after_gateway_import(monkeypatch, tmp_path):
    from tools.skill_manager_tool import skill_manage

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profiles" / "shopping"
    gateway_home.mkdir()
    profile_home.mkdir(parents=True)
    _simulate_import_under_gateway(monkeypatch, gateway_home)

    content = textwrap.dedent(
        """\
        ---
        name: profile-created
        description: created in the routed profile
        ---

        # Profile Created
        """
    )

    with hermes_home_context(profile_home):
        result = json.loads(
            skill_manage(action="create", name="profile-created", content=content)
        )

    assert result["success"] is True
    assert (profile_home / "skills" / "profile-created" / "SKILL.md").exists()
    assert not (gateway_home / "skills" / "profile-created" / "SKILL.md").exists()


def test_skill_command_reload_scans_active_profile_after_gateway_import(monkeypatch, tmp_path):
    from agent import skill_commands

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profiles" / "shopping"
    gateway_home.mkdir()
    profile_home.mkdir(parents=True)
    _write_skill(gateway_home, "gateway-only", "gateway skill")
    _write_skill(profile_home, "profile-only", "profile skill")
    _simulate_import_under_gateway(monkeypatch, gateway_home)
    monkeypatch.setattr(skill_commands, "_skill_commands", {}, raising=False)
    monkeypatch.setattr(skill_commands, "_skill_commands_platform", None, raising=False)

    with hermes_home_context(profile_home):
        commands = skill_commands.scan_skill_commands()

    assert "/profile-only" in commands
    assert "/gateway-only" not in commands


def test_explicit_skills_dir_monkeypatch_still_wins(monkeypatch, tmp_path):
    from tools import skills_tool

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profiles" / "shopping"
    custom_skills = tmp_path / "custom-skills"
    gateway_home.mkdir()
    profile_home.mkdir(parents=True)
    custom_skill = custom_skills / "custom-only"
    custom_skill.mkdir(parents=True)
    (custom_skill / "SKILL.md").write_text(
        "---\nname: custom-only\ndescription: custom skill\n---\n\n# Custom\n",
        encoding="utf-8",
    )
    _simulate_import_under_gateway(monkeypatch, gateway_home)
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", custom_skills, raising=False)

    with hermes_home_context(profile_home):
        assert skills_tool.get_skills_dir() == custom_skills
        listed = json.loads(skills_tool.skills_list())

    assert {item["name"] for item in listed["skills"]} == {"custom-only"}
