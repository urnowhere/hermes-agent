"""
Hermes Chat UI -- HTTP helper functions (adapted from Hermes WebUI).
"""
import json as _json
import re as _re
from pathlib import Path


def require(body: dict, *fields) -> None:
    """Validate required fields. Raises ValueError with clean message."""
    missing = [f for f in fields if not body.get(f) and body.get(f) != 0]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")


def bad(msg, status: int=400) -> dict:
    """Return a clean JSON error response."""
    return {'error': msg, 'status': status}


def _sanitize_error(e: Exception) -> str:
    """Strip filesystem paths from exception messages before returning to client."""
    import re
    msg = str(e)
    msg = re.sub(r'(?:(?:/[a-zA-Z0-9_.-]+)+|(?:[A-Z]:\\[^\s]+))', '<path>', msg)
    return msg


def safe_resolve(root: Path, requested: str) -> Path:
    """Resolve a relative path inside root, raising ValueError on traversal."""
    resolved = (root / requested).resolve()
    resolved.relative_to(root.resolve())
    return resolved


# Image and markdown extensions (common set)
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'}
MD_EXTS = {'.md', '.markdown', '.mdown', '.mkd'}

MIME_MAP = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    '.bmp': 'image/bmp',
    '.ico': 'image/x-icon',
    '.pdf': 'application/pdf',
    '.md': 'text/markdown; charset=utf-8',
    '.markdown': 'text/markdown; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.json': 'application/json',
    '.yaml': 'text/yaml; charset=utf-8',
    '.yml': 'text/yaml; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript',
}

MAX_BODY_BYTES = 20 * 1024 * 1024


# Credential redaction
def _build_redact_fn():
    """Return redact_sensitive_text from hermes-agent if available."""
    try:
        from agent.redact import redact_sensitive_text
        return redact_sensitive_text
    except ImportError:
        pass

    _CRED_RE = _re.compile(
        r"(?<![A-Za-z0-9_-])("
        r"sk-[A-Za-z0-9_-]{10,}"
        r"|ghp_[A-Za-z0-9]{10,}"
        r"|github_pat_[A-Za-z0-9_]{10,}"
        r"|AKIA[A-Z0-9]{16}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}"
        r")(?![A-Za-z0-9_-])"
    )

    def _mask(token: str) -> str:
        return f"{token[:6]}...{token[-4:]}" if len(token) >= 18 else "***"

    def _fallback_redact(text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        text = _CRED_RE.sub(lambda m: _mask(m.group(1)), text)
        return text

    return _fallback_redact


_redact_text = _build_redact_fn()


def _redact_value(v):
    """Recursively redact credentials from strings, dicts, and lists."""
    if isinstance(v, str):
        return _redact_text(v)
    if isinstance(v, dict):
        return {k: _redact_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_redact_value(item) for item in v]
    return v


def redact_session_data(session_dict: dict) -> dict:
    """Redact credentials from session data."""
    result = dict(session_dict)
    if isinstance(result.get('title'), str):
        result['title'] = _redact_text(result['title'])
    if 'messages' in result:
        result['messages'] = _redact_value(result['messages'])
    return result


async def read_body(request) -> dict:
    """Read and JSON-parse a POST request body."""
    try:
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise ValueError(f'Request body too large')
        if not body:
            return {}
        return _json.loads(body)
    except Exception:
        return {}