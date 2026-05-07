from hermes_cli.config import DEFAULT_CONFIG
from run_agent import AIAgent


def _agent_with_immunity(enabled: bool) -> AIAgent:
    agent = AIAgent.__new__(AIAgent)
    agent._adaptive_immunity_enabled = enabled
    return agent


def test_adaptive_immunity_default_is_opt_in():
    assert DEFAULT_CONFIG["adaptive_immunity"]["enabled"] is False


def test_adaptive_immunity_addendum_appends_to_skill_review_only():
    agent = _agent_with_immunity(True)

    skill_prompt = agent._build_background_review_prompt(review_skills=True)
    assert skill_prompt.startswith(agent._SKILL_REVIEW_PROMPT)
    assert "Security addendum" in skill_prompt
    assert "security analyst" in skill_prompt
    assert "capability escalation" in skill_prompt
    assert "high-risk turns" in skill_prompt
    assert "Innocent framing is not decisive" in skill_prompt
    assert "category='security'" in skill_prompt
    assert "apply before tools" in skill_prompt
    assert "refuse matches" in skill_prompt
    assert "Redact indicators" in skill_prompt
    assert "store no secrets" in skill_prompt
    assert "abuse steps" in skill_prompt
    assert "Otherwise do nothing" in skill_prompt
    assert len(AIAgent._ADAPTIVE_IMMUNITY_REVIEW_ADDENDUM.split()) <= len(
        AIAgent._SKILL_REVIEW_PROMPT.split()
    )

    memory_prompt = agent._build_background_review_prompt(review_memory=True)
    assert memory_prompt == agent._MEMORY_REVIEW_PROMPT
    assert "Security addendum" not in memory_prompt


def test_adaptive_immunity_addendum_appends_to_combined_review():
    agent = _agent_with_immunity(True)

    prompt = agent._build_background_review_prompt(review_memory=True, review_skills=True)

    assert prompt.startswith(agent._COMBINED_REVIEW_PROMPT)
    assert "**Memory**" in prompt
    assert "**Skills**" in prompt
    assert "Security addendum" in prompt


def test_adaptive_immunity_addendum_can_be_disabled():
    agent = _agent_with_immunity(False)

    prompt = agent._build_background_review_prompt(review_skills=True)

    assert prompt == agent._SKILL_REVIEW_PROMPT
    assert "Security addendum" not in prompt
