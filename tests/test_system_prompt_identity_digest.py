import json

from hermes_constants import hermes_home_context


def test_system_prompt_identity_digest_changes_when_soul_changes(tmp_path):
    from run_agent import AIAgent

    hermes_home = tmp_path / "profile"
    hermes_home.mkdir()

    with hermes_home_context(hermes_home):
        missing = AIAgent._system_prompt_identity_digest()
        (hermes_home / "SOUL.md").write_text("Identity v1", encoding="utf-8")
        v1 = AIAgent._system_prompt_identity_digest()
        (hermes_home / "SOUL.md").write_text("Identity v2", encoding="utf-8")
        v2 = AIAgent._system_prompt_identity_digest()

    assert missing != v1
    assert v1 != v2


def test_stored_system_prompt_identity_requires_matching_digest(tmp_path):
    from run_agent import AIAgent

    hermes_home = tmp_path / "profile"
    hermes_home.mkdir()
    (hermes_home / "SOUL.md").write_text("Identity v1", encoding="utf-8")

    with hermes_home_context(hermes_home):
        agent = object.__new__(AIAgent)
        agent._system_prompt_identity_fingerprint = AIAgent._system_prompt_identity_digest()
        matching = {
            "model_config": json.dumps(
                {"system_prompt_identity_digest": agent._system_prompt_identity_fingerprint}
            )
        }
        stale = {
            "model_config": json.dumps({"system_prompt_identity_digest": "stale-digest"})
        }

        assert agent._stored_system_prompt_identity_matches(matching) is True
        assert agent._stored_system_prompt_identity_matches(stale) is False
        assert agent._stored_system_prompt_identity_matches({"model_config": "{}"}) is False
