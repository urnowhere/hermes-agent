"""
Platform adapters for messaging integrations.

Each adapter handles:
- Receiving messages from a platform
- Sending messages/responses back
- Platform-specific authentication
- Message formatting and media handling
"""

from .base import BasePlatformAdapter, MessageEvent, SendResult
from .msgraph_webhook import MSGraphWebhookAdapter
from .qqbot import QQAdapter
from .yuanbao import YuanbaoAdapter
from .teams import TeamsAdapter

__all__ = [
    "BasePlatformAdapter",
    "MessageEvent",
    "MSGraphWebhookAdapter",
    "SendResult",
    "QQAdapter",
    "YuanbaoAdapter",
    "TeamsAdapter",
]
