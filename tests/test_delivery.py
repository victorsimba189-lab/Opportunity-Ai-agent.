import json

import pytest

from scout.bot import parse_command, poll
from scout.delivery import deliver_daily
from scout.report import CHUNK_LIMIT, build_digest, command_report, esc, pack
from scout.telegram import TelegramError

from .conftest import FakeTelegram


def add_item(db, agent="internships", title="PEB internship", deadline="2026-11-05", **kw):
    item = {
        "agent": agent, "fingerprint": f"{agent}-{title}", "title": title, "org": kw.pop("org", "PEBCo"),
        "url": "https://example.com/apply?a=1&b=2", "source_url": "https://example.com/apply?a=1&b=2",
        "canonical_url": "https://example.com/apply", "location": "Chennai", "deadline": deadline,
        "start_date": None, "eligibility_intl": "open", "cost": "free", "status": "active",
        "status_reason": None, "verification": "page", "relevance": 8, "score": kw.pop("score", 50),
        "published_at": "2026-10-28", "data": kw.pop("data", {"period": "Dec 2026"}),
    }
    item.update(kw)
    return db.insert_item(item)


def test_digest_sent_once_per_ist_day(db, tg, freeze):
    add_item(db)
    first = deliver_daily(db, tg, "42")
    assert first["status"] == "sent" and len(tg.sent) >= 1
    assert "PEB internship" in tg.sent[0][1]
    n = len(tg.sent)

    # Fallback cron later the same IST day: no second message.
    freeze("2026-10-29T05:13:00Z")
    assert deliver_daily(db, tg, "42")["status"] == "already_sent"
    assert len(tg.sent) == n

    # Next day: the item was already notified, so it's not repeated as "new".
    freeze("2026-10-30T04:30:00Z")
    deliver_daily(db, tg, "42")
    latest = "\n".join(text for _, text in tg.sent[n:])
    assert "1. <b>PEB internship" not in latest
    assert "No scan ran today" in latest


def test_partial_send_resumes_without_duplicates(db, freeze):
    for i in range(12):
        add_item(db, title=f"Internship number {i} " + "x" * 300, deadline=None,
                 data={"period": "y" * 400}, score=100 - i)
        add_item(db, agent="programmes", title=f"Programme {i} " + "z" * 300, deadline=None,
                 data={"practical_value": "v" * 400}, score=100 - i)
    flaky = FakeTelegram(fail_on=1)
    with pytest.raises(TelegramError):
        deliver_daily(db, flaky, "42")
    report = db.report("daily", "2026-10-29")
    assert report["status"] == "pending"
    chunks = json.loads(report["chunks"])
    assert len(chunks) >= 3 and len(flaky.sent) == 1

    result = deliver_daily(db, flaky, "42")  # retry (e.g. the fallback cron)
    assert result["status"] == "sent"
    assert [t for _, t in flaky.sent] == chunks  # every part exactly once, in order


def test_dry_run_changes_nothing(db, tg):
    item_id = add_item(db)
    out = deliver_daily(db, tg, "42", dry_run=True)
    assert out["status"] == "dry_run" and tg.sent == []
    assert db.report("daily", "2026-10-29") is None
    assert db.item(item_id)["notified_at"] is None


def test_deadline_reminders_fire_once_per_threshold(db, tg, freeze):
    item_id = add_item(db, deadline="2026-11-10")  # 12 days after 29 Oct
    deliver_daily(db, tg, "42")
    assert "closes in 12 days" in tg.sent[0][1]
    n = len(tg.sent)
    freeze("2026-10-30T04:30:00Z")
    deliver_daily(db, tg, "42")
    day2 = "\n".join(t for _, t in tg.sent[n:])
    assert "closes in" not in day2  # 14-day reminder already given yesterday
    freeze("2026-11-03T04:30:00Z")  # 7 days left -> new threshold
    deliver_daily(db, tg, "42")
    assert "closes in 7 days" in "\n".join(t for _, t in tg.sent)
    assert db.reminder_sent(item_id, 7)


def test_html_is_escaped_and_chunks_fit():
    assert esc("<b>R&D</b>") == "&lt;b&gt;R&amp;D&lt;/b&gt;"
    blocks = ["a" * 3000, "b" * 3000, "c\n" * 4000]
    chunks = pack(blocks)
    assert all(len(c) <= CHUNK_LIMIT for c in chunks)
    assert "".join(chunks).count("a") == 3000


def test_digest_escapes_untrusted_titles(db):
    add_item(db, title="Intern <script> & co", org="A&B <Ltd>")
    chunks, ids, _ = build_digest(db)
    text = "\n".join(chunks)
    assert "&lt;script&gt; &amp; co" in text and "<script>" not in text
    assert 'href="https://example.com/apply?a=1&amp;b=2"' in text


def test_command_reports(db):
    add_item(db)
    assert "PEB internship" in command_report(db, "internships")[0]
    assert "Deadlines in the next 45 days" in command_report(db, "deadlines")[0]
    assert "Status" in command_report(db, "status")[0]
    assert "LinkedIn" in command_report(db, "sources")[0]


def test_parse_command():
    assert parse_command("/internships@MyScoutBot") == "internships"
    assert parse_command("/COURSES now") == "courses"
    assert parse_command("/unknown") == "help"
    assert parse_command("hello") is None


def test_poll_answers_only_allowed_chat_and_acks(db, tg):
    add_item(db)
    tg.updates = [
        {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/internships"}},
        {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/internships"}},
        {"update_id": 12, "message": {"chat": {"id": 42}, "text": "/help"}},
    ]
    stats = poll(db, tg, "42")
    assert stats == {"updates": 3, "replied": 2, "ignored": 1}
    assert {chat for chat, _ in tg.sent} == {"42"}
    assert tg.acked == 13
    assert poll(db, tg, "42")["updates"] == 0  # acknowledged updates are not answered twice
