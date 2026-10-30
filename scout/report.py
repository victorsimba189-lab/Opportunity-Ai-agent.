"""Telegram HTML formatting for the daily digest and the on-demand command reports."""

from __future__ import annotations

import html
import json
from datetime import timedelta

from . import AGENTS, timeutil
from .config import load_yaml
from .db import DB, item_data
from .telegram import MAX_MESSAGE_CHARS

CHUNK_LIMIT = MAX_MESSAGE_CHARS - 200  # headroom for Telegram's entity counting
SECTION_LIMITS = {"internships": 5, "programmes": 5, "startups": 3}
REMINDER_THRESHOLDS = (14, 7, 3, 1)
MIN_RELEVANCE = {"internships": 4, "programmes": 4, "startups": 3}

TITLES = {
    "internships": "🏗 <b>1. Civil engineering internships</b>",
    "programmes": "🎓 <b>2. Free startup courses &amp; founder opportunities</b>",
    "startups": "📈 <b>3. Startup stories, funding &amp; lessons</b>",
    "deadlines": "⏰ <b>4. Upcoming deadlines &amp; recommended actions</b>",
}
NOUNS = {"internships": "internships", "programmes": "free programmes", "startups": "startup stories"}

HELP = (
    "🤖 <b>Opportunity Scout</b> — daily brief at 10:00 IST.\n\n"
    "/internships — top civil engineering internships\n"
    "/courses — free startup courses &amp; founder opportunities\n"
    "/startups — latest startup stories, funding &amp; lessons\n"
    "/deadlines — deadlines in the next 45 days\n"
    "/sources — links to check manually (LinkedIn, Instagram…)\n"
    "/status — last run, usage and next digest\n"
    "/help — this message\n\n"
    "Replies come from the latest scan and can take up to ~15 minutes."
)


def esc(value) -> str:
    return html.escape(str(value), quote=False)


def link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'


def deadline_text(deadline: str | None) -> str:
    d = timeutil.parse_date(deadline)
    if d is None:
        return "deadline not stated"
    days = timeutil.days_until(d)
    when = "today" if days == 0 else f"{days} days left" if days and days > 0 else "passed"
    return f"deadline {timeutil.fmt_date(d)} ({when})"


def _flags(row, data: dict) -> list[str]:
    out = []
    if row["verification"] == "snippet":
        out.append("🔎 search snippet only — open the link to verify")
    if data.get("unverified_fields"):
        out.append("⚠️ not confirmed in source: " + ", ".join(sorted(data["unverified_fields"])))
    return out


def _advice(data: dict) -> list[str]:
    a = data.get("advice") or {}
    out = []
    if a.get("why_it_matters"):
        out.append(f"💡 {esc(a['why_it_matters'])}")
    if a.get("action"):
        out.append(f"➡️ {esc(a['action'])}")
    if a.get("concerns"):
        out.append(f"⚠️ {esc(a['concerns'])}")
    return out


def format_internship(n: int, row) -> str:
    d = item_data(row)
    lines = [f"{n}. <b>{esc(row['title'])}</b> — {esc(row['org'])}",
             f"📍 {esc(row['location'] or 'location not stated')} · ⏳ {deadline_text(row['deadline'])}"]
    details = [x for x in (d.get("period"), d.get("stipend") and f"stipend: {d['stipend']}",
                           d.get("fee") and f"fee: {d['fee']}") if x]
    if details:
        lines.append("🗓 " + esc(" · ".join(details)))
    intl = {"open": "✅ open to international students (stated)",
            "unclear": "⚠️ international eligibility not stated — confirm with organiser"}.get(row["eligibility_intl"])
    if intl:
        lines.append(f"🌍 {intl}")
    lines += _advice(d) + _flags(row, d)
    links = [link(row["url"], "Apply / details")]
    if row["source_url"] != row["url"]:
        links.append(link(row["source_url"], "source"))
    lines.append("🔗 " + " · ".join(links))
    return "\n".join(lines)


COST_LABELS = {
    "free": "Free", "fully_funded": "Fully funded", "free_with_paid_certificate": "Free (certificate is paid)",
    "paid": "Paid", "not_stated": "Cost not stated",
}


