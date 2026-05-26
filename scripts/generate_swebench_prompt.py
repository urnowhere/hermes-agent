#!/usr/bin/env python3
"""Generate SWE-bench Pro prompt for a given instance_id.

Usage:
    python generate_swebench_prompt.py --dataset <path_to_jsonl> --instance-id <id> --repo-dir <path_to_repo>
"""

import argparse
import json
import subprocess
import sys


PROMPT_TEMPLATE = '''You are a helpful assistant that can interact with a computer shell to solve programming tasks.

<pr_description>
Consider the following PR description:
{problem_statement}
</pr_description>


<instructions>
# Task Instructions
## Overview
You're a software engineer interacting continuously with a computer by submitting commands.
You'll be helping implement necessary changes to meet requirements in the PR description.
Your task is specifically to make changes to non-test files in the current directory in order to fix the issue described in the PR description in a way that is general and consistent with the codebase.
<IMPORTANT>This is an interactive process where you will think and issue AT LEAST ONE command, see the result, then think and issue your next command(s).</important>
For each response:
1. Include a THOUGHT section explaining your reasoning and what you're trying to accomplish
2. Provide one or more bash tool calls to execute
## Important Boundaries
- MODIFY: Regular source code files in /testbed (this is the working directory for all your subsequent commands)
- DO NOT MODIFY: Tests, configuration files (pyproject.toml, setup.cfg, etc.)
## Recommended Workflow
1. Explore the directory and filenames with bash
2. Query source-code context with context_search using relevant symbols, keywords, paths, or error text
3. Create a script to reproduce the issue
4. Edit the source code to resolve the issue
5. Verify your fix works by running your script again
6. Test edge cases to ensure your fix is robust

## Context Search Guidance
Use `context_search` proactively and repeatedly.

When using `context_search`, prefer passing the entire `<pr_description>` content as the `query` value instead of a shortened summary at first time. This preserves full issue context and usually improves retrieval quality.

## Command Execution Rules
You are operating in an environment where
1. You issue at least one command
2. The system executes the command(s) in a subshell
3. You see the result(s)
4. You write your next command(s)
Each response should include:
1. **Reasoning text** where you explain your analysis and plan
2. At least one tool call with your command
**CRITICAL REQUIREMENTS:**
- Your response SHOULD include reasoning text explaining what you're doing
- Your response MUST include AT LEAST ONE bash tool call.
- Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
- However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files
## Environment Details
- You have a full Linux shell environment
- Always use non-interactive flags (-y, -f) for commands
- Avoid interactive tools like vi, nano, or any that require user input
- You can use bash commands or invoke any tool that is available in the environment
- You can also create new tools or scripts to help you
- If a tool isn't available, you can also install it
## Submission
When you've completed your work, you MUST submit your changes as a git patch.
Follow these steps IN ORDER, with SEPARATE commands:
Step 1: Create the patch file
Run `git diff -- path/to/file1 path/to/file2 > patch.txt` listing only the source files you modified.
Do NOT commit your changes.
<IMPORTANT>
The patch must only contain changes to the specific source files you modified to fix the issue.
Do NOT submit file creations or changes to any of the following files:
- test and reproduction files
- helper scripts, tests, or tools that you created
- installation, build, packaging, configuration, or setup scripts unless they are directly part of the issue you are fixing (you can assume that the environment is already set up for your client)
- binary or compiled files
</IMPORTANT>
Step 2: Verify your patch
Inspect patch.txt to confirm it only contains your intended changes and headers show `--- a/` and `+++ b/` paths.
Step 3: Submit (EXACT command required)
You MUST use this EXACT command to submit:
```bash
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt
```
If the command fails (nonzero exit status), it will not submit.
<CRITICAL>
- Creating/viewing the patch and submitting it MUST be separate commands (not combined with &&).
- If you modify patch.txt after verifying, you SHOULD verify again before submitting.
- You CANNOT continue working (reading, editing, testing) in any way on this task after submitting.
</CRITICAL>
</instructions>"'''


def load_instance(dataset_path: str, instance_id: str) -> dict:
    """Load a specific instance from the JSONL dataset."""
    with open(dataset_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("instance_id") == instance_id:
                return record
    print(f"Error: instance_id '{instance_id}' not found in {dataset_path}", file=sys.stderr)
    sys.exit(1)


def reset_repo(repo_dir: str, base_commit: str) -> None:
    """Reset the repo to base_commit, discarding all local changes."""
    print(f"Resetting {repo_dir} to {base_commit}...")
    subprocess.run(["git", "reset", "--hard", base_commit], cwd=repo_dir, check=True)
    subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, check=True)
    print("Repo cleaned.")


def build_prompt(problem_statement: str) -> str:
    """Fill the prompt template with the problem statement."""
    return PROMPT_TEMPLATE.format(problem_statement=problem_statement)


def main():
    parser = argparse.ArgumentParser(description="Generate SWE-bench Pro prompt for a given instance")
    parser.add_argument("--dataset", required=True, help="Path to the SWE-bench Pro JSONL dataset")
    parser.add_argument("--instance-id", required=True, help="The instance_id to look up")
    parser.add_argument("--repo-dir", required=True, help="Path to the repo directory to reset")
    args = parser.parse_args()

    # 1. Load instance from dataset
    instance = load_instance(args.dataset, args.instance_id)
    base_commit = instance["base_commit"]
    problem_statement = instance["problem_statement"]

    # 2. Reset the repo to base_commit
    reset_repo(args.repo_dir, base_commit)

    # 3. Build and print the prompt
    prompt = build_prompt(problem_statement)
    print(prompt)


if __name__ == "__main__":
    main()
