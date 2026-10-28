"""Daily digest delivery with duplicate-notification prevention.

One digest per IST date (UNIQUE kind+date). The digest is stored before sending and each chunk's
message id is recorded as it goes, so a retried or fallback run only sends what is still missing.
"""

from __future__ import annotations

import json
import logging

from . import timeutil
from .db import DB
from .report import build_digest
from .telegram import Telegram

log = logging.getLogger(__name__)


def deliver_daily(db: DB, tg: Telegram | None, chat_id: str | None, *, dry_run: bool = False,
                  force: bool = False) -> dict:
    today = timeutil.today_ist().isoformat()
    existing = db.report("daily", today)

    if existing and existing["status"] == "sent" and not force:
        log.info("today's digest (%s) was already sent at %s — nothing to do", today, existing["sent_at"])
        return {"status": "already_sent"}

    if dry_run:
        chunks, item_ids, _ = build_digest(db)
        return {"status": "dry_run", "chunks": chunks, "items": len(item_ids)}

    if tg is None or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required to send")

    if existing and existing["status"] == "pending" and not force:
        report_id = existing["id"]
        chunks = json.loads(existing["chunks"])
        message_ids = json.loads(existing["message_ids"])
        item_ids = json.loads(existing["item_ids"])
        pairs = [tuple(p) for p in json.loads(existing["reminders"])]
        log.info("resuming partially sent digest: %d of %d parts already delivered",
                 sum(m is not None for m in message_ids), len(chunks))
    else:
        if existing:
            db.delete_report(existing["id"])
        chunks, item_ids, pairs = build_digest(db)
        report_id = db.create_report("daily", today, chunks, item_ids, [list(p) for p in pairs])
        message_ids = [None] * len(chunks)

    for i, chunk in enumerate(chunks):
        if message_ids[i] is not None:
            continue
        message_ids[i] = tg.send_message(chat_id, chunk)
        db.set_report_message_ids(report_id, message_ids)

    db.mark_notified(item_ids)
    db.mark_reminders(pairs)
    db.finish_report(report_id)
    log.info("digest sent: %d message(s), %d item(s)", len(chunks), len(item_ids))
    return {"status": "sent", "messages": len(chunks), "items": len(item_ids)}
