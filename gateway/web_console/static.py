"""Static content helpers for the Hermes Web Console."""

from __future__ import annotations

import json
import re
from pathlib import Path

APP_BASE_PATH = "/app/"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_CONSOLE_ROOT = _REPO_ROOT / "web_console"
_DIST_DIR = _WEB_CONSOLE_ROOT / "dist"


def get_web_console_frontend_root() -> Path:
    """Return the source root for the web console frontend."""
    return _WEB_CONSOLE_ROOT


def get_web_console_dist_dir() -> Path:
    """Return the production build output directory for the web console."""
    return _DIST_DIR


def get_web_console_placeholder_html() -> str:
    """Return a minimal placeholder page for the GUI app shell."""
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Hermes Web Console</title>
  <style>
    body {
      margin: 0;
      font-family: system-ui, sans-serif;
      background: #0b1020;
      color: #edf2ff;
      display: grid;
      place-items: center;
      min-height: 100vh;
    }
    main {
      max-width: 42rem;
      padding: 2rem;
      border: 1px solid rgba(255, 255, 255, 0.14);
      border-radius: 1rem;
      background: rgba(18, 25, 45, 0.92);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.35);
    }
    h1 {
      margin-top: 0;
    }
    p {
      line-height: 1.5;
      color: #cbd5f5;
    }
    code {
      background: rgba(255, 255, 255, 0.08);
      padding: 0.15rem 0.35rem;
      border-radius: 0.35rem;
    }
  </style>
</head>
<body>
  <main>
    <h1>Hermes Web Console</h1>
    <p>This is the initial GUI backend placeholder mounted by the API server.</p>
    <p>Backend status endpoints are available at <code>/api/gui/health</code> and <code>/api/gui/meta</code>.</p>
  </main>
</body>
</html>
"""


def has_built_web_console() -> bool:
    """Return True when a production frontend bundle is available."""
    return (get_web_console_dist_dir() / "index.html").exists()


def _rewrite_built_index_html(html: str) -> str:
    """Rewrite root-relative build output so it can be mounted under /app/."""
    html = re.sub(r'href="/assets/manifest[^"]*\.json"', 'href="/app/manifest.json"', html)
    html = html.replace('src="/assets/', 'src="/app/assets/')
    html = html.replace('href="/assets/', 'href="/app/assets/')
    html = html.replace("navigator.serviceWorker.register('/sw.js')", "navigator.serviceWorker.register('/app/sw.js')")
    html = html.replace('navigator.serviceWorker.register("/sw.js")', 'navigator.serviceWorker.register("/app/sw.js")')
    return html


def get_web_console_app_html() -> str:
    """Return the production app HTML when built, otherwise a placeholder."""
    index_path = get_web_console_dist_dir() / "index.html"
    if not index_path.exists():
        return get_web_console_placeholder_html()
    return _rewrite_built_index_html(index_path.read_text(encoding="utf-8"))


def get_web_console_manifest_json() -> str | None:
    """Return the PWA manifest rewritten for the /app/ mount."""
    manifest_path = get_web_console_frontend_root() / "manifest.json"
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["start_url"] = APP_BASE_PATH
    data["scope"] = APP_BASE_PATH
    for icon in data.get("icons", []):
        src = str(icon.get("src") or "")
        if src.startswith("/icons/"):
            icon["src"] = f"{APP_BASE_PATH}icons/{src.split('/icons/', 1)[1]}"
    return json.dumps(data)


def get_web_console_service_worker() -> str | None:
    """Return the service worker rewritten for the /app/ mount."""
    sw_path = get_web_console_frontend_root() / "sw.js"
    if not sw_path.exists():
        return None
    text = sw_path.read_text(encoding="utf-8")
    return text.replace(
        "const STATIC_ASSETS = [\n  '/',\n  '/index.html',\n  '/manifest.json',\n];",
        "const STATIC_ASSETS = [\n  '/app/',\n  '/app/index.html',\n  '/app/manifest.json',\n];",
    )
