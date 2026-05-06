"""Tests for macOS TCC file-search guidance in the system prompt."""

import agent.prompt_builder as prompt_builder


def test_macos_environment_hints_explain_tcc_search_defaults(monkeypatch):
    monkeypatch.setattr(prompt_builder.sys, "platform", "darwin")
    monkeypatch.setattr(prompt_builder, "is_wsl", lambda: False)

    hints = prompt_builder.build_environment_hints()

    assert "macOS file-search note" in hints
    assert "~/Library/Containers" in hints
    assert "include_tcc_paths=True" in hints


def test_non_macos_environment_hints_do_not_include_tcc_note(monkeypatch):
    monkeypatch.setattr(prompt_builder.sys, "platform", "linux")
    monkeypatch.setattr(prompt_builder, "is_wsl", lambda: False)

    assert "macOS file-search note" not in prompt_builder.build_environment_hints()
