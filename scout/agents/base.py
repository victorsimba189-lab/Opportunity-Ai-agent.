"""Shared scouting pipeline: gather → skip seen → fetch → extract → verify → dedupe → store → advise."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from .. import timeutil
from ..config import Settings, load_yaml
from ..db import DB, item_data
from ..dedupe import canonical_url, domain, same_org, similarity
from ..llm import LLM, LLMBudgetExceeded
from ..models import AdviceList
from ..sources.fetch import Fetcher
from ..sources.tavily import SearchBudgetExceeded, Tavily

log = logging.getLogger(__name__)

RICHER_FIELDS = ("url", "source_url", "canonical_url", "location", "deadline", "start_date",
                 "eligibility_intl", "cost", "status", "status_reason", "verification", "relevance", "score")

MD_LINK = re.compile(r"\[([^\]]{0,200})\]\(([^)\s]+)\)")

EXTRACTION_RULES = """Rules:
- Use only facts stated in the SOURCE text. If something is not stated, use null or "not_stated". Never guess.
- Every *_quote field must be copied character-for-character from the SOURCE (one sentence or table row).
- Dates must be ISO YYYY-MM-DD and only when the SOURCE gives a specific date.
- Links must be copied exactly from the SOURCE; otherwise null.
- If the SOURCE contains no qualifying item, return an empty list."""


@dataclass
class Candidate:
    url: str
    title: str
    snippet: str = ""
    source: str = ""
    published: str | None = None
    page_text: str | None = None
    force: bool = False  # re-process even if the URL was seen (changed watchlist page)


@dataclass
class Context:
    settings: Settings
    db: DB
    llm: LLM | None
    tavily: Tavily
    fetcher: Fetcher
    http: httpx.Client
    profile: dict


@dataclass
class AgentResult:
    agent: str
    status: str = "ok"
    stats: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    new_ids: list[int] = field(default_factory=list)


def content_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode()).hexdigest()


class Scout:
    name = ""
    label = ""
    min_relevance = 4

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.cfg = load_yaml(self.name)
        self.result = AgentResult(self.name)
        self._texts: dict[int, str] = {}

    # ---- helpers -------------------------------------------------------------

    @property
    def stats(self) -> Counter:
        return self.result.stats

    def error(self, msg: str) -> None:
        log.warning("[%s] %s", self.name, msg)
        self.result.errors.append(msg[:300])

    def profile_text(self) -> str:
        return json.dumps(self.ctx.profile, ensure_ascii=False, indent=1, default=str)

    # ---- gathering -----------------------------------------------------------

    def gather(self) -> list[Candidate]:
        return [*self.watchlist(), *self.searches(), *self.extra_candidates()]

    def watchlist(self) -> list[Candidate]:
        out = []
        for entry in self.cfg.get("watchlist", []):
            url, name = (entry, entry) if isinstance(entry, str) else (entry["url"], entry.get("name", entry["url"]))
            page = self.ctx.fetcher.fetch(url)
            if not page.ok:
                self.ctx.db.record_source(url, self.name, "watch", page.error or f"HTTP {page.status}", None)
                self.stats["watch_errors"] += 1
                continue
            self.stats["watch_ok"] += 1
            if self.ctx.db.record_source(url, self.name, "watch", "ok", content_hash(page.text)):
                out.append(Candidate(url=page.final_url, title=name, source=f"watch:{url}",
                                     page_text=page.text, force=True))
                out += self.notice_links(page.final_url, page.text, name)
            else:
                self.stats["watch_unchanged"] += 1
        return out

    def notice_links(self, base_url: str, text: str, name: str) -> list[Candidate]:
        """Follow up to N notice links (often PDFs) on a changed official page, one level deep."""
        pattern = self.cfg.get("follow_link_pattern")
        if not pattern:
            return []
        rx = re.compile(pattern, re.IGNORECASE)
        found: dict[str, str] = {}
        for label, href in MD_LINK.findall(text):
            url = urljoin(base_url, href.strip())
            if url.startswith("http") and (rx.search(label) or rx.search(href)):
                found.setdefault(url, label.strip() or url)
        limit = self.cfg.get("follow_links", 4)
        self.stats["notice_links"] += min(len(found), limit)
        return [Candidate(url=u, title=f"{name}: {label}", source=f"watch-link:{base_url}")
                for u, label in list(found.items())[:limit]]

    def searches(self) -> list[Candidate]:
        out = []
        tavily = self.ctx.tavily
        if not tavily.enabled:
            if self.cfg.get("queries"):
                self.error("TAVILY_API_KEY not set: web searches skipped")
            return out
        defaults = self.cfg.get("search_defaults", {})
        for q in self.cfg.get("queries", []):
            params = {**defaults, **({"query": q} if isinstance(q, str) else q)}
            try:
                results = tavily.search(**params)
            except SearchBudgetExceeded as exc:
                self.error(str(exc))
                break
            except Exception as exc:  # one failing query must not stop the agent
                self.error(f"search failed for {params['query']!r}: {exc}")
                continue
            self.stats["searches"] += 1
            out += [Candidate(url=r.url, title=r.title, snippet=r.content, source=f"search:{params['query']}",
                              published=r.published) for r in results]
        return out

    def extra_candidates(self) -> list[Candidate]:
        return []

    def prefilter(self, candidates: list[Candidate]) -> list[Candidate]:
        """Order/trim candidates before paying for extraction. Watchlist pages first."""
        return sorted(candidates, key=lambda c: not c.source.startswith("watch"))

    # ---- per-candidate work (runs in worker threads) -----------------------

    def page_text(self, c: Candidate) -> tuple[str, str]:
        """Return (text, basis) where basis is 'page' (fetched) or 'snippet' (search/feed snippet only)."""
        if c.page_text:
            return c.page_text, "page"
        page = self.ctx.fetcher.fetch(c.url)
        if page.ok:
            return page.text, "page"
        self.stats["snippet_only"] += 1
        return f"{c.title}\n{c.snippet}".strip(), "snippet"

    def extract(self, c: Candidate, text: str) -> list[dict]:
        raise NotImplementedError

    def to_item(self, c: Candidate, record: dict, text: str, basis: str) -> dict | None:
        raise NotImplementedError

    def source_block(self, c: Candidate, text: str) -> str:
        return (f"Today (IST): {timeutil.today_ist().isoformat()}\nURL: {c.url}\n"
                f"Published: {c.published or 'unknown'}\n\nSOURCE:\n<<<\n{text}\n>>>")

    # ---- storage / dedupe ---------------------------------------------------

    def find_duplicate(self, item: dict):
        existing = self.ctx.db.item_by_fingerprint(item["fingerprint"])
        if existing:
            return existing
        for row in self.ctx.db.items(self.name, statuses=None):
            if same_org(row["org"], item.get("org")) and similarity(row["title"], item["title"]) >= 0.7:
                return row
        return None

    def save(self, item: dict) -> int | None:
        """Insert a new item or merge into its duplicate. Returns the id of newly active items."""
        db = self.ctx.db
        dup = self.find_duplicate(item)
        if dup is not None:
            data = item_data(dup)
            sources = set(data.get("sources", [dup["source_url"]]))
            sources.add(item["source_url"])
            updates: dict = {"sources_count": len({domain(s) for s in sources})}
            if self._richer(item, dup, data):
                # e.g. the official notice PDF after the listing page: keep the better-verified facts.
                updates.update({k: item[k] for k in RICHER_FIELDS})
                data = {**item["data"], **{k: data[k] for k in ("advice", "analysis") if k in data}}
                self.stats["merged_richer"] += 1
            data["sources"] = sorted(sources)
            db.update_item(dup["id"], data=data, **updates)
            self.stats["duplicates"] += 1
            return None
        item["data"].setdefault("sources", [item["source_url"]])
        item_id = db.insert_item(item)
        self.stats[f"status_{item['status']}"] += 1
        return item_id if item["status"] == "active" else None

    @staticmethod
    def _richer(item: dict, dup, dup_data: dict) -> bool:
        verified = len(item["data"].get("verified_fields", []))
        old_verified = len(dup_data.get("verified_fields", []))
        page_beats_snippet = item["verification"] == "page" and dup["verification"] == "snippet"
        return verified > old_verified or page_beats_snippet or bool(item.get("deadline") and not dup["deadline"])

    # ---- run -----------------------------------------------------------------

    def run(self) -> AgentResult:
        db, settings = self.ctx.db, self.ctx.settings
        if self.ctx.llm is None:
            self.error("ANTHROPIC_API_KEY not set: extraction skipped")
            self.result.status = "failed"
            return self.result

        unique: dict[str, Candidate] = {}
        for c in self.gather():
            key = canonical_url(c.url)
            if key in unique:
                continue
            if not c.force and db.is_seen(key):
                self.stats["already_seen"] += 1
                continue
            unique[key] = c
        selected = self.prefilter(list(unique.values()))[: settings.max_candidates_per_agent]
        self.stats["candidates"] = len(selected)

        def work(c: Candidate):
            text, basis = self.page_text(c)
            return text, basis, self.extract(c, text)

        budget_hit = False
        with ThreadPoolExecutor(max_workers=settings.workers) as pool:
            futures = {pool.submit(work, c): c for c in selected}
            for fut in as_completed(futures):
                c = futures[fut]
                try:
                    text, basis, records = fut.result()
                except LLMBudgetExceeded as exc:
                    if not budget_hit:
                        self.error(str(exc))
                    budget_hit = True
                    continue
                except Exception as exc:
                    self.error(f"processing {c.url} failed: {type(exc).__name__}: {exc}")
                    continue
                outcome = "no_items"
                for record in records:
                    self.stats["extracted"] += 1
                    item = self.to_item(c, record, text, basis)
                    if item is None:
                        continue
                    item_id = self.save(item)
                    if item_id:
                        self.result.new_ids.append(item_id)
                        self._texts[item_id] = text
                    outcome = "items"
                db.mark_seen(canonical_url(c.url), self.name, outcome)

        self.stats["expired"] += db.expire_items(timeutil.today_ist().isoformat())
        if self.result.new_ids and not budget_hit:
            try:
                self.advise(self.result.new_ids)
            except Exception as exc:
                self.error(f"advice step failed: {type(exc).__name__}: {exc}")
        self.stats["new_active"] = len(self.result.new_ids)
        if self.result.errors:
            self.result.status = "partial" if (self.stats["extracted"] or self.stats["watch_ok"]) else "failed"
        return self.result

    # ---- advice (strong model) -------------------------------------------------

    def advice_payload(self, row) -> dict:
        data = item_data(row)
        keep = {k: v for k, v in data.items() if k not in ("sources",) and not k.endswith("_quote") and v}
        return {"id": row["id"], "title": row["title"], "org": row["org"], "verification": row["verification"],
                **keep}

    def advise(self, new_ids: list[int]) -> None:
        rows = sorted((self.ctx.db.item(i) for i in new_ids), key=lambda r: -r["score"])[:8]
        if not rows:
            return
        payload = json.dumps([self.advice_payload(r) for r in rows], ensure_ascii=False, indent=1)
        system = (
            "You advise one person on which opportunities to act on. Their profile:\n"
            f"{self.profile_text()}\n\n"
            "For each item return priority, why_it_matters, action and concerns. Use only the item data; "
            "do not add facts. Mention unverified or unclear eligibility in concerns. Be concise and specific."
        )
        user = f"Today (IST): {timeutil.today_ist().isoformat()}\nCategory: {self.label}\nItems:\n{payload}"
        advice = self.ctx.llm.analyse(AdviceList, system, user)
        if advice is None:
            return
        by_id = {r["id"]: r for r in rows}
        for a in advice.items:
            row = by_id.get(a.id)
            if row is None:
                continue
            data = item_data(row)
            data["advice"] = a.model_dump(exclude={"id"})
            self.ctx.db.update_item(row["id"], data=data, score=row["score"] + max(1, min(a.priority, 5)) * 4)
