"""Security utilities for skill loading and validation.

Provides path traversal protection and input validation for skill names
to prevent security vulnerabilities like V-011 (Skills Guard Bypass).
"""

import re
from pathlib import Path
from typing import Optional, Tuple

# Strict skill name validation: alphanumeric, hyphens, underscores only
# This prevents path traversal attacks via skill names like "../../../etc/passwd"
VALID_SKILL_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9._-]+$')

# Maximum skill name length to prevent other attack vectors
MAX_SKILL_NAME_LENGTH = 256

# Suspicious patterns that indicate path traversal attempts
PATH_TRAVERSAL_PATTERNS = [
    "..",           # Parent directory reference
    "~",            # Home directory expansion
    "/",            # Absolute path (Unix)
    "\\",           # Windows path separator
    "//",           # Protocol-relative or UNC path
    "file:",        # File protocol
    "ftp:",         # FTP protocol
    "http:",        # HTTP protocol
    "https:",       # HTTPS protocol
    "data:",        # Data URI
    "javascript:",  # JavaScript protocol
    "vbscript:",    # VBScript protocol
]

# Characters that should never appear in skill names
INVALID_CHARACTERS = set([
    '\x00', '\x01', '\x02', '\x03', '\x04', '\x05', '\x06', '\x07',
    '\x08', '\x09', '\x0a', '\x0b', '\x0c', '\x0d', '\x0e', '\x0f',
    '\x10', '\x11', '\x12', '\x13', '\x14', '\x15', '\x16', '\x17',
    '\x18', '\x19', '\x1a', '\x1b', '\x1c', '\x1d', '\x1e', '\x1f',
    '<', '>', '|', '&', ';', '$', '`', '"', "'",
])


class SkillSecurityError(Exception):
    """Raised when a skill name fails security validation."""
    pass


class PathTraversalError(SkillSecurityError):
    """Raised when path traversal is detected in a skill name."""
    pass


class InvalidSkillNameError(SkillSecurityError):
    """Raised when a skill name contains invalid characters."""
    pass


def validate_skill_name(name: str, allow_path_separator: bool = False) -> None:
    """Validate a skill name for security issues.

    Args:
        name: The skill name or identifier to validate
        allow_path_separator: If True, allows '/' for category/skill paths (e.g., "mlops/axolotl")

    Raises:
        PathTraversalError: If path traversal patterns are detected
        InvalidSkillNameError: If the name contains invalid characters
        SkillSecurityError: For other security violations
    """
    if not name or not isinstance(name, str):
        raise InvalidSkillNameError("Skill name must be a non-empty string")

    if len(name) > MAX_SKILL_NAME_LENGTH:
        raise InvalidSkillNameError(
            f"Skill name exceeds maximum length of {MAX_SKILL_NAME_LENGTH} characters"
        )

    # Check for null bytes and other control characters
    for char in name:
        if char in INVALID_CHARACTERS:
            raise InvalidSkillNameError(
                f"Skill name contains invalid character: {repr(char)}"
            )

    # Validate against allowed character pattern first
    pattern = r'^[a-zA-Z0-9._-]+$' if not allow_path_separator else r'^[a-zA-Z0-9._/-]+$'
    if not re.match(pattern, name):
        invalid_chars = set(c for c in name if not re.match(r'[a-zA-Z0-9._/-]', c))
        raise InvalidSkillNameError(
            f"Skill name contains invalid characters: {sorted(invalid_chars)}. "
            "Only alphanumeric characters, hyphens, underscores, dots, "
            f"{'and forward slashes ' if allow_path_separator else ''}are allowed."
        )

    # Check for path traversal patterns (excluding '/' when path separators are allowed)
    name_lower = name.lower()
    patterns_to_check = PATH_TRAVERSAL_PATTERNS.copy()
    if allow_path_separator:
        # Remove '/' from patterns when path separators are allowed
        patterns_to_check = [p for p in patterns_to_check if p != '/']

    for pattern in patterns_to_check:
        if pattern in name_lower:
            raise PathTraversalError(
                f"Path traversal detected in skill name: '{pattern}' is not allowed"
            )


def resolve_skill_path(
    skill_name: str,
    skills_base_dir: Path,
    allow_path_separator: bool = True
) -> Tuple[Path, Optional[str]]:
    """Safely resolve a skill name to a path within the skills directory.

    Args:
        skill_name: The skill name or path (e.g., "axolotl" or "mlops/axolotl")
        skills_base_dir: The base skills directory
        allow_path_separator: Whether to allow '/' in skill names for categories

    Returns:
        Tuple of (resolved_path, error_message)
        - If successful: (resolved_path, None)
        - If failed: (skills_base_dir, error_message)

    Raises:
        PathTraversalError: If the resolved path would escape the skills directory
    """
    try:
        validate_skill_name(skill_name, allow_path_separator=allow_path_separator)
    except SkillSecurityError as e:
        return skills_base_dir, str(e)

    # Build the target path
    try:
        target_path = (skills_base_dir / skill_name).resolve()
    except (OSError, ValueError) as e:
        return skills_base_dir, f"Invalid skill path: {e}"

    # Ensure the resolved path is within the skills directory
    try:
        target_path.relative_to(skills_base_dir.resolve())
    except ValueError:
        raise PathTraversalError(
            f"Skill path '{skill_name}' resolves outside the skills directory boundary"
        )

    return target_path, None


def sanitize_skill_identifier(identifier: str) -> str:
    """Sanitize a skill identifier by removing dangerous characters.

    This is a defensive fallback for cases where strict validation
    cannot be applied. It removes or replaces dangerous characters.

    Args:
        identifier: The raw skill identifier

    Returns:
        A sanitized version of the identifier
    """
    if not identifier:
        return ""

    # Replace path traversal sequences
    sanitized = identifier.replace("..", "")
    sanitized = sanitized.replace("//", "/")

    # Remove home directory expansion
    if sanitized.startswith("~"):
        sanitized = sanitized[1:]

    # Remove protocol handlers
    for protocol in ["file:", "ftp:", "http:", "https:", "data:", "javascript:", "vbscript:"]:
        sanitized = sanitized.replace(protocol, "")
        sanitized = sanitized.replace(protocol.upper(), "")

    # Remove null bytes and control characters
    for char in INVALID_CHARACTERS:
        sanitized = sanitized.replace(char, "")

    # Normalize path separators to forward slash
    sanitized = sanitized.replace("\\", "/")

    # Remove leading/trailing slashes and whitespace
    sanitized = sanitized.strip("/ ").strip()

    return sanitized


def is_safe_skill_path(path: Path, allowed_base_dirs: list[Path]) -> bool:
    """Check if a path is safely within allowed directories.

    Args:
        path: The path to check
        allowed_base_dirs: List of allowed base directories

    Returns:
        True if the path is within allowed boundaries, False otherwise
    """
    try:
        resolved = path.resolve()
        for base_dir in allowed_base_dirs:
            try:
                resolved.relative_to(base_dir.resolve())
                return True
            except ValueError:
                continue
        return False
    except (OSError, ValueError):
        return False