def format_programme(n: int, row) -> str:
    d = item_data(row)
    kind = (d.get("programme_type") or "").replace("_", "-")
    mode = d.get("mode") if d.get("mode") not in (None, "not_stated") else None
    head = " · ".join(x for x in (kind, mode, COST_LABELS.get(row["cost"] or "", "")) if x)
    lines = [f"{n}. <b>{esc(row['title'])}</b> — {esc(row['org'])}", f"🏷 {esc(head)} · ⏳ {deadline_text(row['deadline'])}"]
    extras = []
    if d.get("fee_details"):
        extras.append(f"fees: {d['fee_details']}")
    if d.get("certificate") in ("free_certificate", "paid_certificate"):
        extras.append("certificate: " + ("free" if d["certificate"] == "free_certificate" else "paid"))
    if d.get("funding"):
        extras.append(f"funding: {d['funding']}")
    if d.get("equity"):
        extras.append(f"equity: {d['equity']}")
    if extras:
        lines.append("💵 " + esc(" · ".join(extras)))
    if d.get("practical_value"):
        lines.append(f"🎯 {esc(d['practical_value'])}")
    if row["eligibility_intl"] == "open":
        lines.append("🌍 ✅ open to foreign nationals (stated)")
    elif row["eligibility_intl"] == "unclear":
        lines.append("🌍 ⚠️ eligibility for foreign nationals not stated")
    lines += _advice(d) + _flags(row, d)
    lines.append("🔗 " + link(row["url"], "Apply / details"))
    return "\n".join(lines)


VALUATION_LABELS = {"confirmed": "confirmed by company/investor/filing", "reported": "reported by media, not confirmed",
                    "estimated": "third-party estimate"}


def format_startup(n: int, row) -> str:
    d = item_data(row)
    where = " · ".join(x for x in (d.get("country"), d.get("sector")) if x)
    lines = [f"{n}. <b>{esc(row['org'])}</b> ({esc(where)})", esc(d.get("event_summary") or row["title"])]
    if d.get("founders"):
        lines.append("👥 Founders: " + esc(", ".join(d["founders"])))
    if d.get("amount_raised"):
        raised = d["amount_raised"] + (f" ({d['round_stage']})" if d.get("round_stage") else "")
        if d.get("investors"):
            raised += " — investors: " + ", ".join(d["investors"][:5])
        lines.append("💰 Funding raised: " + esc(raised))
    if d.get("valuation"):
        lines.append(f"📊 Valuation: {esc(d['valuation'])} ({VALUATION_LABELS.get(d.get('valuation_status'), 'status unclear')})")
    if d.get("revenue"):
        lines.append(f"📈 Revenue (not funding): {esc(d['revenue'])}")
    if d.get("business_model") or d.get("problem"):
        lines.append("🧩 " + esc(" — ".join(x for x in (d.get("problem"), d.get("business_model")) if x)))
    if d.get("challenges"):
        lines.append(f"⚠️ Challenges: {esc(d['challenges'])}")
    a = d.get("analysis") or {}
    if a:
        lines.append("🧠 <i>Analysis (AI, not reported fact):</i> " + esc(a.get("growth_story", "")))
        if a.get("competitive_advantage"):
            lines.append("🛡 Edge: " + esc(a["competitive_advantage"]))
        for lesson in a.get("lessons", [])[:3]:
            lines.append("📝 " + esc(lesson))
        if a.get("speculative"):
            lines.append("❔ Unconfirmed: " + esc(a["speculative"]))
    lines += _flags(row, d)
    published = timeutil.fmt_date(timeutil.parse_date(row["published_at"])) if row["published_at"] else "date unknown"
    corroborated = f" · corroborated by {row['sources_count']} outlets" if (row["sources_count"] or 1) > 1 else ""
    lines.append(f"🔗 {link(row['url'], d.get('outlet') or 'source')} ({published}){corroborated}")
    return "\n".join(lines)


FORMATTERS = {"internships": format_internship, "programmes": format_programme, "startups": format_startup}


# ---- selection ---------------------------------------------------------------

def new_items(db: DB, agent: str) -> list:
    rows = [r for r in db.items(agent) if r["notified_at"] is None and (r["relevance"] or 0) >= MIN_RELEVANCE[agent]]
    return sorted(rows, key=lambda r: -r["score"])[: SECTION_LIMITS[agent]]


def deadline_reminders(db: DB, included_ids: set[int], horizon: int = 14) -> list[tuple]:
    """Active items whose deadline is within `horizon` days and that are due a reminder today."""
    due = []
    for agent in ("internships", "programmes"):
        for row in db.items(agent):
            days = timeutil.days_until(timeutil.parse_date(row["deadline"]))
            if days is None or days < 0 or days > horizon:
                continue
            threshold = min(t for t in REMINDER_THRESHOLDS if days <= t)
            if row["id"] in included_ids or not db.reminder_sent(row["id"], threshold):
                due.append((days, threshold, row))
    return sorted(due, key=lambda x: x[0])


