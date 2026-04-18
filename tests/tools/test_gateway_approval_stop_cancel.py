"""Regression: /stop (session interrupt) must unblock gateway approval waits (#8697)."""

import os
import threading
from unittest.mock import patch


def _clear_approval_state():
    from tools import approval as mod

    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()
    mod._session_approved.clear()
    mod._permanent_approved.clear()
    mod._pending.clear()


class TestGatewayApprovalStopCancel:
    SESSION_KEY = "stop-cancel-test-session"

    def setup_method(self):
        _clear_approval_state()
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("HERMES_GATEWAY_SESSION", "HERMES_YOLO_MODE", "HERMES_SESSION_KEY")
        }
        os.environ.pop("HERMES_YOLO_MODE", None)
        os.environ["HERMES_GATEWAY_SESSION"] = "1"
        os.environ["HERMES_SESSION_KEY"] = self.SESSION_KEY

    def teardown_method(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _clear_approval_state()

    def test_cancel_gateway_approvals_unblocks_wait_as_deny(self):
        from tools.approval import (
            cancel_gateway_approvals_on_user_interrupt,
            check_all_command_guards,
            register_gateway_notify,
        )

        register_gateway_notify(self.SESSION_KEY, lambda _payload: None)

        result_holder: dict = {}

        def _run_check():
            result_holder["result"] = check_all_command_guards(
                "rm -rf /tmp/nonexistent-stop-cancel-target", "local"
            )

        thread = threading.Thread(target=_run_check, daemon=True)
        thread.start()
        threading.Event().wait(timeout=0.3)

        n = cancel_gateway_approvals_on_user_interrupt(self.SESSION_KEY, reason="stop")
        thread.join(timeout=5.0)

        assert not thread.is_alive()
        assert n >= 1
        res = result_holder["result"]
        assert res["approved"] is False
        msg = res.get("message", "")
        assert "BLOCKED" in msg
        assert "denied" in msg.lower()

    def test_gateway_block_wait_timeout_prefers_approval_timeout_per_command(self):
        from tools import approval as mod

        with patch.object(
            mod,
            "_get_approval_config",
            return_value={"approval_timeout_per_command": 42, "gateway_timeout": 999},
        ):
            assert mod._gateway_block_wait_timeout() == 42

        with patch.object(
            mod,
            "_get_approval_config",
            return_value={"gateway_timeout": 77},
        ):
            assert mod._gateway_block_wait_timeout() == 77
