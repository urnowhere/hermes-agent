"""Message conversion utilities for the Formsy context engine."""

from typing import Optional
from plugins.formsy.models import CompileBundle


def convert_compile_bundle_to_messages(bundle: CompileBundle) -> list[dict]:
    """Convert CompileBundle to OpenAI-style messages."""
    messages = []

    # Add compiled messages directly
    for msg in bundle.compiled_messages:
        messages.append({
            "role": msg.role,
            "content": msg.content,
        })

    # Optionally add advisory as system message
    if bundle.advisory and bundle.advisory.recommended_action:
        advisory_text = f"[ADVISORY] {bundle.advisory.rationale_tail}"
        messages.append({
            "role": "system",
            "content": advisory_text,
        })

    return messages


def detect_scene(context: dict) -> str:
    """Detect the scene from context hints."""
    if context.get("repo_id") or context.get("file_path"):
        return "coding"
    elif context.get("document_id"):
        return "vision_doc"
    return "auto"


def extract_task(messages: list[dict]) -> dict:
    """Extract task information from messages."""
    instruction = ""
    task_type = "general"

    # Get the last user message as the instruction
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                instruction = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        instruction = part.get("text", "")
                        break
            break

    # Simple heuristics for task type
    instruction_lower = instruction.lower()
    if any(word in instruction_lower for word in ["fix", "bug", "error", "patch"]):
        task_type = "bugfix"
    elif any(word in instruction_lower for word in ["add", "implement", "create", "build"]):
        task_type = "patch_generation"
    elif any(word in instruction_lower for word in ["refactor", "clean", "improve"]):
        task_type = "patch_generation"

    return {
        "instruction": instruction[:500],  # Truncate for safety
        "task_type": task_type,
    }