def status_line(db: DB, agent: str) -> str:
    run = db.last_run(agent)
    today = timeutil.today_ist().isoformat()
    if run is None or run["ist_date"] != today:
        return f"<i>No scan ran today for {NOUNS[agent]}.</i>"
    stats = json.loads(run["stats"] or "{}")
    if run["status"] == "failed":
        errors = json.loads(run["errors"] or "[]")
        return f"⚠️ <i>Scan failed today ({esc(errors[0][:120]) if errors else 'unknown error'}). Will retry tomorrow.</i>"
    checked = []
    if stats.get("searches"):
        checked.append(f"{stats['searches']} searches")
    if stats.get("watch_ok"):
        checked.append(f"{stats['watch_ok']} official pages")
    if stats.get("feeds_ok"):
        checked.append(f"{stats['feeds_ok']} news feeds")
    excluded = []
    for key, label in (("status_ineligible", "ineligible"), ("status_expired", "expired"), ("expired", "expired"),
                       ("status_paid", "paid"), ("status_out_of_window", "outside your dates"),
                       ("duplicates", "duplicates")):
        if stats.get(key):
            excluded.append(f"{stats[key]} {label}")
    msg = f"No new verified {NOUNS[agent]} today"
    if checked:
        msg += " — checked " + ", ".join(checked)
    if excluded:
        msg += "; filtered out " + ", ".join(excluded)
    return f"<i>{esc(msg)}.</i>"


# ---- assembly -------------------------------------------------------------------

def pack(blocks: list[str], limit: int = CHUNK_LIMIT) -> list[str]:
    """Pack text blocks into messages under the Telegram limit, splitting oversized blocks by line."""
    chunks: list[str] = []
    current = ""
    for block in blocks:
        pieces = [block]
        if len(block) > limit:
            pieces, buf = [], ""
            for line in block.split("\n"):
                line = line if len(line) <= limit else line[: limit - 1] + "…"
                if buf and len(buf) + len(line) + 1 > limit:
                    pieces.append(buf)
                    buf = ""
                buf = f"{buf}\n{line}" if buf else line
            if buf:
                pieces.append(buf)
        for piece in pieces:
            if current and len(current) + len(piece) + 2 > limit:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def build_digest(db: DB) -> tuple[list[str], list[int], list[tuple[int, int]]]:
    """Returns (message chunks, item ids included, reminder (item_id, threshold) pairs)."""
    now = timeutil.now_ist()
    blocks = [f"🗞 <b>Daily Opportunity Brief</b> — {now.strftime('%a %d %b %Y')}"]
    included: list[int] = []
    for agent in AGENTS:
        rows = new_items(db, agent)
        section = [TITLES[agent]]
        if rows:
            section += [FORMATTERS[agent](i, r) for i, r in enumerate(rows, 1)]
            included += [r["id"] for r in rows]
        else:
            section.append(status_line(db, agent))
        blocks.append("\n\n".join(section))

    reminders = deadline_reminders(db, set(included))
    lines = [TITLES["deadlines"]]
    pairs: list[tuple[int, int]] = []
    for days, threshold, row in reminders:
        when = "TODAY" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        action = (item_data(row).get("advice") or {}).get("action")
        line = f"• <b>{esc(row['title'])}</b> ({esc(row['org'])}) — closes {when}, {timeutil.fmt_date(timeutil.parse_date(row['deadline']))}"
        if action:
            line += f"\n  ➡️ {esc(action)}"
        lines.append(line + f" · {link(row['url'], 'link')}")
        pairs += [(row["id"], t) for t in REMINDER_THRESHOLDS if t >= threshold]
    if not reminders:
        lines.append("<i>No application deadlines in the next 14 days.</i>")
    top_actions = []
    for item_id in included:
        row = db.item(item_id)
        action = (item_data(row).get("advice") or {}).get("action")
        if action and row["agent"] != "startups" and not row["deadline"]:
            top_actions.append(f"• {esc(action)} — {link(row['url'], row['title'][:40])}")
    if top_actions:
        lines.append("<b>Also worth doing:</b>\n" + "\n".join(top_actions[:3]))
    blocks.append("\n".join(lines))

    spend = db.usage("llm_usd", timeutil.today_ist().isoformat())
    blocks.append(f"<i>Scan cost today ≈ ${spend:.2f} · /help for commands</i>")
    return pack(blocks), included, pairs


