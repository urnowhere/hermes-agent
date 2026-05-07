from __future__ import annotations

import contextlib
import io
import sys
import types
from argparse import Namespace

if "dotenv" not in sys.modules:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = fake_dotenv

from hermes_cli import doctor as doctor_mod


def test_doctor_reports_google_gemini_api_connectivity(monkeypatch):
    for env_name in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "GOOGLE_API_KEY",
        "GLM_API_KEY",
        "ZAI_API_KEY",
        "Z_AI_API_KEY",
        "KIMI_API_KEY",
        "KIMI_CN_API_KEY",
        "STEPFUN_API_KEY",
        "ARCEEAI_API_KEY",
        "GMI_API_KEY",
        "DEEPSEEK_API_KEY",
        "HF_TOKEN",
        "NVIDIA_API_KEY",
        "DASHSCOPE_API_KEY",
        "MINIMAX_API_KEY",
        "MINIMAX_CN_API_KEY",
        "AI_GATEWAY_API_KEY",
        "KILOCODE_API_KEY",
        "OPENCODE_ZEN_API_KEY",
        "OPENCODE_GO_API_KEY",
        "WECOM_BOT_ID",
        "QQ_APP_ID",
    ):
        monkeypatch.delenv(env_name, raising=False)

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: ([], []),
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    try:
        from hermes_cli import auth as auth_mod

        monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {})
        monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {})
    except Exception:
        pass

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers, timeout))
        return types.SimpleNamespace(status_code=200)

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))
    out = buf.getvalue()

    assert "Google / Gemini API" in out
    assert any(
        url == "https://generativelanguage.googleapis.com/v1beta/models"
        and headers == {"x-goog-api-key": "gemini-test-key"}
        and timeout == 10
        for url, headers, timeout in calls
    )
