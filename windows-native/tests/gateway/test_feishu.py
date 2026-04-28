"""Tests for the Feishu gateway integration.

Copied from tests/gateway/test_feishu.py and fixed for Windows compatibility.
"""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

try:
    import lark_oapi

    _HAS_LARK_OAPI = True
except ImportError:
    _HAS_LARK_OAPI = False


class TestProcessInboundMessage(unittest.TestCase):
    """Test copied from test_feishu.py and fixed for Windows."""

    @patch.dict(os.environ, {"HERMES_HOME": tempfile.mkdtemp()})
    def test_process_inbound_group_message_keeps_group_type_when_chat_lookup_falls_back(
        self,
    ):
        from gateway.config import PlatformConfig
        from gateway.platforms.feishu import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._dispatch_inbound_event = AsyncMock()
        adapter.get_chat_info = AsyncMock(
            return_value={"chat_id": "oc_group", "name": "oc_group", "type": "dm"}
        )
        adapter._resolve_sender_profile = AsyncMock(
            return_value={
                "user_id": "ou_user",
                "user_name": "张三",
                "user_id_alt": None,
            }
        )
        message = SimpleNamespace(
            chat_id="oc_group",
            thread_id=None,
            message_type="text",
            content='{"text":"hello group"}',
            message_id="om_group_text",
        )
        sender_id = SimpleNamespace(open_id="ou_user", user_id=None, union_id=None)
        data = SimpleNamespace(event=SimpleNamespace(message=message))

        asyncio.run(
            adapter._process_inbound_message(
                data=data,
                message=message,
                sender_id=sender_id,
                chat_type="group",
                message_id="om_group_text",
            )
        )

        event = adapter._dispatch_inbound_event.await_args.args[0]
        self.assertEqual(event.source.chat_type, "group")
