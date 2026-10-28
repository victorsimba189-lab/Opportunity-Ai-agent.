"""RSS/Atom feeds (free, publisher-sanctioned access to news)."""

from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

from .fetch import request_with_retry

log = logging.getLogger(__name__)


@dataclass
class FeedEntry:
    title: str
    url: str
    summary: str
    published: str | None
    feed: str


def _published(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc).date().isoformat()
    return None


def read_feed(client: httpx.Client, url: str, name: str) -> list[FeedEntry]:
    resp = request_with_retry(client, "GET", url, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"unparseable feed: {parsed.bozo_exception}")
    entries = []
    for e in parsed.entries:
        link = e.get("link")
        if not link:
            continue
        entries.append(FeedEntry(
            title=(e.get("title") or "").strip(),
            url=link,
            summary=_strip_tags(e.get("summary") or "")[:600],
            published=_published(e),
            feed=name,
        ))
    return entries


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
