"""URL fetcher — download web pages with SSRF protection.

Security constraints:
* Timeout: 10 seconds
* Max HTML size: 5 MB
* Blocked: localhost, 127.0.0.0/8, 0.0.0.0, ::1, and all RFC-1918 private
  ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16).
* Only http:// and https:// schemes accepted.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FETCH_TIMEOUT_SECONDS = 10
MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique-local
    ipaddress.ip_network("fe80::/10"),  # link-local v6
]

_USER_AGENT = (
    "Mozilla/5.0 (compatible; HermesAgent/1.0; +https://github.com/NousResearch/hermes-agent)"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Raised when a URL cannot be fetched for a known reason."""


def fetch_url(url: str) -> str:
    """Fetch *url* and return the response body as a string.

    Raises :class:`FetchError` with a human-readable message on failure.
    """
    # 1. Validate scheme --------------------------------------------------
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"Unsupported URL scheme '{parsed.scheme}'. Only http:// and https:// are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise FetchError(f"Invalid URL: could not determine hostname from '{url}'.")

    # 2. SSRF protection — resolve hostname and check against blocklist ---
    _check_ssrf(hostname)

    # 3. Fetch ------------------------------------------------------------
    try:
        resp = requests.get(
            url,
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=True,
            stream=True,
        )
        resp.raise_for_status()

        # Enforce size limit while streaming
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_CONTENT_BYTES:
                resp.close()
                raise FetchError(
                    f"Response exceeded maximum size ({MAX_CONTENT_BYTES // (1024 * 1024)} MB). "
                    "The document is too large to process."
                )
            chunks.append(chunk)

        raw_bytes = b"".join(chunks)

        # Attempt charset detection from the Content-Type header
        encoding = resp.encoding or "utf-8"
        try:
            return raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return raw_bytes.decode("utf-8", errors="replace")

    except FetchError:
        raise
    except requests.exceptions.Timeout:
        raise FetchError(f"Request to '{url}' timed out after {FETCH_TIMEOUT_SECONDS}s.")
    except requests.exceptions.ConnectionError as exc:
        raise FetchError(f"Could not connect to '{url}': {exc}")
    except requests.exceptions.HTTPError as exc:
        raise FetchError(f"HTTP error fetching '{url}': {exc}")
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"Failed to fetch '{url}': {exc}")


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------


def _check_ssrf(hostname: str) -> None:
    """Resolve *hostname* and raise :class:`FetchError` if it points at a
    private/loopback address."""
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError(f"Could not resolve hostname '{hostname}'.")

    for family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if addr in network:
                raise FetchError(
                    f"Access to '{hostname}' ({ip_str}) is blocked — "
                    "internal/private network addresses are not allowed."
                )
