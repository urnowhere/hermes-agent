import json
import logging
import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

from cron.scheduler import run_job
from hermes_time import now as hermes_now


class TestCronSchedulerJobProfiles:
    def _write_profile_fixture(self, owner_home, profile_name="field"):
        owner_home.mkdir(parents=True, exist_ok=True)
        (owner_home / "cron").mkdir(exist_ok=True)
        (owner_home / "sessions").mkdir(exist_ok=True)
        (owner_home / ".env").write_text("PROFILE_ENV_MARKER=owner-env\n", encoding="utf-8")
        (owner_home / "SOUL.md").write_text("OWNER SOUL IDENTITY", encoding="utf-8")
        (owner_home / "config.yaml").write_text(
            "model:\n  default: owner-model\nagent:\n  max_turns: 3\n",
            encoding="utf-8",
        )

        profile_home = owner_home / "profiles" / profile_name
        profile_home.mkdir(parents=True, exist_ok=True)
        (profile_home / "cron").mkdir(exist_ok=True)
        (profile_home / "sessions").mkdir(exist_ok=True)
        (profile_home / ".env").write_text("PROFILE_ENV_MARKER=profile-env\n", encoding="utf-8")
        (profile_home / "SOUL.md").write_text("PROFILE SOUL IDENTITY", encoding="utf-8")
        (profile_home / "profile_prefill.json").write_text(
            json.dumps([{"role": "user", "content": "profile prefill"}]),
            encoding="utf-8",
        )
        (profile_home / "config.yaml").write_text(
            "\n".join(
                [
                    "model:",
                    "  default: profile-model",
                    "agent:",
                    "  max_turns: 7",
                    "prefill_messages_file: profile_prefill.json",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return profile_home

    def _fake_dotenv_loader(self, loaded_paths):
        def _load(path=None, *args, **kwargs):
            if path is None:
                path = kwargs.get("dotenv_path")
            loaded_paths.append(str(path))
            env_path = os.fspath(path)
            if os.path.exists(env_path):
                for line in open(env_path, encoding=kwargs.get("encoding") or "utf-8"):
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    os.environ[key] = value
            return True
        return _load

    def _fake_agent_class(self, captured):
        class FakeAgent:
            def __init__(self, **kwargs):
                from hermes_constants import get_hermes_home
                from agent.prompt_builder import load_soul_md

                captured["kwargs"] = kwargs
                captured["home_at_init"] = str(get_hermes_home())
                captured["soul_at_init"] = load_soul_md()
                captured["env_at_init"] = os.getenv("PROFILE_ENV_MARKER")

            def run_conversation(self, prompt):
                from hermes_constants import get_hermes_home
                from agent.prompt_builder import load_soul_md

                captured["home_at_run"] = str(get_hermes_home())
                captured["soul_at_run"] = load_soul_md()
                captured["env_at_run"] = os.getenv("PROFILE_ENV_MARKER")
                return {"final_response": "ok"}

            def close(self):
                captured["closed"] = True

        return FakeAgent

    def _runtime_patch(self):
        return patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            return_value={
                "api_key": "test-key",
                "base_url": "https://example.invalid/v1",
                "provider": "openrouter",
                "api_mode": "chat_completions",
            },
        )

    def test_run_job_profile_loads_selected_profile_env_config_and_soul(
        self, tmp_path, monkeypatch
    ):
        owner_home = tmp_path / "owner"
        profile_home = self._write_profile_fixture(owner_home, "field")
        monkeypatch.setenv("HERMES_HOME", str(owner_home))

        fake_db = MagicMock()
        captured = {}
        loaded_env_paths = []

        with patch("cron.scheduler._hermes_home", owner_home), \
             patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("dotenv.load_dotenv", side_effect=self._fake_dotenv_loader(loaded_env_paths)), \
             patch("hermes_state.SessionDB", return_value=fake_db), \
             self._runtime_patch(), \
             patch("run_agent.AIAgent", self._fake_agent_class(captured)):
            success, _output, final_response, error = run_job({
                "id": "profile-job",
                "name": "profile job",
                "prompt": "hello",
                "profile": "field",
            })

        assert success is True
        assert final_response == "ok"
        assert error is None
        assert loaded_env_paths[-1] == str(profile_home / ".env")
        assert captured["kwargs"]["model"] == "profile-model"
        assert captured["kwargs"]["max_iterations"] == 7
        assert captured["kwargs"]["prefill_messages"] == [
            {"role": "user", "content": "profile prefill"}
        ]
        assert captured["home_at_init"] == str(profile_home)
        assert captured["home_at_run"] == str(profile_home)
        assert captured["soul_at_init"] == "PROFILE SOUL IDENTITY"
        assert captured["soul_at_run"] == "PROFILE SOUL IDENTITY"
        assert captured["env_at_run"] == "profile-env"
        assert os.environ["HERMES_HOME"] == str(owner_home)

    def test_run_job_blank_profile_preserves_server_default(self, tmp_path, monkeypatch):
        owner_home = tmp_path / "owner"
        self._write_profile_fixture(owner_home, "field")
        monkeypatch.setenv("HERMES_HOME", str(owner_home))

        fake_db = MagicMock()
        captured = {}
        loaded_env_paths = []

        with patch("cron.scheduler._hermes_home", owner_home), \
             patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("dotenv.load_dotenv", side_effect=self._fake_dotenv_loader(loaded_env_paths)), \
             patch("hermes_state.SessionDB", return_value=fake_db), \
             self._runtime_patch(), \
             patch("run_agent.AIAgent", self._fake_agent_class(captured)):
            success, _output, _final_response, error = run_job({
                "id": "legacy-job",
                "name": "legacy job",
                "prompt": "hello",
                "profile": "   ",
            })

        assert success is True
        assert error is None
        assert loaded_env_paths[-1] == str(owner_home / ".env")
        assert captured["kwargs"]["model"] == "owner-model"
        assert captured["home_at_run"] == str(owner_home)
        assert captured["soul_at_run"] == "OWNER SOUL IDENTITY"
        assert captured["env_at_run"] == "owner-env"

    def test_run_job_deleted_profile_warns_and_falls_back_to_server_default(
        self, tmp_path, monkeypatch, caplog
    ):
        owner_home = tmp_path / "owner"
        self._write_profile_fixture(owner_home, "field")
        monkeypatch.setenv("HERMES_HOME", str(owner_home))

        fake_db = MagicMock()
        captured = {}
        loaded_env_paths = []

        with caplog.at_level(logging.WARNING, logger="cron.scheduler"), \
             patch("cron.scheduler._hermes_home", owner_home), \
             patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("dotenv.load_dotenv", side_effect=self._fake_dotenv_loader(loaded_env_paths)), \
             patch("hermes_state.SessionDB", return_value=fake_db), \
             self._runtime_patch(), \
             patch("run_agent.AIAgent", self._fake_agent_class(captured)):
            success, _output, _final_response, error = run_job({
                "id": "deleted-profile-job",
                "name": "deleted profile job",
                "prompt": "hello",
                "profile": "ghost",
            })

        assert success is True
        assert error is None
        assert loaded_env_paths[-1] == str(owner_home / ".env")
        assert captured["kwargs"]["model"] == "owner-model"
        assert captured["home_at_run"] == str(owner_home)
        assert "profile" in caplog.text
        assert "ghost" in caplog.text
        assert "falling back" in caplog.text.lower()

    def test_tick_profile_job_persists_run_metadata_in_owner_cron_store(
        self, tmp_path, monkeypatch
    ):
        import cron.jobs as cron_jobs
        import cron.scheduler as scheduler

        owner_home = tmp_path / "owner"
        profile_home = self._write_profile_fixture(owner_home, "field")
        monkeypatch.setenv("HERMES_HOME", str(owner_home))

        owner_cron = owner_home / "cron"
        owner_output = owner_cron / "output"
        owner_jobs_file = owner_cron / "jobs.json"
        due_at = (hermes_now() - timedelta(seconds=1)).isoformat()
        job = {
            "id": "profile-tick",
            "name": "profile tick",
            "prompt": "hello",
            "enabled": True,
            "deliver": "local",
            "profile": "field",
            "schedule": {"kind": "once", "run_at": due_at, "display": "once"},
            "schedule_display": "once",
            "next_run_at": due_at,
        }
        owner_jobs_file.write_text(json.dumps({"jobs": [job]}), encoding="utf-8")

        fake_db = MagicMock()
        captured = {}
        loaded_env_paths = []

        with patch.object(cron_jobs, "CRON_DIR", owner_cron), \
             patch.object(cron_jobs, "JOBS_FILE", owner_jobs_file), \
             patch.object(cron_jobs, "OUTPUT_DIR", owner_output), \
             patch.object(scheduler, "_hermes_home", owner_home), \
             patch.object(scheduler, "_LOCK_DIR", owner_cron), \
             patch.object(scheduler, "_LOCK_FILE", owner_cron / ".tick.lock"), \
             patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("dotenv.load_dotenv", side_effect=self._fake_dotenv_loader(loaded_env_paths)), \
             patch("hermes_state.SessionDB", return_value=fake_db), \
             self._runtime_patch(), \
             patch("run_agent.AIAgent", self._fake_agent_class(captured)):
            assert scheduler.tick(verbose=False) == 1

        owner_jobs = json.loads(owner_jobs_file.read_text(encoding="utf-8"))["jobs"]
        assert owner_jobs[0]["id"] == "profile-tick"
        assert owner_jobs[0]["last_status"] == "ok"
        assert owner_jobs[0]["last_run_at"]
        assert owner_jobs[0]["profile"] == "field"
        assert loaded_env_paths[-1] == str(profile_home / ".env")
        assert captured["home_at_run"] == str(profile_home)
        assert list((owner_output / "profile-tick").glob("*.md"))
        assert not (profile_home / "cron" / "jobs.json").exists()
        assert not (profile_home / "cron" / "output" / "profile-tick").exists()
