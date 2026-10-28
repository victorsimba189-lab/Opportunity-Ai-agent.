"""SQLite storage: opportunities, sources, search cache, runs, reports and notification history."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from . import timeutil

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    agent         TEXT NOT NULL,
    fingerprint   TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    org           TEXT,
    url           TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    location      TEXT,
    deadline      TEXT,
    start_date    TEXT,
    eligibility_intl TEXT,
    cost          TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    status_reason TEXT,
    verification  TEXT,
    relevance     INTEGER DEFAULT 0,
    score         REAL DEFAULT 0,
    published_at  TEXT,
    sources_count INTEGER DEFAULT 1,
    data          TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    notified_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_agent_status ON items(agent, status);

CREATE TABLE IF NOT EXISTS seen_urls (
    canonical_url TEXT PRIMARY KEY,
    agent         TEXT NOT NULL,
    outcome       TEXT,
    first_seen    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    url          TEXT PRIMARY KEY,
    agent        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    content_hash TEXT,
    last_status  TEXT,
    last_checked TEXT,
    last_changed TEXT
);

CREATE TABLE IF NOT EXISTS search_cache (
    query_key TEXT NOT NULL,
    day       TEXT NOT NULL,
    results   TEXT NOT NULL,
    PRIMARY KEY (query_key, day)
);

CREATE TABLE IF NOT EXISTS usage (
    day    TEXT NOT NULL,
    kind   TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    agent       TEXT NOT NULL,
    ist_date    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    stats       TEXT,
    errors      TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    ist_date    TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    sent_at     TEXT,
    chunks      TEXT NOT NULL,
    message_ids TEXT NOT NULL,
    item_ids    TEXT NOT NULL,
    reminders   TEXT NOT NULL DEFAULT '[]',
    UNIQUE (kind, ist_date)
);

CREATE TABLE IF NOT EXISTS reminders (
    item_id   INTEGER NOT NULL,
    threshold INTEGER NOT NULL,
    sent_on   TEXT NOT NULL,
    PRIMARY KEY (item_id, threshold)
);
"""

