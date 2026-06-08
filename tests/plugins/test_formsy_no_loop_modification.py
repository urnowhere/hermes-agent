from __future__ import annotations

import subprocess


def test_constraint_keeper_feature_does_not_modify_agent_loop_files():
    forbidden = ["run_agent.py", "model_tools.py", "agent/tool_guardrails.py"]
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--"] + forbidden,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()

    assert changed == []
