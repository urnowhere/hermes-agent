"""Tests for coroutine cleanup in environments.patches helpers."""

import gc
import warnings
from unittest.mock import MagicMock, patch

import pytest


class TestAsyncWorker:
    def test_scheduler_failure_closes_coroutine(self):
        from environments.patches import _AsyncWorker

        worker = _AsyncWorker()
        worker._loop = MagicMock()
        worker._loop.is_closed.return_value = False

        async def _sample():
            return "ok"

        coro = _sample()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with patch("environments.patches.asyncio.run_coroutine_threadsafe", side_effect=RuntimeError("scheduler down")):
                with pytest.raises(RuntimeError, match="scheduler down"):
                    worker.run_coroutine(coro)
            gc.collect()

        assert coro.cr_frame is None
        runtime_warnings = [
            w for w in caught
            if issubclass(w.category, RuntimeWarning)
            and "was never awaited" in str(w.message)
        ]
        assert runtime_warnings == []

    def test_missing_loop_closes_coroutine(self):
        from environments.patches import _AsyncWorker

        worker = _AsyncWorker()

        async def _sample():
            return "ok"

        coro = _sample()
        with pytest.raises(RuntimeError, match="not running"):
            worker.run_coroutine(coro)
        assert coro.cr_frame is None
