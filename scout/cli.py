"""Command-line entry point: python -m scout <command>."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import timedelta

from . import AGENTS, timeutil
from .agents import SCOUTS, Context
from .agents.base import AgentResult
from .bot import COMMANDS, poll, whoami
from .config import Settings, profile
from .db import DB
from .delivery import deliver_daily
from .llm import LLM
from .report import command_report
from .sources.fetch import Fetcher, make_client
from .sources.tavily import Tavily
from .telegram import Telegram

log = logging.getLogger("scout")
MAX_WAIT_SECONDS = 75 * 60


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO, which would include the Telegram bot token.
    for noisy in ("httpx", "httpcore", "anthropic", "trafilatura", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def summary(text: str) -> None:
    """Append to the GitHub Actions job summary when running in Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def build_context(settings: Settings, db: DB) -> Context:
    http = make_client()
    return Context(
        settings=settings,
        db=db,
        llm=LLM(settings, db) if settings.anthropic_api_key else None,
        tavily=Tavily(settings.tavily_api_key, db, http, settings.max_tavily_credits_per_day),
        fetcher=Fetcher(http),
        http=http,
        profile=profile(),
    )


def run_scouts(ctx: Context, agents: list[str], force: bool) -> list[AgentResult]:
    db = ctx.db
    today = timeutil.today_ist().isoformat()
    db.prune_search_cache((timeutil.today_ist() - timedelta(days=3)).isoformat())
    results = []
    for name in agents:
        if not force and db.successful_run(name, today):
            log.info("[%s] already scouted today; reusing results (use --force to rescan)", name)
            continue
        run_id = db.start_run(name)
        started = time.monotonic()
        try:
            result = SCOUTS[name](ctx).run()
        except Exception as exc:  # an agent crash must not block the others or the digest
            log.exception("[%s] crashed", name)
            result = AgentResult(name, status="failed", errors=[f"{type(exc).__name__}: {exc}"])
        result.stats["seconds"] = round(time.monotonic() - started)
        db.finish_run(run_id, result.status, dict(result.stats), result.errors)
        log.info("[%s] %s %s", name, result.status, json.dumps(dict(result.stats)))
        for err in result.errors[:10]:
            log.warning("[%s] %s", name, err)
        results.append(result)
    return results


def telegram(settings: Settings) -> Telegram | None:
    return Telegram(settings.telegram_bot_token) if settings.telegram_bot_token else None


def cmd_daily(args, settings: Settings, db: DB) -> int:
    today = timeutil.today_ist().isoformat()
    existing = db.report("daily", today)
    if existing and existing["status"] == "sent" and not args.force and not args.dry_run:
        log.info("digest for %s already sent at %s; exiting", today, existing["sent_at"])
        summary(f"Digest for {today} was already sent at {existing['sent_at']} — nothing to do.")
        return 0

    agents = AGENTS if args.agents == "all" else tuple(a.strip() for a in args.agents.split(","))
    results: list[AgentResult] = []
    if not args.skip_scout:
        missing = settings.missing("anthropic_api_key", "tavily_api_key")
        if missing:
            log.warning("missing secrets: %s", ", ".join(m.upper() for m in missing))
        ctx = build_context(settings, db)
        results = run_scouts(ctx, list(agents), args.force)
        if ctx.llm:
            log.info("Claude calls: %d, est. cost $%.3f", ctx.llm.calls, ctx.llm.cost)

    lines = ["## Scout run", "", "| agent | status | stats |", "|---|---|---|"]
    lines += [f"| {r.agent} | {r.status} | {json.dumps(dict(r.stats))} |" for r in results]
    summary("\n".join(lines))

    if args.wait and not args.dry_run:
        wait = timeutil.seconds_until_ist(settings.digest_hour, settings.digest_minute)
        if wait > 0:
            wait = min(wait, MAX_WAIT_SECONDS)
            log.info("waiting %.0f s until %02d:%02d IST", wait, settings.digest_hour, settings.digest_minute)
            time.sleep(wait)

    tg = telegram(settings)
    if tg and not args.dry_run:
        try:
            tg.set_commands(COMMANDS)
        except Exception as exc:
            log.warning("could not register bot commands: %s", exc)
    outcome = deliver_daily(db, tg, settings.telegram_chat_id, dry_run=args.dry_run, force=args.force)
    sent_at = timeutil.now_ist().strftime("%Y-%m-%d %H:%M:%S IST")
    log.info("delivery: %s at %s", outcome["status"], sent_at)
    if outcome["status"] == "dry_run":
        preview = "\n\n---\n\n".join(outcome["chunks"])
        print(preview)
        summary(f"## Digest preview (dry run, not sent)\n\n```\n{preview}\n```")
    else:
        summary(f"Delivery: **{outcome['status']}** at {sent_at}")

    if results and all(r.status == "failed" for r in results):
        return 1
    return 0


