from __future__ import annotations

from plugins.formsy.constraint_keeper import runtime


class FakeRuntimeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self


def test_default_runtime_coordinator_verifies_final_submit(monkeypatch, tmp_path):
    monkeypatch.delenv("FORMSY_CONSTRAINT_KEEPER_FAIL_CLOSED_ON_SUBMIT", raising=False)
    monkeypatch.setattr(runtime, "ConstraintKeeperClient", FakeRuntimeClient)
    monkeypatch.setattr(runtime, "_formsy_config", lambda: {"base_url": "http://runtime"})
    monkeypatch.setattr(runtime, "_spool_root", lambda: tmp_path)

    coordinator = runtime.build_default_coordinator()

    assert coordinator.fail_closed_on_submit is True


def test_runtime_coordinator_can_opt_out_of_final_submit_verifier(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMSY_CONSTRAINT_KEEPER_FAIL_CLOSED_ON_SUBMIT", "false")
    monkeypatch.setattr(runtime, "ConstraintKeeperClient", FakeRuntimeClient)
    monkeypatch.setattr(runtime, "_formsy_config", lambda: {"base_url": "http://runtime"})
    monkeypatch.setattr(runtime, "_spool_root", lambda: tmp_path)

    coordinator = runtime.build_default_coordinator()

    assert coordinator.fail_closed_on_submit is False
