#!/usr/bin/env python3
"""Tests for Google Veo video_generate plugin."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-12345")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _clear_model_overrides(monkeypatch):
    monkeypatch.delenv("GOOGLE_VIDEO_MODEL", raising=False)


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch):
    """Polling loop calls time.sleep — short-circuit so tests stay fast."""
    monkeypatch.setattr("plugins.video_gen.google.time.sleep", lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# Tool gate + model resolution
# ---------------------------------------------------------------------------


class TestGate:
    def test_available_with_gemini_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        from plugins.video_gen.google import _check_video_gen_available

        assert _check_video_gen_available() is True

    def test_available_with_google_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        from plugins.video_gen.google import _check_video_gen_available

        assert _check_video_gen_available() is True

    def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        from plugins.video_gen.google import _check_video_gen_available

        assert _check_video_gen_available() is False


class TestModelResolution:
    def test_default_is_veo3_fast(self):
        from plugins.video_gen.google import _resolve_model

        model_id, _ = _resolve_model()
        assert model_id == "veo-3.0-fast-generate-001"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_VIDEO_MODEL", "veo-3.0-generate-001")
        from plugins.video_gen.google import _resolve_model

        model_id, _ = _resolve_model()
        assert model_id == "veo-3.0-generate-001"

    def test_env_override_unknown_falls_back(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_VIDEO_MODEL", "veo-99-impossible")
        from plugins.video_gen.google import _resolve_model

        model_id, _ = _resolve_model()
        assert model_id == "veo-3.0-fast-generate-001"


# ---------------------------------------------------------------------------
# Helpers — operation submit / poll / extract / download
# ---------------------------------------------------------------------------


class TestSubmitOp:
    def test_returns_op_name(self):
        from plugins.video_gen.google import _start_op

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "models/veo-3.0-fast-generate-001/operations/abc"}

        with patch("plugins.video_gen.google.requests.post", return_value=mock_resp) as post:
            name, err = _start_op(
                api_key="k", model_id="veo-3.0-fast-generate-001",
                prompt="x", aspect_ratio="16:9", duration_seconds=4,
            )
        assert err is None
        assert name == "models/veo-3.0-fast-generate-001/operations/abc"

        called_url = post.call_args.args[0]
        assert ":predictLongRunning" in called_url
        body = post.call_args.kwargs["json"]
        assert body["instances"][0]["prompt"] == "x"
        assert body["parameters"]["aspectRatio"] == "16:9"
        assert body["parameters"]["durationSeconds"] == 4
        assert post.call_args.kwargs["params"] == {"key": "k"}

    def test_api_error_propagates(self):
        from plugins.video_gen.google import _start_op

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"error": {"message": "paid plan required"}}'
        mock_resp.json.return_value = {"error": {"message": "paid plan required"}}

        with patch("plugins.video_gen.google.requests.post", return_value=mock_resp):
            _, err = _start_op(
                api_key="k", model_id="veo-3.0-fast-generate-001",
                prompt="x", aspect_ratio="16:9", duration_seconds=4,
            )
        assert err is not None
        assert "paid plan" in err

    def test_timeout(self):
        import requests as req_lib
        from plugins.video_gen.google import _start_op

        with patch("plugins.video_gen.google.requests.post", side_effect=req_lib.Timeout()):
            _, err = _start_op(
                api_key="k", model_id="veo-3.0-fast-generate-001",
                prompt="x", aspect_ratio="16:9", duration_seconds=4,
            )
        assert err is not None and "timed out" in err


class TestPollOp:
    def test_returns_body_when_done(self):
        from plugins.video_gen.google import _poll_op

        # Two pending responses, then a done one — verifies the loop polls.
        responses = [
            MagicMock(status_code=200, **{"json.return_value": {"done": False}}),
            MagicMock(status_code=200, **{"json.return_value": {"done": False}}),
            MagicMock(status_code=200, **{"json.return_value": {"done": True, "response": {}}}),
        ]

        with patch("plugins.video_gen.google.requests.get", side_effect=responses):
            body, err = _poll_op(api_key="k", op_name="models/veo/operations/x")
        assert err is None
        assert body == {"done": True, "response": {}}

    def test_polling_returns_non_200(self):
        from plugins.video_gen.google import _poll_op

        mock_resp = MagicMock(status_code=500, text="oops")
        with patch("plugins.video_gen.google.requests.get", return_value=mock_resp):
            body, err = _poll_op(api_key="k", op_name="models/veo/operations/x")
        assert body is None
        assert err is not None and "500" in err


class TestExtractUri:
    def test_finds_uri_in_generateVideoResponse(self):
        from plugins.video_gen.google import _extract_video_uri

        body = {
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [{"video": {"uri": "https://x/file.mp4"}}]
                }
            }
        }
        assert _extract_video_uri(body) == "https://x/file.mp4"

    def test_finds_uri_at_top_level(self):
        from plugins.video_gen.google import _extract_video_uri

        body = {"response": {"generatedSamples": [{"video": {"uri": "https://y/v.mp4"}}]}}
        assert _extract_video_uri(body) == "https://y/v.mp4"

    def test_returns_none_on_error(self):
        from plugins.video_gen.google import _extract_video_uri

        assert _extract_video_uri({"error": {"message": "x"}}) is None

    def test_returns_none_when_missing(self):
        from plugins.video_gen.google import _extract_video_uri

        assert _extract_video_uri({"response": {}}) is None


class TestDownload:
    def test_success(self):
        from plugins.video_gen.google import _download_video

        mock_resp = MagicMock(status_code=200, content=b"\x00" * 5000)
        with patch("plugins.video_gen.google.requests.get", return_value=mock_resp) as g:
            content, err = _download_video(api_key="k", uri="https://x/v.mp4")
        assert err is None
        assert content == b"\x00" * 5000
        # Auth via header, follow redirects
        assert g.call_args.kwargs["headers"]["x-goog-api-key"] == "k"
        assert g.call_args.kwargs["allow_redirects"] is True

    def test_small_body_rejected(self):
        from plugins.video_gen.google import _download_video

        mock_resp = MagicMock(status_code=200, content=b'{"err":"x"}')
        with patch("plugins.video_gen.google.requests.get", return_value=mock_resp):
            content, err = _download_video(api_key="k", uri="https://x")
        assert content is None
        assert err is not None and "small" in err.lower()


# ---------------------------------------------------------------------------
# End-to-end handler
# ---------------------------------------------------------------------------


class TestHandler:
    def test_full_happy_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("plugins.video_gen.google._videos_cache_dir", lambda: tmp_path)
        from plugins.video_gen.google import _handle_video_generate

        with patch("plugins.video_gen.google._start_op", return_value=("ops/x", None)):
            with patch("plugins.video_gen.google._poll_op", return_value=(
                {"done": True, "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/v.mp4"}}]}}},
                None,
            )):
                with patch("plugins.video_gen.google._download_video", return_value=(b"\x00" * 5000, None)):
                    raw = _handle_video_generate({"prompt": "a robot waves", "duration_seconds": 4})

        result = json.loads(raw)
        assert result["success"] is True
        assert result["model"] == "veo-3.0-fast-generate-001"
        assert result["aspect_ratio"] == "16:9"
        assert result["duration_seconds"] == 4
        # File actually saved
        from pathlib import Path
        saved = Path(result["video"])
        assert saved.exists()
        assert saved.stat().st_size == 5000

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        from plugins.video_gen.google import _handle_video_generate

        result = json.loads(_handle_video_generate({"prompt": "x"}))
        assert "GEMINI_API_KEY" in result["error"]
        assert result["error_type"] == "auth_required"

    def test_empty_prompt(self):
        from plugins.video_gen.google import _handle_video_generate

        result = json.loads(_handle_video_generate({"prompt": ""}))
        assert "prompt is required" in result["error"]

    def test_invalid_aspect_ratio_normalised(self):
        from plugins.video_gen.google import _handle_video_generate

        captured = {}

        def fake_start(**kw):
            captured.update(kw)
            return None, "stop here"

        with patch("plugins.video_gen.google._start_op", side_effect=fake_start):
            _handle_video_generate({"prompt": "x", "aspect_ratio": "21:9"})
        # 21:9 is not in the enum — should be normalised to the default 16:9.
        assert captured["aspect_ratio"] == "16:9"

    def test_duration_clamped(self):
        from plugins.video_gen.google import _handle_video_generate

        captured = {}

        def fake_start(**kw):
            captured.update(kw)
            return None, "stop"

        with patch("plugins.video_gen.google._start_op", side_effect=fake_start):
            _handle_video_generate({"prompt": "x", "duration_seconds": 99})
        assert captured["duration_seconds"] == 16  # clamped to max

        captured.clear()
        with patch("plugins.video_gen.google._start_op", side_effect=fake_start):
            _handle_video_generate({"prompt": "x", "duration_seconds": 1})
        assert captured["duration_seconds"] == 2  # clamped to min

    def test_submit_error_propagates(self):
        from plugins.video_gen.google import _handle_video_generate

        with patch("plugins.video_gen.google._start_op", return_value=(None, "submit failed: 400")):
            result = json.loads(_handle_video_generate({"prompt": "x"}))
        assert result["error_type"] == "api_error"
        assert "submit failed" in result["error"]

    def test_op_finished_with_error(self):
        from plugins.video_gen.google import _handle_video_generate

        with patch("plugins.video_gen.google._start_op", return_value=("ops/x", None)):
            with patch("plugins.video_gen.google._poll_op", return_value=(
                {"done": True, "error": {"message": "content policy"}}, None,
            )):
                result = json.loads(_handle_video_generate({"prompt": "x"}))
        assert result["error_type"] == "api_error"
        assert "content policy" in result["error"]

    def test_no_uri_in_response(self):
        from plugins.video_gen.google import _handle_video_generate

        with patch("plugins.video_gen.google._start_op", return_value=("ops/x", None)):
            with patch("plugins.video_gen.google._poll_op", return_value=(
                {"done": True, "response": {}}, None,
            )):
                result = json.loads(_handle_video_generate({"prompt": "x"}))
        assert result["error_type"] == "empty_response"


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------


class TestSchema:
    def test_schema_shape(self):
        from plugins.video_gen.google import VIDEO_GENERATE_SCHEMA

        assert VIDEO_GENERATE_SCHEMA["name"] == "video_generate"
        params = VIDEO_GENERATE_SCHEMA["parameters"]["properties"]
        assert "prompt" in params
        assert params["aspect_ratio"]["enum"] == ["16:9", "9:16", "1:1"]
        assert params["duration_seconds"]["minimum"] == 2
        assert params["duration_seconds"]["maximum"] == 16


class TestRegistration:
    def test_register_calls_register_tool(self):
        from plugins.video_gen.google import register

        ctx = MagicMock()
        register(ctx)
        ctx.register_tool.assert_called_once()
        kwargs = ctx.register_tool.call_args.kwargs
        assert kwargs["name"] == "video_generate"
        assert kwargs["toolset"] == "video_gen"
        assert kwargs["emoji"] == "🎬"
        assert kwargs["check_fn"] is not None
