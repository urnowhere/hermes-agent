"""Smoke test for the zoom_meeting plugin CLI wiring."""

from __future__ import annotations

from pathlib import Path


def test_zoom_subcommand_is_reachable_from_main_cli(tmp_path, monkeypatch, capsys):
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir(parents=True)
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - zoom_meeting\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("ZOOM_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("ZOOM_CLIENT_ID", raising=False)
    monkeypatch.delenv("ZOOM_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["hermes", "zoom", "auth-check"],
    )

    import hermes_cli.plugins as plugins_mod
    from hermes_cli.main import main

    plugins_mod._plugin_manager = plugins_mod.PluginManager()
    main()

    out = capsys.readouterr().out
    assert "Zoom OAuth env vars missing" in out
    assert "ZOOM_ACCOUNT_ID" in out
