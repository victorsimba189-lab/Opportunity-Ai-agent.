"""Time helpers. All scheduling decisions are made in Asia/Kolkata (IST, UTC+5:30, no DST)."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

IST = ZoneInfo("Asia/Kolkata")

# Tests replace this to freeze time.
_clock = lambda: datetime.now(timezone.utc)  # noqa: E731


def now_utc() -> datetime:
    return _clock()


def now_ist() -> datetime:
    return now_utc().astimezone(IST)


def today_ist() -> date:
    return now_ist().date()


def iso_now() -> str:
    return now_utc().replace(microsecond=0).isoformat()


def seconds_until_ist(hour: int, minute: int = 0) -> float:
    """Seconds from now until today's hour:minute IST (negative if already past)."""
    now = now_ist()
    target = datetime.combine(now.date(), time(hour, minute), tzinfo=IST)
    return (target - now).total_seconds()


def parse_date(value: str | None) -> date | None:
    """Parse a date written by the LLM (ISO preferred) or found in a feed.

    ISO strings are parsed strictly first: dateutil's dayfirst mode misreads
    2026-11-05 as 11 May, and Indian sources write dates day-first.
    """
    if not value:
        return None
    value = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    try:
        return dateparser.parse(value, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def days_until(d: date | None) -> int | None:
    if d is None:
        return None
    return (d - today_ist()).days


def fmt_date(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else "not stated"


def within_days(published: str | None, days: int) -> bool:
    d = parse_date(published)
    return d is None or d >= today_ist() - timedelta(days=days)


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
