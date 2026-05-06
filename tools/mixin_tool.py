"""Mixin Network Messaging Tool — send messages through Mixin Messenger.

Allows the agent to send text, Markdown, app cards, and other message types
to Mixin users via a Mixin bot's keystore credentials.

Required Credentials
--------------------
A Mixin bot keystore JSON file is required. You can get one by:

  1. Go to https://developers.mixin.one/dashboard
  2. Create or select your bot app
  3. Go to Settings → Keystore → Download
  4. Save the file as ~/.mixin/<name>.keystore.json

The keystore contains these fields:

  {
    "app_id":             "uuid",   // Bot's user_id on Mixin
    "session_id":         "uuid",   // Session identifier
    "server_public_key":  "hex",    // For PIN/TIP encryption
    "session_private_key":"hex",    // EdDSA Ed25519 key for JWT signing
    "spend_private_key":  "hex"     // For signing Safe transactions
  }

For message-only bots, only app_id, session_id, and session_private_key
are needed. The spend_private_key is only for money-moving operations.

Default keystore path: ~/.mixin/mixin-donate.keystore.json
Customize it via the keystore_path parameter.

Message Categories
------------------
  PLAIN_TEXT           - Plain text (no formatting)
  PLAIN_POST           - Markdown (headers, bold, links, etc.) ✅
  PLAIN_IMAGE          - Image (requires attachment upload)
  PLAIN_AUDIO          - Audio
  PLAIN_VIDEO          - Video
  PLAIN_DATA           - File
  PLAIN_STICKER        - Sticker
  PLAIN_CONTACT        - Contact card
  PLAIN_LOCATION       - Map location
  APP_CARD             - Rich app card with icon + description
  APP_BUTTON_GROUP     - Interactive buttons (max 6)

Toolset: mixin
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

try:
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import ed25519
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


# ── Constants ──────────────────────────────────────────────────────────────

MIXIN_API_BASE = "https://api.mixin.one"
DEFAULT_KEYSTORE = os.path.expanduser("~/.mixin/mixin-donate.keystore.json")

SUPPORTED_CATEGORIES = [
    "PLAIN_TEXT", "PLAIN_POST", "PLAIN_IMAGE", "PLAIN_AUDIO",
    "PLAIN_VIDEO", "PLAIN_DATA", "PLAIN_STICKER", "PLAIN_CONTACT",
    "PLAIN_LOCATION", "APP_CARD", "APP_BUTTON_GROUP",
]


# ── Auth ───────────────────────────────────────────────────────────────────

def _build_jwt(ks: dict, method: str, uri: str, body_str: str = "") -> str:
    """Build a Mixin API JWT signed with EdDSA (Ed25519)."""
    now = int(time.time())
    body_hash = hashlib.sha256(body_str.encode()).hexdigest()

    header = {"alg": "EdDSA", "typ": "JWT"}
    payload = {
        "uid": ks["app_id"],
        "sid": ks["session_id"],
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
        "sig": f"{method} {uri} {body_hash}",
        "scp": "FULL",
    }

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()

    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(ks["session_private_key"])
    )
    sig_b64 = _b64url(private_key.sign(signing_input))

    return f"{header_b64}.{payload_b64}.{sig_b64}"


# ── API Client ─────────────────────────────────────────────────────────────

async def _request(method: str, path: str, ks: dict, body: dict = None) -> dict:
    """Make a signed request to the Mixin API."""
    body_str = json.dumps(body, separators=(",", ":")) if body else ""
    jwt = _build_jwt(ks, method.upper(), path, body_str)

    url = f"{MIXIN_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    }

    if not HAS_AIOHTTP:
        return {"error": "aiohttp not installed. Run: pip install aiohttp"}

    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, headers=headers, json=body) as resp:
            return await resp.json()


# ── Keystore ───────────────────────────────────────────────────────────────

def _load_keystore(path: str) -> dict:
    """Load and validate a Mixin bot keystore JSON file."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(
            f"Mixin keystore not found: {p}\n\n"
            "To create one:\n"
            "  1. Go to https://developers.mixin.one/dashboard\n"
            "  2. Create or select your bot app\n"
            "  3. Download the keystore JSON\n"
            "  4. Save it and pass keystore_path= pointing to it"
        )
    with open(p) as f:
        ks = json.load(f)
    required = ["app_id", "session_id", "session_private_key"]
    missing = [k for k in required if k not in ks]
    if missing:
        raise ValueError(
            f"Keystore missing required fields: {missing}\n"
            "A valid keystore must have: app_id, session_id, session_private_key"
        )
    return ks


# ── Tool handlers ──────────────────────────────────────────────────────────