ITEM_COLUMNS = (
    "agent", "fingerprint", "title", "org", "url", "source_url", "canonical_url", "location",
    "deadline", "start_date", "eligibility_intl", "cost", "status", "status_reason",
    "verification", "relevance", "score", "published_at", "data",
)


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Worker threads (LLM usage accounting) share this connection; every access holds the lock.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.commit()
            self.conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def _many(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    # ---- items -------------------------------------------------------------

    def item(self, item_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM items WHERE id = ?", (item_id,))

    def item_by_fingerprint(self, fingerprint: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM items WHERE fingerprint = ?", (fingerprint,))

    def items(self, agent: str, statuses: tuple[str, ...] | None = ("active",)) -> list[sqlite3.Row]:
        if statuses is None:
            return self._all("SELECT * FROM items WHERE agent = ?", (agent,))
        marks = ",".join("?" * len(statuses))
        return self._all(f"SELECT * FROM items WHERE agent = ? AND status IN ({marks})", (agent, *statuses))

    def insert_item(self, item: dict) -> int:
        now = timeutil.iso_now()
        values = [json.dumps(item[c], ensure_ascii=False) if c == "data" else item.get(c) for c in ITEM_COLUMNS]
        cur = self._exec(
            f"INSERT INTO items ({', '.join(ITEM_COLUMNS)}, first_seen, last_seen) "
            f"VALUES ({', '.join('?' * len(ITEM_COLUMNS))}, ?, ?)",
            [*values, now, now],
        )
        return int(cur.lastrowid)

    def update_item(self, item_id: int, **fields: Any) -> None:
        if "data" in fields and not isinstance(fields["data"], str):
            fields["data"] = json.dumps(fields["data"], ensure_ascii=False)
        fields["last_seen"] = timeutil.iso_now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE items SET {sets} WHERE id = ?", [*fields.values(), item_id])

    def expire_items(self, today_iso: str) -> int:
        cur = self._exec(
            "UPDATE items SET status = 'expired', status_reason = 'deadline passed' "
            "WHERE status = 'active' AND deadline IS NOT NULL AND deadline < ?",
            (today_iso,),
        )
        return cur.rowcount

    def mark_notified(self, item_ids: Iterable[int]) -> None:
        now = timeutil.iso_now()
        self._many(
            "UPDATE items SET notified_at = ? WHERE id = ? AND notified_at IS NULL",
            [(now, i) for i in item_ids],
        )

    def status_counts(self, agent: str) -> dict[str, int]:
        rows = self._all("SELECT status, COUNT(*) AS n FROM items WHERE agent = ? GROUP BY status", (agent,))
        return {r["status"]: r["n"] for r in rows}

    # ---- seen urls / sources ------------------------------------------------

    def is_seen(self, canonical_url: str) -> bool:
        return self._one("SELECT 1 FROM seen_urls WHERE canonical_url = ?", (canonical_url,)) is not None

    def mark_seen(self, canonical_url: str, agent: str, outcome: str) -> None:
        self._exec(
            "INSERT INTO seen_urls (canonical_url, agent, outcome, first_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(canonical_url) DO UPDATE SET outcome = excluded.outcome",
            (canonical_url, agent, outcome, timeutil.iso_now()),
        )

    def source(self, url: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM sources WHERE url = ?", (url,))

    def record_source(self, url: str, agent: str, kind: str, status: str, content_hash: str | None) -> bool:
        """Store a source check; returns True when the content changed (or is new)."""
        now = timeutil.iso_now()
        with self._lock:
            prev = self.source(url)
            changed = content_hash is not None and (prev is None or prev["content_hash"] != content_hash)
            self._exec(
                "INSERT INTO sources (url, agent, kind, content_hash, last_status, last_checked, last_changed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET "
                "content_hash = COALESCE(excluded.content_hash, sources.content_hash), "
                "last_status = excluded.last_status, last_checked = excluded.last_checked, "
                "last_changed = COALESCE(excluded.last_changed, sources.last_changed)",
                (url, agent, kind, content_hash, status, now, now if changed else None),
            )
        return changed

    def sources(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM sources ORDER BY agent, url")

    # ---- search cache / usage ----------------------------------------------

    def cached_search(self, key: str, day: str) -> list[dict] | None:
        row = self._one("SELECT results FROM search_cache WHERE query_key = ? AND day = ?", (key, day))
        return json.loads(row["results"]) if row else None

    def store_search(self, key: str, day: str, results: list[dict]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO search_cache (query_key, day, results) VALUES (?, ?, ?)",
            (key, day, json.dumps(results, ensure_ascii=False)),
        )

    def prune_search_cache(self, before_day: str) -> None:
        self._exec("DELETE FROM search_cache WHERE day < ?", (before_day,))

    def add_usage(self, kind: str, amount: float, day: str | None = None) -> None:
        day = day or timeutil.today_ist().isoformat()
        self._exec(
            "INSERT INTO usage (day, kind, amount) VALUES (?, ?, ?) "
            "ON CONFLICT(day, kind) DO UPDATE SET amount = usage.amount + excluded.amount",
            (day, kind, amount),
        )

    def usage(self, kind: str, prefix: str) -> float:
        """Sum usage for days starting with prefix ('2026-10-29' for a day, '2026-10' for a month)."""
        row = self._one(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM usage WHERE kind = ? AND day LIKE ?",
            (kind, f"{prefix}%"),
        )
        return float(row["total"])

    # ---- runs -------------------------------------------------------------

    def start_run(self, agent: str) -> int:
        cur = self._exec(
            "INSERT INTO runs (agent, ist_date, started_at) VALUES (?, ?, ?)",
            (agent, timeutil.today_ist().isoformat(), timeutil.iso_now()),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, stats: dict, errors: list[str]) -> None:
        self._exec(
            "UPDATE runs SET finished_at = ?, status = ?, stats = ?, errors = ? WHERE id = ?",
            (timeutil.iso_now(), status, json.dumps(stats), json.dumps(errors[:50]), run_id),
        )

    def successful_run(self, agent: str, ist_date: str) -> sqlite3.Row | None:
        return self._one(
            "SELECT * FROM runs WHERE agent = ? AND ist_date = ? AND status IN ('ok', 'partial') "
            "ORDER BY id DESC LIMIT 1",
            (agent, ist_date),
        )

    def last_run(self, agent: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM runs WHERE agent = ? ORDER BY id DESC LIMIT 1", (agent,))

    # ---- reports / reminders ---------------------------------------------

    def report(self, kind: str, ist_date: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM reports WHERE kind = ? AND ist_date = ?", (kind, ist_date))

    def last_report(self, kind: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM reports WHERE kind = ? ORDER BY ist_date DESC LIMIT 1", (kind,))

    def create_report(self, kind: str, ist_date: str, chunks: list[str], item_ids: list[int],
                      reminders: list[list[int]]) -> int:
        cur = self._exec(
            "INSERT INTO reports (kind, ist_date, status, created_at, chunks, message_ids, item_ids, reminders) "
            "VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)",
            (kind, ist_date, timeutil.iso_now(), json.dumps(chunks, ensure_ascii=False),
             json.dumps([None] * len(chunks)), json.dumps(item_ids), json.dumps(reminders)),
        )
        return int(cur.lastrowid)

    def set_report_message_ids(self, report_id: int, message_ids: list[int | None]) -> None:
        self._exec("UPDATE reports SET message_ids = ? WHERE id = ?", (json.dumps(message_ids), report_id))

    def finish_report(self, report_id: int) -> None:
        self._exec("UPDATE reports SET status = 'sent', sent_at = ? WHERE id = ?", (timeutil.iso_now(), report_id))

    def delete_report(self, report_id: int) -> None:
        self._exec("DELETE FROM reports WHERE id = ?", (report_id,))

    def reminder_sent(self, item_id: int, threshold: int) -> bool:
        return self._one(
            "SELECT 1 FROM reminders WHERE item_id = ? AND threshold = ?", (item_id, threshold)
        ) is not None

    def mark_reminders(self, pairs: Iterable[tuple[int, int]]) -> None:
        day = timeutil.today_ist().isoformat()
        self._many(
            "INSERT OR IGNORE INTO reminders (item_id, threshold, sent_on) VALUES (?, ?, ?)",
            [(i, t, day) for i, t in pairs],
        )


def item_data(row: sqlite3.Row | None) -> dict:
    return json.loads(row["data"]) if row and row["data"] else {}
