"""Agent 3: startup intelligence — funding, valuations, growth, failures and lessons."""

from __future__ import annotations

import json

from .. import timeutil
from ..db import item_data
from ..dedupe import canonical_url, fingerprint, normalise_text
from ..models import StartupAnalysisList, StartupStory
from ..rank import keyword_bonus, rule_score
from ..sources.rss import read_feed
from ..verify import check_fields
from .base import EXTRACTION_RULES, Candidate, Scout

QUOTED = {
    "amount_raised": "amount_quote",
    "valuation": "valuation_quote",
    "revenue": "revenue_quote",
}

EVENT_TERMS = {
    "raises": 4, "raised": 3, "funding": 3, "series a": 4, "series b": 4, "series c": 4, "series d": 4,
    "seed": 2, "pre-series": 3, "valuation": 4, "unicorn": 4, "acquires": 3, "acquired": 3, "ipo": 3,
    "revenue": 3, "profit": 3, "arr": 2, "down round": 5, "layoff": 3, "shuts down": 4, "shutdown": 4,
}


class StartupScout(Scout):
    name = "startups"
    label = "Startup intelligence"
    min_relevance = 3

    def extra_candidates(self) -> list[Candidate]:
        out = []
        days = self.cfg.get("feed_max_age_days", 3)
        for feed in self.cfg.get("feeds", []):
            try:
                entries = read_feed(self.ctx.http, feed["url"], feed["name"])
            except Exception as exc:
                self.ctx.db.record_source(feed["url"], self.name, "rss", f"error: {exc}"[:200], None)
                self.stats["feed_errors"] += 1
                continue
            self.ctx.db.record_source(feed["url"], self.name, "rss", "ok", None)
            self.stats["feeds_ok"] += 1
            out += [Candidate(url=e.url, title=e.title, snippet=e.summary, source=f"rss:{e.feed}",
                              published=e.published)
                    for e in entries if timeutil.within_days(e.published, days)]
        return out

    def prefilter(self, candidates: list[Candidate]) -> list[Candidate]:
        """Keep news about specific startup events, ranked by event terms and priority sectors."""
        sectors = self.cfg.get("keywords", {})

        def interest(c: Candidate) -> float:
            blob = f"{c.title} {c.snippet}"
            return keyword_bonus(blob, EVENT_TERMS, cap=10) + keyword_bonus(blob, sectors, cap=10)

        scored = [(interest(c), c) for c in candidates]
        kept = [c for s, c in sorted(scored, key=lambda x: -x[0]) if s >= 3]
        self.stats["prefiltered_out"] += len(candidates) - len(kept)
        return kept

    def system_prompt(self) -> str:
        return (
            "You extract facts about one startup from a news article for an aspiring founder:\n"
            f"{self.profile_text()}\n\n"
            "Keep funding raised separate from revenue. Label valuation_status honestly: confirmed only when "
            "the company, an investor or a filing states it; reported when media cite sources; estimated "
            "for third-party estimates. Include losses, down-rounds, layoffs or shutdowns in challenges. "
            "If the article is a roundup of many companies, set is_startup_story=false.\n\n" + EXTRACTION_RULES
        )

    def extract(self, c: Candidate, text: str) -> list[dict]:
        story = self.ctx.llm.extract(StartupStory, self.system_prompt(), self.source_block(c, text))
        return [story.model_dump()] if story else []

    def find_duplicate(self, item: dict):
        existing = self.ctx.db.item_by_fingerprint(item["fingerprint"])
        if existing:
            return existing
        name = normalise_text(item["org"])
        event = item["data"].get("event_type")
        when = timeutil.parse_date(item["data"].get("event_date") or item.get("published_at")) or timeutil.today_ist()
        for row in self.ctx.db.items(self.name, statuses=None):
            data = item_data(row)
            if normalise_text(row["org"]) != name or data.get("event_type") != event:
                continue
            other = timeutil.parse_date(data.get("event_date") or row["published_at"]) or when
            if abs((other - when).days) <= 45:
                return row
        return None

    def to_item(self, c: Candidate, r: dict, text: str, basis: str) -> dict | None:
        if not r.get("is_startup_story") or not r.get("startup_name"):
            self.stats["not_story"] += 1
            return None
        verified, unverified = check_fields(r, text, QUOTED)
        r["verified_fields"], r["unverified_fields"] = verified, unverified
        if r.get("valuation") is None:
            r["valuation_status"] = "not_disclosed"
        event_day = timeutil.parse_date(r.get("event_date") or c.published)
        status, reason = "active", None
        if (r.get("relevance") or 0) < self.min_relevance:
            status, reason = "low_relevance", "low relevance"
        blob = " ".join(str(r.get(k) or "") for k in ("sector", "event_type", "event_summary", "country",
                                                      "business_model"))
        month = event_day.strftime("%Y-%m") if event_day else ""
        return {
            "agent": self.name,
            "fingerprint": fingerprint(self.name, r["startup_name"], r["event_type"], r.get("round_stage"), month),
            "title": r.get("event_summary") or r["startup_name"],
            "org": r["startup_name"],
            "url": c.url,
            "source_url": c.url,
            "canonical_url": canonical_url(c.url),
            "location": ", ".join(x for x in (r.get("hq_city"), r.get("country")) if x) or None,
            "deadline": None,
            "start_date": None,
            "eligibility_intl": None,
            "cost": None,
            "status": status,
            "status_reason": reason,
            "verification": basis,
            "relevance": r.get("relevance") or 0,
            "score": rule_score(blob=blob, relevance=r.get("relevance") or 0, cfg=self.cfg,
                                published=c.published, verification=basis),
            "published_at": c.published,
            "data": {**r, "found_via": c.source, "outlet": c.source.split(":", 1)[-1]},
        }

    def advise(self, new_ids: list[int]) -> None:
        """Deep analysis (growth story, moat, lessons) for the top new stories only."""
        rows = sorted((self.ctx.db.item(i) for i in new_ids), key=lambda r: -r["score"])
        rows = rows[: self.cfg.get("analyse_top", 3)]
        if not rows:
            return
        stories = []
        for r in rows:
            facts = {k: v for k, v in item_data(r).items() if v and not k.endswith("_quote") and k != "sources"}
            stories.append({"id": r["id"], "facts": facts, "article": self._texts.get(r["id"], "")[:10000]})
        system = (
            "You are a startup analyst mentoring an aspiring founder:\n"
            f"{self.profile_text()}\n\n"
            "For each story explain how the company grew, its competitive advantage and 2-3 practical lessons. "
            "Use only the facts and article given. Say explicitly which claims are estimates or unconfirmed. "
            "Do not present speculation as fact."
        )
        user = f"Today (IST): {timeutil.today_ist().isoformat()}\nStories:\n" + json.dumps(stories, ensure_ascii=False)
        result = self.ctx.llm.analyse(StartupAnalysisList, system, user)
        if result is None:
            return
        by_id = {r["id"]: r for r in rows}
        for a in result.items:
            row = by_id.get(a.id)
            if row is None:
                continue
            data = item_data(row)
            data["analysis"] = a.model_dump(exclude={"id"})
            self.ctx.db.update_item(row["id"], data=data, score=row["score"] + max(1, min(a.priority, 5)) * 4)
