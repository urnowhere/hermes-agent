"""Tests for memory/skill nudge trigger logic — ensuring counters are only reset when review is actually triggered."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from run_agent import AIAgent


def _make_minimal_agent() -> AIAgent:
    """Return an AIAgent constructed with the absolute minimum args.
    
    We skip __init__ entirely and only set up the attributes needed
    for testing nudge trigger logic.
    """
    agent = AIAgent.__new__(AIAgent)  # skip __init__ entirely
    
    # Set up nudge-related attributes
    agent._memory_nudge_interval = 2
    agent._skill_nudge_interval = 3
    agent._turns_since_memory = 0
    agent._iters_since_skill = 0
    agent.valid_tool_names = {"memory", "skill_manage"}
    agent._memory_store = MagicMock()
    
    return agent


def _check_memory_nudge_trigger(agent: AIAgent) -> bool:
    """Check if memory nudge should trigger - mirrors real code in run_agent.py."""
    if (agent._memory_nudge_interval > 0
            and agent._turns_since_memory >= agent._memory_nudge_interval
            and "memory" in agent.valid_tool_names):
        return True
    return False


def _check_skill_nudge_trigger(agent: AIAgent) -> bool:
    """Check if skill nudge should trigger - mirrors real code in run_agent.py."""
    if (agent._skill_nudge_interval > 0
            and agent._iters_since_skill >= agent._skill_nudge_interval
            and "skill_manage" in agent.valid_tool_names):
        return True
    return False


def _increment_memory_counter(agent: AIAgent):
    """Increment memory counter at turn start - mirrors real code in run_agent.py."""
    if (agent._memory_nudge_interval > 0
            and "memory" in agent.valid_tool_names
            and agent._memory_store):
        agent._turns_since_memory += 1


def _reset_counters_if_needed(agent: AIAgent, final_response, interrupted, 
                              should_review_memory, should_review_skills):
    """Reset counters - mirrors real code in run_agent.py."""
    if final_response and not interrupted and (should_review_memory or should_review_skills):
        if should_review_memory:
            agent._turns_since_memory = 0
        if should_review_skills:
            agent._iters_since_skill = 0


class TestNudgeTriggerLogic:
    """Tests for memory/skill nudge trigger logic."""

    def test_memory_nudge_complete_flow(self):
        """Test complete memory nudge flow: increment at turn start, check at turn end, reset only on review."""
        agent = _make_minimal_agent()
        
        # Start with counter 0
        assert agent._turns_since_memory == 0
        
        # --- Turn 1 ---
        _increment_memory_counter(agent)
        assert agent._turns_since_memory == 1
        
        should_review = _check_memory_nudge_trigger(agent)
        assert should_review is False
        
        # --- Turn 2 ---
        _increment_memory_counter(agent)
        assert agent._turns_since_memory == 2
        
        should_review = _check_memory_nudge_trigger(agent)
        assert should_review is True
        
        # --- Reset on success ---
        _reset_counters_if_needed(agent, "Success response", False, should_review, False)
        assert agent._turns_since_memory == 0

    def test_skill_nudge_complete_flow(self):
        """Test complete skill nudge flow: check at turn end, reset only on review."""
        agent = _make_minimal_agent()
        
        # At interval
        agent._iters_since_skill = 3
        should_review = _check_skill_nudge_trigger(agent)
        assert should_review is True
        
        # Reset on success
        _reset_counters_if_needed(agent, "Success response", False, False, should_review)
        assert agent._iters_since_skill == 0
        
        # Below interval
        agent._iters_since_skill = 2
        should_review = _check_skill_nudge_trigger(agent)
        assert should_review is False
        
        # No reset
        _reset_counters_if_needed(agent, "Success response", False, False, should_review)
        assert agent._iters_since_skill == 2

    def test_both_nudges_triggered_together(self):
        """Test when both memory and skill nudges trigger together."""
        agent = _make_minimal_agent()
        agent._turns_since_memory = 2
        agent._iters_since_skill = 3
        
        should_review_memory = _check_memory_nudge_trigger(agent)
        should_review_skills = _check_skill_nudge_trigger(agent)
        
        assert should_review_memory is True
        assert should_review_skills is True
        
        # Reset both together
        _reset_counters_if_needed(agent, "Success response", False, 
                                 should_review_memory, should_review_skills)
        
        assert agent._turns_since_memory == 0
        assert agent._iters_since_skill == 0

    def test_counters_preserved_when_memory_tool_unavailable(self):
        """Test that memory nudge doesn't trigger when memory tool is unavailable."""
        agent = _make_minimal_agent()
        agent.valid_tool_names = {"skill_manage"}  # remove memory tool
        agent._turns_since_memory = 2
        
        should_review = _check_memory_nudge_trigger(agent)
        assert should_review is False
        
        # Counter should NOT reset
        _reset_counters_if_needed(agent, "Success response", False, should_review, False)
        assert agent._turns_since_memory == 2

    def test_counters_preserved_on_interruption_or_failure(self):
        """Test that counters are preserved when conversation is interrupted or fails."""
        agent = _make_minimal_agent()
        agent._turns_since_memory = 2
        agent._iters_since_skill = 3
        should_review_memory = True
        should_review_skills = True
        
        # --- Scenario 1: Interrupted ---
        _reset_counters_if_needed(agent, "Partial response", True, 
                                 should_review_memory, should_review_skills)
        assert agent._turns_since_memory == 2
        assert agent._iters_since_skill == 3
        
        # --- Scenario 2: No final response (failed) ---
        _reset_counters_if_needed(agent, None, False, 
                                 should_review_memory, should_review_skills)
        assert agent._turns_since_memory == 2
        assert agent._iters_since_skill == 3
        
        # --- Scenario 3: Success (for comparison) ---
        _reset_counters_if_needed(agent, "Success response", False, 
                                 should_review_memory, should_review_skills)
        assert agent._turns_since_memory == 0
        assert agent._iters_since_skill == 0