def cmd_scout(args, settings: Settings, db: DB) -> int:
    ctx = build_context(settings, db)
    results = run_scouts(ctx, [args.agent], force=True)
    for item_id in results[0].new_ids if results else []:
        row = db.item(item_id)
        print(f"- [{row['score']:.0f}] {row['title']} — {row['org']} ({row['url']})")
    return 0 if results and results[0].status != "failed" else 1


def cmd_poll(args, settings: Settings, db: DB) -> int:
    tg = telegram(settings)
    if tg is None or not settings.telegram_chat_id:
        # Runs every 15 minutes: stay green (no failure e-mails) until the secrets are added.
        log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set yet; nothing to poll")
        return 0
    stats = poll(db, tg, settings.telegram_chat_id)
    log.info("poll: %s", stats)
    return 0


def cmd_whoami(args, settings: Settings, db: DB) -> int:
    tg = telegram(settings)
    if tg is None:
        log.error("TELEGRAM_BOT_TOKEN is required")
        return 1
    n = whoami(tg)
    log.info("sent chat ids to %d chat(s); send /start to the bot first if this is 0", n)
    return 0


def cmd_send_test(args, settings: Settings, db: DB) -> int:
    tg = telegram(settings)
    if tg is None or not settings.telegram_chat_id:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        return 1
    now = timeutil.now_ist().strftime("%a %d %b %Y, %H:%M:%S IST")
    tg.send_message(settings.telegram_chat_id, f"✅ Opportunity Scout test message — {now}.\nSend /help for commands.")
    tg.set_commands(COMMANDS)
    log.info("test message sent at %s", now)
    return 0


def cmd_report(args, settings: Settings, db: DB) -> int:
    for chunk in command_report(db, args.command):
        print(chunk, end="\n\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="scout")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("daily", help="scout, then send today's digest (once per IST day)")
    p.add_argument("--agents", default="all", help="all or comma list of: " + ", ".join(AGENTS))
    p.add_argument("--dry-run", action="store_true", help="print the digest instead of sending it")
    p.add_argument("--force", action="store_true", help="rescan and resend even if done today")
    p.add_argument("--wait", action="store_true", help="wait until 10:00 IST before sending")
    p.add_argument("--skip-scout", action="store_true", help="only build/send the digest")
    p = sub.add_parser("scout", help="run one agent without sending anything")
    p.add_argument("agent", choices=AGENTS)
    sub.add_parser("poll", help="answer pending Telegram commands")
    sub.add_parser("whoami", help="reply to people who messaged the bot with their chat id")
    sub.add_parser("send-test", help="send a Telegram test message")
    p = sub.add_parser("report", help="print a command report")
    p.add_argument("command", choices=["internships", "courses", "startups", "deadlines", "status", "sources", "help"])
    args = parser.parse_args(argv)

    settings = Settings()
    db = DB(settings.db_path)
    try:
        handler = {
            "daily": cmd_daily, "scout": cmd_scout, "poll": cmd_poll, "whoami": cmd_whoami,
            "send-test": cmd_send_test, "report": cmd_report,
        }[args.cmd]
        return handler(args, settings, db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
