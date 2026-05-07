"""Common utilities for FormalCC Hermes Plugin."""

import uuid
from typing import Optional


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return str(uuid.uuid4())


def generate_turn_id(session_id: str, turn_number: int) -> str:
    """Generate a turn ID."""
    return f"{session_id}_turn_{turn_number:04d}"


def validate_workspace_id(workspace_id: Optional[str]) -> str:
    """Validate and return workspace ID."""
    if not workspace_id:
        return "ws_default"
    return workspace_id


def validate_tenant_id(tenant_id: Optional[str]) -> Optional[str]:
    """Validate and return tenant ID."""
    return tenant_id
