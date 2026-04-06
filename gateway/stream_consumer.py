"""Gateway streaming consumer — bridges sync agent callbacks to async platform delivery.

The agent fires stream_delta_callback(text) synchronously from its worker thread.
GatewayStreamConsumer:
  1. Receives deltas via on_delta() (thread-safe, sync)
  2. Queues them to an asyncio task via queue.Queue
  3. The async run() task buffers, rate-limits, and progressively edits
     a single message on the target platform

Design: Uses the edit transport (send initial message, then editMessageText).
This is universally supported across Telegram, Discord, and Slack.

Credit: jobless0x (#774, #1312), OutThisLife (#798), clicksingh (#697).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("gateway.stream_consumer")

# Sentinel to signal the stream is complete
_DONE = object()


@dataclass
class StreamConsumerConfig:
    """Runtime config for a single stream consumer instance."""
    edit_interval: float = 0.3
    buffer_threshold: int = 40
    cursor: str = " ▉"


class GatewayStreamConsumer:
    """Async consumer that progressively edits a platform message with streamed tokens.

    Usage::

        consumer = GatewayStreamConsumer(adapter, chat_id, config, metadata=metadata)
        # Pass consumer.on_delta as stream_delta_callback to AIAgent
        agent = AIAgent(..., stream_delta_callback=consumer.on_delta)
        # Start the consumer as an asyncio task
        task = asyncio.create_task(consumer.run())
        # ... run agent in thread pool ...
        consumer.finish()  # signal completion
        await task         # wait for final edit
    """

    def __init__(
        self,
        adapter: Any,
        chat_id: str,
        config: Optional[StreamConsumerConfig] = None,
        metadata: Optional[dict] = None,
    ):
        self.adapter = adapter
        self.chat_id = chat_id
        self.cfg = config or StreamConsumerConfig()
        self.metadata = metadata
        self._queue: queue.Queue = queue.Queue()
        self._accumulated = ""
        self._message_id: Optional[str] = None
        self._already_sent = False
        self._edit_supported = True  # Disabled on first edit failure (Signal/Email/HA)
        self._last_edit_time = 0.0
        self._last_sent_text = ""   # Track last-sent text to skip redundant edits

    @property
    def already_sent(self) -> bool:
        """True if at least one message was sent/edited — signals the base
        adapter to skip re-sending the final response."""
        return self._already_sent

    def on_delta(self, text: str) -> None:
        """Thread-safe callback — called from the agent's worker thread."""
        if text:
            self._queue.put(text)

    def finish(self) -> None:
        """Signal that the stream is complete."""
        self._queue.put(_DONE)

    async def run(self) -> None:
        """Async task that drains the queue and edits the platform message."""
        # Platform message length limit — leave room for cursor, formatting,
        # and MDv2 escape character inflation (special chars each add 1 byte).
        # Use 75% of the raw limit as a conservative safe ceiling.
        _raw_limit = getattr(self.adapter, "MAX_MESSAGE_LENGTH", 4096)
        _safe_limit = max(500, int(_raw_limit * 0.75))

        try:
            while True:
                # Drain all available items from the queue
                got_done = False
                while True:
                    try:
                        item = self._queue.get_nowait()
                        if item is _DONE:
                            got_done = True
                            break
                        self._accumulated += item
                    except queue.Empty:
                        break

                # Decide whether to flush an edit
                now = time.monotonic()
                elapsed = now - self._last_edit_time
                should_edit = (
                    got_done
                    or (elapsed >= self.cfg.edit_interval
                        and len(self._accumulated) > 0)
                    or len(self._accumulated) >= self.cfg.buffer_threshold
                )

                if should_edit and self._accumulated:
                    # Split overflow: if accumulated text exceeds the platform
                    # limit, finalize the current message and start a new one.
                    while (
                        len(self._accumulated) > _safe_limit
                        and self._message_id is not None
                    ):
                        split_at = self._accumulated.rfind(\"\\n\", 0, _safe_limit)
                        if split_at < _safe_limit // 2:
                            split_at = _safe_limit
                        chunk = self._accumulated[:split_at]
                        # Bypass _edit_supported — must cleanly finalize this
                        # chunk without cursor regardless of earlier edit failures.
                        # Retry once on exception to cover transient failures.
                        for _attempt in range(2):
                            try:
                                await self.adapter.edit_message(
                                    chat_id=self.chat_id,
                                    message_id=self._message_id,
                                    content=chunk,
                                    metadata=self.metadata,
                                )
                                break
                            except Exception:
                                if _attempt == 0:
                                    await asyncio.sleep(0.5)
                                # second attempt failed — cursor stays in this chunk
                        self._accumulated = self._accumulated[split_at:].lstrip(\"\\n\")
                        self._message_id = None
                        self._last_sent_text = \"\"

                    if got_done:
                        # Final delivery — strip cursor, bypass _edit_supported
                        # so a disabled flag from a failed overflow edit doesn't
                        # leave the cursor stuck in the last message.
                        if self._message_id is not None:
                            for _attempt in range(2):
                                try:
                                    await self.adapter.edit_message(
                                        chat_id=self.chat_id,
                                        message_id=self._message_id,
                                        content=self._accumulated,
                                        metadata=self.metadata,
                                    )
                                    self._already_sent = True
                                    break
                                except Exception:
                                    if _attempt == 0:
                                        await asyncio.sleep(0.5)
                        else:
                            result = await self.adapter.send(
                                chat_id=self.chat_id,
                                content=self._accumulated,
                                metadata=self.metadata,
                            )
                            if result.success:
                                self._already_sent = True
                        return

                    display_text = self._accumulated + self.cfg.cursor
                    await self._send_or_edit(display_text)
                    self._last_edit_time = time.monotonic()

                if got_done:
                    return

                await asyncio.sleep(0.05)  # Small yield to not busy-loop

        except asyncio.CancelledError:
            # Best-effort final edit on cancellation
            if self._accumulated and self._message_id:
                try:
                    await self._send_or_edit(self._accumulated)
                except Exception:
                    pass
        except Exception as e:
            logger.error("Stream consumer error: %s", e)

    # Pattern to strip MEDIA:<path> tags (including optional surrounding quotes).
    # Matches the simple cleanup regex used by the non-streaming path in
    # gateway/platforms/base.py for post-processing.
    _MEDIA_RE = re.compile(r'''[`"']?MEDIA:\s*\S+[`"']?''')

    @staticmethod
    def _clean_for_display(text: str) -> str:
        """Strip MEDIA: directives and internal markers from text before display.

        The streaming path delivers raw text chunks that may include
        ``MEDIA:<path>`` tags and ``[[audio_as_voice]]`` directives meant for
        the platform adapter's post-processing.  The actual media files are
        delivered separately via ``_deliver_media_from_response()`` after the
        stream finishes — we just need to hide the raw directives from the
        user.
        """
        if "MEDIA:" not in text and "[[audio_as_voice]]" not in text:
            return text
        cleaned = text.replace("[[audio_as_voice]]", "")
        cleaned = GatewayStreamConsumer._MEDIA_RE.sub("", cleaned)
        # Collapse excessive blank lines left behind by removed tags
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        # Strip trailing whitespace/newlines but preserve leading content
        return cleaned.rstrip()

    async def _send_or_edit(self, text: str) -> None:
        """Send or edit the streaming message."""
        # Strip MEDIA: directives so they don't appear as visible text.
        # Media files are delivered as native attachments after the stream
        # finishes (via _deliver_media_from_response in gateway/run.py).
        text = self._clean_for_display(text)
        if not text.strip():
            return
        try:
            if self._message_id is not None:
                if self._edit_supported:
                    # Skip if text is identical to what we last sent
                    if text == self._last_sent_text:
                        return
                    # Edit existing message — pass metadata so overflow chunks
                    # preserve the thread_id and land in the correct topic.
                    result = await self.adapter.edit_message(
                        chat_id=self.chat_id,
                        message_id=self._message_id,
                        content=text,
                        metadata=self.metadata,
                    )
                    if result.success:
                        self._already_sent = True
                        self._last_sent_text = text
                    else:
                        # If an edit fails mid-stream (especially Telegram flood control),
                        # stop progressive edits and let the normal final send path deliver
                        # the complete answer instead of leaving the user with a partial.
                        logger.debug("Edit failed, disabling streaming for this adapter")
                        self._edit_supported = False
                        self._already_sent = False
                else:
                    # Editing not supported — skip intermediate updates.
                    # The final response will be sent by the normal path.
                    pass
            else:
                # First message — send new
                result = await self.adapter.send(
                    chat_id=self.chat_id,
                    content=text,
                    metadata=self.metadata,
                )
                if result.success and result.message_id:
                    self._message_id = result.message_id
                    self._already_sent = True
                    self._last_sent_text = text
                else:
                    # Initial send failed — disable streaming for this session
                    self._edit_supported = False
        except Exception as e:
            logger.error("Stream send/edit error: %s", e)
