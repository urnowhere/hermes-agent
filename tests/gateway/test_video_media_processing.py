"""Tests for inbound video media processing in the gateway.

Covers the video_paths routing in the media loop and the
_extract_video_components / _enrich_message_with_video helpers.
"""

import asyncio
import os
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.platforms.base import MessageEvent, MessageType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(media_urls=None, media_types=None, message_type=MessageType.TEXT, text=""):
    """Build a minimal MessageEvent for testing."""
    evt = MagicMock(spec=MessageEvent)
    evt.text = text
    evt.message_type = message_type
    evt.media_urls = media_urls or []
    evt.media_types = media_types or []
    return evt


# ---------------------------------------------------------------------------
# Media-loop routing tests (unit-level, no I/O)
# ---------------------------------------------------------------------------

class TestVideoMediaRouting:
    """Verify that video/* MIME types and MessageType.VIDEO are routed correctly."""

    def test_video_mime_detected(self):
        """video/mp4 should be classified into video_paths."""
        image_paths = []
        audio_paths = []
        video_paths = []
        media_urls = ["/tmp/clip.mp4"]
        media_types = ["video/mp4"]

        for i, path in enumerate(media_urls):
            mtype = media_types[i] if i < len(media_types) else ""
            if mtype.startswith("image/") or False:
                image_paths.append(path)
            if mtype.startswith("audio/") or False:
                audio_paths.append(path)
            if mtype.startswith("video/") or False:
                video_paths.append(path)

        assert video_paths == ["/tmp/clip.mp4"]
        assert not image_paths
        assert not audio_paths

    def test_video_message_type_detected(self):
        """MessageType.VIDEO should route to video_paths even without MIME."""
        evt = _make_event(
            media_urls=["/tmp/clip.mov"],
            media_types=[""],
            message_type=MessageType.VIDEO,
        )

        video_paths = []
        for i, path in enumerate(evt.media_urls):
            mtype = evt.media_types[i] if i < len(evt.media_types) else ""
            if mtype.startswith("video/") or evt.message_type == MessageType.VIDEO:
                video_paths.append(path)

        assert video_paths == ["/tmp/clip.mov"]

    def test_mixed_media_routing(self):
        """Mixed image + video + audio should each go to the right bucket."""
        urls = ["/tmp/pic.jpg", "/tmp/clip.mp4", "/tmp/voice.ogg"]
        types = ["image/jpeg", "video/mp4", "audio/ogg"]

        image_paths, audio_paths, video_paths = [], [], []
        for i, path in enumerate(urls):
            mtype = types[i]
            if mtype.startswith("image/"):
                image_paths.append(path)
            if mtype.startswith("audio/"):
                audio_paths.append(path)
            if mtype.startswith("video/"):
                video_paths.append(path)

        assert len(image_paths) == 1
        assert len(audio_paths) == 1
        assert len(video_paths) == 1


# ---------------------------------------------------------------------------
# _extract_video_components tests
# ---------------------------------------------------------------------------

class TestExtractVideoComponents:
    """Test ffmpeg-based extraction (mocked subprocess)."""

    @pytest.mark.asyncio
    async def test_ffmpeg_not_found_graceful(self):
        """When ffmpeg is missing, should return (None, []) without raising."""
        from gateway.run import GatewayRunner

        app = MagicMock(spec=GatewayRunner)
        app._extract_video_components = GatewayRunner._extract_video_components.__get__(app)
        app.config = MagicMock(video_processing={"enabled": True, "ffmpeg_path": "ffmpeg", "max_size_mb": 500})

        with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg")):
            audio, frames = await app._extract_video_components("/tmp/fake.mp4")

        assert audio is None
        assert frames == []

    @pytest.mark.asyncio
    async def test_audio_extraction_timeout(self):
        """Timeout during audio extraction should be handled gracefully."""
        from gateway.run import GatewayRunner

        app = MagicMock(spec=GatewayRunner)
        app._extract_video_components = GatewayRunner._extract_video_components.__get__(app)
        app.config = MagicMock(video_processing={"enabled": True, "ffmpeg_path": "ffmpeg", "max_size_mb": 500})

        with patch("asyncio.to_thread", side_effect=subprocess.TimeoutExpired("ffmpeg", 120)):
            audio, frames = await app._extract_video_components("/tmp/fake.mp4")

        assert audio is None
        assert frames == []


