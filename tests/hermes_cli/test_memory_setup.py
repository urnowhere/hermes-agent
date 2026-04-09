from argparse import Namespace

from hermes_cli import memory_setup as memory_setup_mod


def _make_args() -> Namespace:
    return Namespace()


class TestMemorySetup:
    def test_choice_dicts_use_value_and_default_hint(self, monkeypatch, capsys):
        """Choice metadata from plugin schemas should drive the selector cleanly.

        Keep returns choice objects, not just plain strings. The setup wizard
        needs to:
        - show the human-friendly labels/descriptions to the reviewer
        - use the declared default when there is no saved value yet
        - write back the machine-facing ``value`` rather than the label
        """

        class Provider:
            def get_config_schema(self):
                return [{
                    "key": "summarizer",
                    "description": "Summarization provider",
                    "choices": [
                        {
                            "label": "OpenAI (Recommended)",
                            "description": "Best default for most installs",
                            "value": "openai",
                            "default": True,
                        },
                        {
                            "label": "Anthropic",
                            "description": "Use Claude for summaries",
                            "value": "anthropic",
                        },
                    ],
                }]

            def save_config(self, values, hermes_home):
                self.saved = (values, hermes_home)

        provider = Provider()
        selects = []
        saved_configs = []

        def fake_select(title, items, default=0):
            selects.append((title, items, default))
            return 0

        monkeypatch.setenv("HERMES_HOME", "/tmp/hermes-home")
        monkeypatch.setattr(memory_setup_mod, "_get_available_providers", lambda: [("keep", "local", provider)])
        monkeypatch.setattr(memory_setup_mod, "_install_dependencies", lambda name: None)
        monkeypatch.setattr(memory_setup_mod, "_curses_select", fake_select)
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
        monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved_configs.append(cfg))

        memory_setup_mod.cmd_setup(_make_args())

        assert selects[0] == (
            "Memory provider setup",
            [("keep", "— local"), ("Built-in only", "— MEMORY.md / USER.md (default)")],
            1,
        )
        assert selects[1] == (
            "  Summarization provider",
            [
                ("OpenAI (Recommended)", "Best default for most installs"),
                ("Anthropic", "Use Claude for summaries"),
            ],
            0,
        )
        assert saved_configs == [{"memory": {"provider": "keep"}}]
        assert provider.saved == ({"summarizer": "openai"}, "/tmp/hermes-home")

        out = capsys.readouterr().out
        assert "Provider config saved" in out

    def test_provider_config_failure_does_not_claim_success(self, monkeypatch, capsys):
        """Activation and provider-native config are separate steps.

        Keep setup first records ``memory.provider = keep`` in Hermes config,
        then asks the provider to write its own native config files. If that
        second step fails, the wizard should keep the warning visible and avoid
        printing the misleading "Provider config saved" success line.
        """

        class Provider:
            def get_config_schema(self):
                return [{
                    "key": "store_kind",
                    "description": "Store kind",
                    "choices": ["local"],
                    "default": "local",
                }]

            def save_config(self, values, hermes_home):
                raise RuntimeError("native write failed")

        saved_configs = []

        monkeypatch.setenv("HERMES_HOME", "/tmp/hermes-home")
        monkeypatch.setattr(memory_setup_mod, "_get_available_providers", lambda: [("keep", "local", Provider())])
        monkeypatch.setattr(memory_setup_mod, "_install_dependencies", lambda name: None)
        monkeypatch.setattr(memory_setup_mod, "_curses_select", lambda title, items, default=0: 0)
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
        monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved_configs.append(cfg))

        memory_setup_mod.cmd_setup(_make_args())

        assert saved_configs == [{"memory": {"provider": "keep"}}]

        out = capsys.readouterr().out
        assert "Failed to write provider config" in out
        assert "Activation saved to config.yaml" in out
        assert "Provider config saved" not in out
