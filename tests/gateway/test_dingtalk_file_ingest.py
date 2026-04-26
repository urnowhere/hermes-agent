import asyncio
import os
from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from gateway.platforms.dingtalk import DingTalkAdapter, _IncomingHandler


class _FakeResponse:
    def __init__(self, *, json_data=None, content=b"", headers=None):
        self._json_data = json_data or {}
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_data

    async def aread(self):
        return self.content


class _FakeHttpClient:
    def __init__(self):
        self.posts = []
        self.gets = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse(json_data={"downloadUrl": "https://cdn.example.com/download/blob"})

    async def get(self, url, headers=None, timeout=None):
        self.gets.append({"url": url, "headers": headers, "timeout": timeout})
        return _FakeResponse(
            content=b"fake-xlsx-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )


@pytest.mark.asyncio
async def test_incoming_handler_preserves_raw_dingtalk_file_name(monkeypatch):
    adapter = DingTalkAdapter(PlatformConfig(enabled=True, extra={"client_id": "cid", "client_secret": "secret"}))
    handler = _IncomingHandler(adapter)

    captured = {}

    async def fake_safe_on_message(chatbot_msg):
        captured["msg"] = chatbot_msg

    tasks = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro):
        task = real_create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(handler, "_safe_on_message", fake_safe_on_message)
    monkeypatch.setattr(adapter, "_spawn_bg", lambda coro: coro.close())
    monkeypatch.setattr(asyncio, "create_task", tracking_create_task)

    callback = SimpleNamespace(
        data={
            "msgId": "msg1",
            "conversationId": "conv1",
            "conversationType": "1",
            "msgtype": "file",
            "senderId": "user1",
            "senderNick": "Kevin",
            "senderStaffId": "staff1",
            "sessionWebhook": "https://api.dingtalk.com/v1.0/test",
            "sessionWebhookExpiredTime": 9999999999999,
            "robotCode": "robot1",
            "content": {
                "downloadCode": "dl-code",
                "fileName": "report.xlsx",
                "fileId": "file-1",
                "spaceId": "space-1",
            },
        }
    )

    await handler.process(callback)
    await asyncio.gather(*tasks)

    assert captured["msg"].file_content.filename == "report.xlsx"


@pytest.mark.asyncio
async def test_resolve_media_codes_downloads_file_to_local_cache_and_sets_excel_mime(monkeypatch):
    adapter = DingTalkAdapter(PlatformConfig(enabled=True, extra={"client_id": "cid", "client_secret": "secret"}))
    adapter._http_client = _FakeHttpClient()

    async def fake_get_access_token():
        return "token-123"

    monkeypatch.setattr(adapter, "_get_access_token", fake_get_access_token)

    message = SimpleNamespace(
        robot_code="robot1",
        image_content=None,
        rich_text_content=None,
        audio_content=None,
        video_content=None,
        voice_content=None,
        file_content=SimpleNamespace(download_code="dl-code", filename="report.xlsx"),
        message_type="file",
    )

    await adapter._resolve_media_codes(message)
    msg_type, media_urls, media_types = adapter._extract_media(message)

    assert msg_type == MessageType.DOCUMENT
    assert len(media_urls) == 1
    assert os.path.isabs(media_urls[0])
    assert os.path.exists(media_urls[0])
    assert media_urls[0].endswith("report.xlsx")
    assert media_types == [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ]
