import logging
import time
from typing import Any, Callable, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RETRY_CONFIG = {
    "max_attempts": 3,
    "base_delay": 0.5,
    "backoff_factor": 2.0,
    "max_delay": 10.0,
}

RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
    httpx.TransportError,
)

try:
    import ssl
    RETRYABLE_ERRORS += (ssl.SSLError,)
except Exception:
    pass


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, RETRYABLE_ERRORS)


def retryable_http_call(
    request_fn: Callable[[], httpx.Response],
    max_attempts: int = DEFAULT_RETRY_CONFIG["max_attempts"],
    base_delay: float = DEFAULT_RETRY_CONFIG["base_delay"],
    backoff_factor: float = DEFAULT_RETRY_CONFIG["backoff_factor"],
    max_delay: float = DEFAULT_RETRY_CONFIG["max_delay"],
    logger_extra: Optional[Dict[str, Any]] = None,
) -> httpx.Response:
    extra = logger_extra or {}
    attempt = 0
    delay = base_delay

    while True:
        attempt += 1
        try:
            return request_fn()
        except Exception as exc:
            if not _is_retryable_exception(exc) or attempt >= max_attempts:
                logger.warning(
                    "HTTP call failed (non-retryable or max attempts reached).",
                    exc_info=True,
                    extra=extra,
                )
                raise
            sleep_time = min(delay, max_delay)
            logger.warning(
                f"HTTP call failed with retryable error ({type(exc).__name__}): {exc}. "
                f"Retry attempt {attempt + 1}/{max_attempts} in {sleep_time:.2f}s.",
                exc_info=True,
                extra=extra,
            )
            time.sleep(sleep_time)
            delay *= backoff_factor


def retryable_post(
    client: httpx.Client,
    url: str,
    json: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    **retry_kwargs,
) -> httpx.Response:
    def _post() -> httpx.Response:
        kwargs = {}
        if data is not None:
            kwargs["data"] = data
        elif json is not None:
            kwargs["json"] = json
        if headers is not None:
            kwargs["headers"] = headers
        if timeout is not None:
            kwargs["timeout"] = timeout
        return client.post(url, **kwargs)

    return retryable_http_call(_post, **retry_kwargs)


def retryable_get(
    client: httpx.Client,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    **retry_kwargs,
) -> httpx.Response:
    def _get() -> httpx.Response:
        kwargs = {}
        if params is not None:
            kwargs["params"] = params
        if headers is not None:
            kwargs["headers"] = headers
        if timeout is not None:
            kwargs["timeout"] = timeout
        return client.get(url, **kwargs)
    return retryable_http_call(_get, **retry_kwargs)
