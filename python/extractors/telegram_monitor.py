"""
extractors/telegram_monitor.py
────────────────────────────────
Monitors public Telegram channels for intelligence signals.

Uses two approaches depending on available credentials:
  1. Telethon (full MTProto client) — if TELEGRAM_API_ID + TELEGRAM_API_HASH set
  2. Telegram Bot API public channel scraper — no auth needed, limited to
     channels that have @username, returns only the most recent posts

The extractor degrades gracefully: if neither method is configured it
returns an empty list with a warning and does NOT raise.

Environment variables:
  TELEGRAM_API_ID       — from https://my.telegram.org/apps (optional)
  TELEGRAM_API_HASH     — from https://my.telegram.org/apps (optional)
  TELEGRAM_BOT_TOKEN    — Bot API token from @BotFather (optional, for method 2)
  TELEGRAM_CHANNELS     — comma-separated list of @usernames (e.g. "@bbcnews,@cnn")

fetch() params:
    channels    list  override env channel list
    limit       int   max messages per channel (default 20)
    query       str   optional keyword filter
"""

import os
import time
from typing import Any, Dict, List, Optional

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso

_BOT_API = "https://api.telegram.org/bot{token}/getUpdates"

# Default public intelligence/news channels to monitor
DEFAULT_CHANNELS = [
    "@bbcnews",
    "@cnn",
    "@reuters",
    "@aljazeera",
    "@rt_news",
    "@disclosetv",       # breaking news aggregator
    "@IntelSlava",       # conflict intelligence
    "@TRTWorldNow",
    "@ReutersAgency",
]


class TelegramExtractor(BaseExtractor):
    """
    Monitors public Telegram channels for narrative-relevant content.

    Falls back to an empty list without error if no credentials configured.
    This is intentional — Telegram monitoring is optional.
    """

    source_name = "telegram"

    def __init__(self) -> None:
        super().__init__()
        self._api_id    = os.getenv("TELEGRAM_API_ID", "")
        self._api_hash  = os.getenv("TELEGRAM_API_HASH", "")
        self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._channels  = [
            c.strip()
            for c in os.getenv("TELEGRAM_CHANNELS", ",".join(DEFAULT_CHANNELS)).split(",")
            if c.strip()
        ]

    @property
    def _configured(self) -> bool:
        return bool(self._bot_token or (self._api_id and self._api_hash))

    def fetch(
        self,
        channels: Optional[List[str]] = None,
        limit: int = 20,
        query: str = "",
        **_,
    ) -> List[Dict]:
        if not self._configured:
            self.logger.debug(
                "Telegram: no credentials configured — skipping. "
                "Set TELEGRAM_BOT_TOKEN or TELEGRAM_API_ID/HASH to enable."
            )
            return []

        active_channels = channels or self._channels
        results: List[Dict] = []

        if self._bot_token:
            results = self._fetch_via_bot_api(active_channels, limit, query)
        elif self._api_id and self._api_hash:
            results = self._fetch_via_telethon(active_channels, limit, query)

        return results

    def _fetch_via_bot_api(
        self,
        channels: List[str],
        limit: int,
        query: str,
    ) -> List[Dict]:
        """
        Use the Bot API to read forwarded updates from channels the bot is in.
        Limited: the bot must be added to each channel as an admin.
        Best effort — silently skips channels that return errors.
        """
        results: List[Dict] = []
        query_lower = query.lower() if query else ""

        try:
            resp = requests.get(
                _BOT_API.format(token=self._bot_token),
                params={"limit": 100, "timeout": 5},
                timeout=10,
            )
            if resp.status_code != 200:
                self.logger.warning("Telegram Bot API: %d %s", resp.status_code, resp.text[:80])
                return []

            updates = resp.json().get("result", [])
            for update in updates:
                msg = update.get("channel_post") or update.get("message")
                if not msg:
                    continue

                text = msg.get("text") or msg.get("caption") or ""
                if query_lower and query_lower not in text.lower():
                    continue

                chat = msg.get("chat", {})
                channel_title = chat.get("title") or chat.get("username") or "unknown"
                username      = chat.get("username", "")

                results.append({
                    "_channel":    f"@{username}" if username else channel_title,
                    "_chat_title": channel_title,
                    "message_id":  msg.get("message_id"),
                    "text":        text,
                    "date":        msg.get("date"),
                    "views":       msg.get("views"),
                    "forwards":    msg.get("forward_count"),
                    "url":         f"https://t.me/{username}/{msg.get('message_id')}" if username else None,
                })

                if len(results) >= limit:
                    break

        except Exception as exc:
            self.logger.error("Telegram Bot API fetch failed: %s", exc)

        return results

    def _fetch_via_telethon(
        self,
        channels: List[str],
        limit: int,
        query: str,
    ) -> List[Dict]:
        """
        Use Telethon MTProto client for full access to public channels.
        Requires interactive login once (session stored in telegram.session).
        """
        try:
            from telethon.sync import TelegramClient
            from telethon.errors import FloodWaitError
        except ImportError:
            self.logger.warning("telethon not installed. Run: pip install telethon")
            return []

        query_lower = query.lower() if query else ""
        results: List[Dict] = []

        try:
            with TelegramClient("telegram_vision", self._api_id, self._api_hash) as client:
                for channel in channels:
                    if len(results) >= limit:
                        break
                    try:
                        for msg in client.iter_messages(channel, limit=limit // max(len(channels), 1)):
                            text = msg.text or ""
                            if query_lower and query_lower not in text.lower():
                                continue

                            results.append({
                                "_channel": channel,
                                "_chat_title": getattr(msg.chat, "title", channel),
                                "message_id": msg.id,
                                "text": text,
                                "date": int(msg.date.timestamp()) if msg.date else None,
                                "views": getattr(msg, "views", None),
                                "forwards": getattr(msg, "forwards", None),
                                "url": f"https://t.me/{channel.lstrip('@')}/{msg.id}",
                            })
                    except FloodWaitError as e:
                        self.logger.warning("Telegram FloodWait on %s: sleeping %ds", channel, e.seconds)
                        time.sleep(min(e.seconds, 60))
                    except Exception as exc:
                        self.logger.warning("Telegram channel %s failed: %s", channel, exc)

        except Exception as exc:
            self.logger.error("Telethon client error: %s", exc)

        return results

    def normalize(self, item: Any) -> VisionEvent:
        channel   = item.get("_channel", "telegram")
        title_raw = item.get("_chat_title", channel)
        text      = item.get("text") or ""
        msg_id    = str(item.get("message_id", ""))
        url       = item.get("url")
        ts        = to_iso(item.get("date"))
        views     = item.get("views")
        forwards  = item.get("forwards")

        # Use first line of text as title if no other title
        title = text.split("\n")[0][:120] if text else f"Post in {title_raw}"
        source_key = f"telegram_{channel.lstrip('@').lower()}"

        return VisionEvent(
            event_id   = stable_id(source_key, msg_id or text[:64]),
            source     = "telegram",
            source_id  = msg_id,
            event_type = "social",
            title      = title,
            description= text[:500],
            body       = text,
            url        = url,
            language   = "en",
            author     = title_raw,
            timestamp  = ts,
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = None,
            sentiment  = None,
            tags       = ["telegram", channel.lstrip("@")],
            extras     = {
                "channel":  channel,
                "views":    views,
                "forwards": forwards,
            },
            raw = item,
        )

    def health(self) -> Dict:
        if not self._configured:
            return {"source": self.source_name, "status": "disabled",
                    "detail": "No credentials configured"}
        return {"source": self.source_name, "status": "configured",
                "channels": len(self._channels)}