async def mixin_send_message(
    recipient_id: str,
    message_text: str = "",
    category: str = "PLAIN_POST",
    keystore_path: str = DEFAULT_KEYSTORE,
) -> str:
    """Send a message to a Mixin user via your Mixin bot.

    Args:
        recipient_id: Mixin user_id of the recipient
        message_text: Message content (plain text for PLAIN_TEXT/PLAIN_POST;
                      JSON string for APP_CARD, APP_BUTTON_GROUP, etc.)
        category: Message category (default: PLAIN_POST = Markdown)
        keystore_path: Path to Mixin bot keystore JSON
                       (default: ~/.mixin/mixin-donate.keystore.json)

    Returns:
        JSON result from the Mixin API
    """
    if not HAS_CRYPTO:
        return json.dumps({"error": "Missing PyJWT[crypto]. Install: pip install 'PyJWT[crypto]'"})

    try:
        ks = _load_keystore(keystore_path)

        payload = {
            "category": category,
            "recipient_id": recipient_id,
            "message_id": str(uuid.uuid4()),
            "data": base64.b64encode(
                message_text.encode() if isinstance(message_text, str)
                else json.dumps(message_text).encode()
            ).decode(),
        }

        result = await _request("POST", "/messages", ks, payload)
        data = result.get("data", result)
        if "error" in result and result["error"]:
            return json.dumps({
                "error": f"Mixin API error: {result.get('error', {}).get('description', str(result))}",
                "raw": result,
            }, ensure_ascii=False)

        return json.dumps({
            "status": "sent",
            "category": category,
            "recipient_id": recipient_id,
        }, ensure_ascii=False)

    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Mixin send failed: {e}"}, ensure_ascii=False)


async def mixin_send_trending(
    keystore_path: str = DEFAULT_KEYSTORE,
) -> str:
    """Fetch GitHub Trending and send a Markdown summary to the bot creator.

    Scrapes https://github.com/trending, parses the repos, formats them in
    Chinese with GitHub links, and sends via PLAIN_POST (Markdown) to the
    bot's creator (owner) as identified by Mixin's /me API.

    Args:
        keystore_path: Path to Mixin bot keystore JSON

    Returns:
        Status message
    """
    if not HAS_CRYPTO:
        return json.dumps({"error": "Missing PyJWT[crypto]. Install: pip install 'PyJWT[crypto]'"})
    if not HAS_AIOHTTP:
        return json.dumps({"error": "Missing aiohttp. Install: pip install aiohttp"})

    try:
        ks = _load_keystore(keystore_path)

        # 1. Get bot profile → creator_id
        me = await _request("GET", "/me", ks)
        me_data = me.get("data", {})
        creator_id = me_data.get("app", {}).get("creator_id")
        bot_name = me_data.get("full_name", "Bot")

        if not creator_id:
            return json.dumps({"error": "Cannot find bot creator. Your bot may not have an owner."},
                              ensure_ascii=False)

        # 2. Fetch GitHub trending HTML
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://github.com/trending",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                html = await resp.text()

        # 3. Parse repos
        repos = []
        article_pattern = re.compile(r"<article[^>]*>([\s\S]*?)</article>", re.I)
        for article_match in article_pattern.finditer(html):
            article = article_match.group(1)

            h2_match = re.search(
                r"<h2[^>]*>\s*<a[^>]*href=\"(/[^\"]+)\"[^>]*>([\s\S]*?)</a>\s*</h2>",
                article, re.I
            )
            if not h2_match:
                continue

            repo_path = h2_match.group(1)
            full_name = re.sub(r"\s+", " ", h2_match.group(2)).strip()
            repo_url = f"https://github.com{repo_path}"

            desc_match = re.search(r"<p[^>]*>([\s\S]*?)</p>", article, re.I)
            desc = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip() if desc_match else ""

            lang_match = re.search(
                r'itemprop="programmingLanguage"[^>]*>([^<]+)<', article, re.I
            )
            lang = lang_match.group(1).strip() if lang_match else ""

            star_match = re.search(
                r'stargazers"[^>]*>[\s\S]*?(\d[\d,]*)<', article, re.I
            )
            stars = star_match.group(1) if star_match else ""

            today_match = re.search(r"(\d[\d,]*)\s*stars?\s*today", article, re.I)
            today = today_match.group(1) if today_match else ""

            repos.append({
                "name": full_name, "url": repo_url, "desc": desc,
                "lang": lang, "stars": stars, "today": today,
            })

        if not repos:
            return json.dumps({"error": "No trending repos found. GitHub may have rate-limited us."},
                              ensure_ascii=False)

        # 4. Format as Markdown (Chinese)
        date_str = datetime.now().strftime("%Y年%m月%d日")
        md = f"# 📊 GitHub Trending 今日榜单 — {date_str}\n\n"
        for i, r in enumerate(repos[:16], 1):
            md += f"## {i}. [{r['name']}]({r['url']})\n\n"
            md += f"> {r['desc']}\n\n"
            md += f"⭐ **{r['stars']}**"
            if r["lang"]:
                md += f" · 🔤 `{r['lang']}`"
            if r["today"]:
                md += f" · 📈 **+{r['today']}** today"
            md += "\n\n---\n\n"
        md += f"\n_—— 由 Hermes + {bot_name} 机器人自动发送 🤖_"

        # 5. Send as Markdown
        payload = {
            "category": "PLAIN_POST",
            "recipient_id": creator_id,
            "message_id": str(uuid.uuid4()),
            "data": base64.b64encode(md.encode()).decode(),
        }
        result = await _request("POST", "/messages", ks, payload)

        if "error" in result and result.get("error"):
            return json.dumps({
                "error": f"Send failed: {result['error']}",
                "raw": result,
            }, ensure_ascii=False)

        return json.dumps({
            "status": "sent",
            "bot": bot_name,
            "creator_id": creator_id,
            "repo_count": len(repos[:16]),
            "message_length": len(md),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"Failed: {e}"}, ensure_ascii=False)


