"""Telegram command handling by polling (run every ~15 minutes from GitHub Actions)."""

from __future__ import annotations

import logging

from .db import DB
from .report import HELP, command_report
from .telegram import Telegram, TelegramError

log = logging.getLogger(__name__)

COMMANDS = [
    ("internships", "Top civil engineering internships"),
    ("courses", "Free startup courses & founder opportunities"),
    ("startups", "Latest startup stories, funding & lessons"),
    ("deadlines", "Deadlines in the next 45 days"),
    ("sources", "Links to check manually"),
    ("status", "Last run, usage and next digest"),
    ("help", "How to use this bot"),
]
KNOWN = {c for c, _ in COMMANDS} | {"start", "programmes"}


def parse_command(text: str | None) -> str | None:
    if not text or not text.startswith("/"):
        return None
    word = text.split()[0][1:].split("@")[0].lower()
    return word if word in KNOWN else "help"


def poll(db: DB, tg: Telegram, allowed_chat_id: str) -> dict:
    """Answer pending commands from the allowed chat, then acknowledge the updates.

    Updates are acknowledged (offset = last id + 1) only after replies are sent, so a crash
    mid-way re-delivers them next run instead of losing them. The offset lives on Telegram's
    side, so this job never needs to write the database.
    """
    updates = tg.get_updates()
    stats = {"updates": len(updates), "replied": 0, "ignored": 0}
    for update in updates:
        msg = update.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        command = parse_command(msg.get("text"))
        if chat_id != str(allowed_chat_id):
            stats["ignored"] += 1
            continue
        try:
            if command is None:
                tg.send_message(chat_id, "Send /help to see what I can do.")
            else:
                chunks = [HELP] if command in ("help", "start") else command_report(db, command)
                for chunk in chunks:
                    tg.send_message(chat_id, chunk)
                log.info("answered /%s", command)
            stats["replied"] += 1
        except TelegramError as exc:
            # Don't let one undeliverable reply block the queue forever.
            log.error("reply failed: %s", exc)
            stats["failed"] = stats.get("failed", 0) + 1
    if updates:
        tg.get_updates(offset=updates[-1]["update_id"] + 1, limit=1)
    return stats


def whoami(tg: Telegram) -> int:
    """Reply to everyone who messaged the bot with their chat id (so it never appears in public logs)."""
    updates = tg.get_updates()
    chats = {}
    for update in updates:
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("id") is not None:
            chats[chat["id"]] = chat
    for chat_id in chats:
        tg.send_message(chat_id, f"Your chat ID is <code>{chat_id}</code>.\n"
                                 "Add it as the TELEGRAM_CHAT_ID repository secret.")
    return len(chats)