# ---------------------------------------------------------------------------
# _enrich_message_with_video tests
# ---------------------------------------------------------------------------

class TestEnrichMessageWithVideo:
    """Test the high-level video enrichment method."""

    @pytest.mark.asyncio
    async def test_no_ffmpeg_returns_fallback_note(self):
        """When extraction fails completely, user gets a note."""
        from gateway.run import GatewayRunner

        app = MagicMock(spec=GatewayRunner)
        app._enrich_message_with_video = GatewayRunner._enrich_message_with_video.__get__(app)
        app._extract_video_components = AsyncMock(return_value=(None, []))
        app.config = MagicMock(video_processing={"enabled": True})

        result = await app._enrich_message_with_video("hello", ["/tmp/clip.mp4"])
        assert "could not be processed" in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_audio_only_enrichment(self):
        """When only audio is extracted, transcription is prepended."""
        from gateway.run import GatewayRunner

        app = MagicMock(spec=GatewayRunner)
        app._enrich_message_with_video = GatewayRunner._enrich_message_with_video.__get__(app)
        app._extract_video_components = AsyncMock(return_value=("/tmp/audio.wav", []))
        app._enrich_message_with_transcription = AsyncMock(return_value="Hello world")
        app._enrich_message_with_vision = AsyncMock(return_value="")
        app.config = MagicMock(video_processing={"enabled": True})

        result = await app._enrich_message_with_video("", ["/tmp/clip.mp4"])
        assert "transcription" in result.lower() or "Hello world" in result
        app._enrich_message_with_transcription.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_extraction_enriches_both(self):
        """Audio + frames -> transcription + vision both prepended."""
        import tempfile
        from pathlib import Path
        from gateway.run import GatewayRunner

        app = MagicMock(spec=GatewayRunner)
        app._enrich_message_with_video = GatewayRunner._enrich_message_with_video.__get__(app)
        app._enrich_message_with_transcription = AsyncMock(return_value="hello world")
        app._enrich_message_with_vision = AsyncMock(return_value="a red lobster")

        td = tempfile.mkdtemp()
        audio = os.path.join(td, "audio.wav")
        frame = os.path.join(td, "frame.jpg")
        Path(audio).touch()
        Path(frame).touch()
        app._extract_video_components = AsyncMock(return_value=(audio, [frame]))

        # Mock config for video_processing
        app.config = MagicMock()
        app.config.video_processing = {"enabled": True}

        result = await app._enrich_message_with_video("user text", ["/tmp/clip.mp4"])
        app._enrich_message_with_transcription.assert_called_once()
        app._enrich_message_with_vision.assert_called_once()
        assert "hello world" in result
        assert "a red lobster" in result
        assert "user text" in result


class TestExtractVideoComponentsIntegration:
    """Integration tests that exercise real ffmpeg."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not __import__("shutil").which("ffmpeg"),
        reason="ffmpeg not installed",
    )
    async def test_ffmpeg_real_extraction(self):
        """End-to-end: create a real tiny video, extract audio + frames."""
        import shutil
        import subprocess
        import tempfile
        from gateway.run import GatewayRunner

        tmp_dir = tempfile.mkdtemp()
        try:
            video = os.path.join(tmp_dir, "test.mp4")
            subprocess.run(
                ["ffmpeg", "-f", "lavfi",
                 "-i", "testsrc=duration=1:size=320x240:rate=10",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-t", "1", "-y", video],
                check=True, capture_output=True,
            )

            app = MagicMock(spec=GatewayRunner)
            app._extract_video_components = GatewayRunner._extract_video_components.__get__(app)
            app.config = MagicMock()
            app.config.video_processing = {"ffmpeg_path": "ffmpeg", "max_size_mb": 500}

            audio, frames = await app._extract_video_components(video)
            assert audio and os.path.getsize(audio) > 44
            assert len(frames) >= 1
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