# ── Health check ──────────────────────────────────────────────────────────

def _check_mixin() -> bool:
    """Tool is available when crypto + aiohttp are installed."""
    return HAS_CRYPTO and HAS_AIOHTTP


def _check_keystore() -> bool:
    """Return True if the default keystore exists."""
    return Path(DEFAULT_KEYSTORE).expanduser().exists()


# ── Register Tools ─────────────────────────────────────────────────────────

registry.register(
    name="mixin_send_message",
    toolset="mixin",
    schema={
        "name": "mixin_send_message",
        "description": (
            "Send a message to a Mixin Messenger user using your Mixin bot.\n\n"
            "Requires a Mixin bot keystore JSON file. Default path:\n"
            "~/.mixin/mixin-donate.keystore.json\n\n"
            "To get a keystore:\n"
            "  1. Go to https://developers.mixin.one/dashboard\n"
            "  2. Create or select your bot app\n"
            "  3. Settings → Download keystore JSON\n\n"
            "Supported categories:\n"
            "  PLAIN_TEXT  - Plain text (no formatting)\n"
            "  PLAIN_POST  - Markdown (headers, bold, links) ✅\n"
            "  APP_CARD    - Rich app card\n"
            "  APP_BUTTON_GROUP - Interactive buttons\n"
            "  PLAIN_STICKER, PLAIN_CONTACT, PLAIN_LOCATION\n\n"
            "For images/audio/video/files, upload first then provide attachment_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient_id": {
                    "type": "string",
                    "description": "Mixin user_id of the recipient. "
                                   "The bot creator's ID can be found via mixin_send_trending."
                },
                "message_text": {
                    "type": "string",
                    "description": "Message content. For PLAIN_TEXT/PLAIN_POST just the text. "
                                   "For APP_CARD pass JSON: {\"app_id\":\"...\",\"icon_url\":\"...\","
                                   "\"title\":\"...\",\"description\":\"...\",\"action\":\"...\"}. "
                                   "For APP_BUTTON_GROUP pass JSON array of "
                                   "{\"label\":\"...\",\"color\":\"#hex\",\"action\":\"...\"}."
                },
                "category": {
                    "type": "string",
                    "enum": SUPPORTED_CATEGORIES,
                    "description": "Message category. Default: PLAIN_POST (Markdown). "
                                   "PLAIN_TEXT renders as-is with no formatting."
                },
                "keystore_path": {
                    "type": "string",
                    "description": "Path to Mixin bot keystore JSON file. "
                                   "Default: ~/.mixin/mixin-donate.keystore.json",
                },
            },
            "required": ["recipient_id"],
        },
    },
    handler=lambda args, **kw: mixin_send_message(
        recipient_id=args.get("recipient_id", ""),
        message_text=args.get("message_text", ""),
        category=args.get("category", "PLAIN_POST"),
        keystore_path=args.get("keystore_path", DEFAULT_KEYSTORE),
    ),
    check_fn=_check_mixin,
    requires_env=[],
    requires_credential_files=[DEFAULT_KEYSTORE],
)

registry.register(
    name="mixin_send_trending",
    toolset="mixin",
    schema={
        "name": "mixin_send_trending",
        "description": (
            "Fetch GitHub trending repositories and send a Markdown summary "
            "to your Mixin bot's creator/owner via PLAIN_POST (Markdown).\n\n"
            "Automatically: scrapes trending, formats in Chinese with GitHub "
            "links, identifies the bot creator via Mixin API, and sends."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keystore_path": {
                    "type": "string",
                    "description": "Path to Mixin bot keystore JSON file. "
                                   "Default: ~/.mixin/mixin-donate.keystore.json",
                },
            },
        },
    },
    handler=lambda args, **kw: mixin_send_trending(
        keystore_path=args.get("keystore_path", DEFAULT_KEYSTORE),
    ),
    check_fn=_check_keystore,
    requires_env=[],
    requires_credential_files=[DEFAULT_KEYSTORE],
)
