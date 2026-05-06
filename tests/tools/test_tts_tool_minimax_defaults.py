"""Regression test for issue #20869 - MiniMax TTS defaults update."""

from tools.tts_tool import DEFAULT_MINIMAX_MODEL, DEFAULT_MINIMAX_BASE_URL


def test_minimax_model_default():
    assert DEFAULT_MINIMAX_MODEL == "speech-02"


def test_minimax_base_url_default():
    assert DEFAULT_MINIMAX_BASE_URL == "https://api.minimaxi.com/v1/t2a_v2"