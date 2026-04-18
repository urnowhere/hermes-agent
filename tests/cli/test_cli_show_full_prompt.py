"""Tests for full prompt echo formatting in the CLI."""

from cli import HermesCLI


def _make_cli(show_full_prompt: bool):
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.show_full_prompt = show_full_prompt
    return cli_obj


class TestSubmittedPromptFormatting:
    def test_multiline_prompt_uses_compact_preview_by_default(self):
        cli_obj = _make_cli(show_full_prompt=False)

        rendered = cli_obj._format_submitted_user_input("alpha\nbeta\ngamma")

        assert "alpha" in rendered
        assert "(+2 lines)" in rendered
        assert "beta" not in rendered
        assert "gamma" not in rendered

    def test_multiline_prompt_can_render_full_text(self):
        cli_obj = _make_cli(show_full_prompt=True)

        rendered = cli_obj._format_submitted_user_input("alpha\nbeta\ngamma")

        assert "alpha" in rendered
        assert "beta" in rendered
        assert "gamma" in rendered
        assert "(3 lines total)" in rendered
        assert "(+2 lines)" not in rendered

    def test_single_line_prompt_is_unchanged(self):
        cli_obj = _make_cli(show_full_prompt=True)

        rendered = cli_obj._format_submitted_user_input("just one line")

        assert "just one line" in rendered
        assert "lines total" not in rendered
        assert "(+" not in rendered
