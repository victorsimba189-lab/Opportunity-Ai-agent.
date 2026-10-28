"""Minimal Telegram Bot API client with retry handling. Never logs the bot token."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4096


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self._base = f"https://api.telegram.org/bot{token}/"
        self.client = client or httpx.Client(timeout=30)

    def call(self, method: str, attempts: int = 4, **params) -> object:
        delay = 2.0
        for attempt in range(1, attempts + 1):
            try:
                resp = self.client.post(self._base + method, json=params)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise TelegramError(f"{method}: network error {type(exc).__name__}") from None
                time.sleep(delay)
                delay *= 2
                continue
            try:
                body = resp.json()
            except ValueError:
                body = {"ok": False, "description": f"HTTP {resp.status_code}"}
            if body.get("ok"):
                return body.get("result")
            if resp.status_code == 429 and attempt < attempts:
                wait = float(body.get("parameters", {}).get("retry_after", delay))
                time.sleep(min(wait, 60))
                continue
            if resp.status_code >= 500 and attempt < attempts:
                time.sleep(delay)
                delay *= 2
                continue
            raise TelegramError(f"{method}: {resp.status_code} {body.get('description', '')}")
        raise TelegramError(f"{method}: retries exhausted")

    def send_message(self, chat_id: str | int, text: str, html: bool = True) -> int:
        params = {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}}
        if html:
            params["parse_mode"] = "HTML"
        try:
            result = self.call("sendMessage", **params)
        except TelegramError as exc:
            if html and "can't parse entities" in str(exc):
                log.warning("Telegram rejected HTML formatting; resending as plain text")
                return self.send_message(chat_id, _strip_html(text), html=False)
            raise
        return int(result["message_id"])

    def get_updates(self, offset: int | None = None, limit: int = 100) -> list[dict]:
        params: dict = {"timeout": 0, "limit": limit, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return list(self.call("getUpdates", **params) or [])

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])

    def delete_webhook(self) -> None:
        self.call("deleteWebhook", drop_pending_updates=False)


def _strip_html(text: str) -> str:
    import html
    import re

    text = re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2 (\1)", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))
