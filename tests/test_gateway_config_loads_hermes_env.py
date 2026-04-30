from __future__ import annotations

from pathlib import Path

from gateway.config import Platform, load_gateway_config


def test_load_gateway_config_reads_hermes_env(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("TELEGRAM_BOT_TOKEN=test-token\n", encoding="utf-8")
    (hermes_home / "config.yaml").write_text("platforms:\n  telegram: {}\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    cfg = load_gateway_config()

    telegram = cfg.platforms[Platform.TELEGRAM]
    assert telegram.enabled is True
    assert telegram.token == "test-token"