def command_report(db: DB, command: str) -> list[str]:
    command = command.lower()
    if command in ("internships", "courses", "programmes", "startups"):
        agent = "programmes" if command == "courses" else command
        rows = sorted(db.items(agent), key=lambda r: -r["score"])
        if agent == "startups":
            cutoff = (timeutil.today_ist() - timedelta(days=10)).isoformat()
            rows = [r for r in rows if (r["first_seen"] or "") >= cutoff]
        rows = [r for r in rows if (r["relevance"] or 0) >= MIN_RELEVANCE[agent]][:8 if agent != "startups" else 5]
        blocks = [TITLES[agent].replace("1. ", "").replace("2. ", "").replace("3. ", "")]
        if rows:
            blocks += [FORMATTERS[agent](i, r) for i, r in enumerate(rows, 1)]
        else:
            blocks.append("<i>Nothing active right now.</i> " + status_line(db, agent))
        counts = db.status_counts(agent)
        hidden = {k: v for k, v in counts.items() if k != "active"}
        if hidden:
            blocks.append("<i>Hidden: " + esc(", ".join(f"{v} {k.replace('_', ' ')}" for k, v in hidden.items())) + "</i>")
        return pack(blocks)
    if command == "deadlines":
        due = deadline_reminders_all(db, 45)
        lines = ["⏰ <b>Deadlines in the next 45 days</b>"]
        lines += [f"• {timeutil.fmt_date(timeutil.parse_date(r['deadline']))} ({days}d) — <b>{esc(r['title'])}</b>, "
                  f"{esc(r['org'])} · {link(r['url'], 'link')}" for days, r in due]
        if not due:
            lines.append("<i>No known deadlines in the next 45 days.</i>")
        return pack(["\n".join(lines)])
    if command == "status":
        return pack([status_report(db)])
    if command == "sources":
        return pack([sources_report(db)])
    return [HELP]


def deadline_reminders_all(db: DB, horizon: int) -> list[tuple[int, object]]:
    out = []
    for agent in ("internships", "programmes"):
        for row in db.items(agent):
            days = timeutil.days_until(timeutil.parse_date(row["deadline"]))
            if days is not None and 0 <= days <= horizon:
                out.append((days, row))
    return sorted(out, key=lambda x: x[0])


def status_report(db: DB) -> str:
    lines = ["📋 <b>Status</b>"]
    for agent in AGENTS:
        run = db.last_run(agent)
        if run is None:
            lines.append(f"• {agent}: never run")
            continue
        started = timeutil.parse_iso(run["started_at"]).astimezone(timeutil.IST).strftime("%d %b %H:%M IST")
        stats = json.loads(run["stats"] or "{}")
        lines.append(f"• {agent}: {run['status']} at {started} — {stats.get('new_active', 0)} new, "
                     f"{stats.get('candidates', 0)} checked")
    last = db.last_report("daily")
    if last and last["sent_at"]:
        sent = timeutil.parse_iso(last["sent_at"]).astimezone(timeutil.IST).strftime("%d %b %H:%M IST")
        lines.append(f"Last digest: {sent}")
    month = timeutil.today_ist().strftime("%Y-%m")
    lines.append(f"Usage this month: Tavily {db.usage('tavily', month):.0f} credits · "
                 f"AI {db.usage('llm_calls', month):.0f} calls ≈ ${db.usage('llm_usd', month):.2f}")
    lines.append("Next digest: 10:00 IST tomorrow" if timeutil.seconds_until_ist(10) < 0 else "Next digest: 10:00 IST today")
    return "\n".join(lines)


def sources_report(db: DB) -> str:
    lines = ["🔎 <b>Check these manually</b> (no permitted automated access)"]
    for agent in AGENTS:
        for entry in load_yaml(agent).get("manual_checks", []):
            lines.append(f"• {link(entry['url'], entry['label'])}")
    broken = [r for r in db.sources() if r["last_status"] and r["last_status"] != "ok"]
    if broken:
        lines.append("\n<b>Sources failing on the last scan</b>")
        lines += [f"• {esc(r['url'])} — {esc(r['last_status'][:60])}" for r in broken[:10]]
    return "\n".join(lines)
