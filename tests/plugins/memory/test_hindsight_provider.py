import sys
import types

from plugins.memory.hindsight import HindsightMemoryProvider


class FakeEmbeddedClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._manager = FakeManager()
        self.started = False

    def _ensure_started(self):
        self.started = True


class InlineThread:
    def __init__(self, target=None, daemon=None, name=None):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


class FakeManager:
    def is_running(self, profile):
        return False

    def stop(self, profile):
        return None


def test_initialize_local_uses_real_env_key_when_config_secret_is_placeholder(monkeypatch, tmp_path):
    fake_hindsight = types.ModuleType("hindsight")
    fake_hindsight.HindsightEmbedded = FakeEmbeddedClient
    monkeypatch.setitem(sys.modules, "hindsight", fake_hindsight)
    fake_daemon_manager = types.ModuleType("hindsight_embed.daemon_embed_manager")
    fake_daemon_manager.console = None
    monkeypatch.setitem(sys.modules, "hindsight_embed", types.ModuleType("hindsight_embed"))
    monkeypatch.setitem(sys.modules, "hindsight_embed.daemon_embed_manager", fake_daemon_manager)

    fake_rich_console = types.ModuleType("rich.console")

    class FakeConsole:
        def __init__(self, *args, **kwargs):
            pass

    fake_rich_console.Console = FakeConsole
    monkeypatch.setitem(sys.modules, "rich", types.ModuleType("rich"))
    monkeypatch.setitem(sys.modules, "rich.console", fake_rich_console)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HINDSIGHT_LLM_API_KEY", "real-secret")
    monkeypatch.setattr("plugins.memory.hindsight._load_config", lambda: {
        "mode": "local",
        "profile": "demo",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "llmApiKey": "***",
    })
    monkeypatch.setattr("plugins.memory.hindsight.get_hermes_home", lambda: tmp_path / ".hermes")
    monkeypatch.setattr("plugins.memory.hindsight.threading.Thread", InlineThread)

    provider = HindsightMemoryProvider()
    provider.initialize("session-1")

    profile_env = tmp_path / ".hindsight" / "profiles" / "demo.env"
    contents = profile_env.read_text()
    assert provider._client.kwargs["llm_api_key"] == "real-secret"
    assert "HINDSIGHT_API_LLM_API_KEY=real-secret" in contents
    assert "***" not in contents
    assert provider._client.started is True
