"""Tavily web search (free tier: 1,000 credits/month; a basic search costs 1 credit)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import httpx

from .. import timeutil
from ..db import DB
from .fetch import request_with_retry

log = logging.getLogger(__name__)

API_URL = "https://api.tavily.com/search"


class SearchBudgetExceeded(RuntimeError):
    pass


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    published: str | None = None


class Tavily:
    def __init__(self, api_key: str | None, db: DB, client: httpx.Client, max_credits_per_day: int):
        self.api_key = api_key
        self.db = db
        self.client = client
        self.max_credits_per_day = max_credits_per_day

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def credits_used_today(self) -> float:
        return self.db.usage("tavily", timeutil.today_ist().isoformat())

    def search(self, query: str, *, topic: str = "general", time_range: str | None = "month",
               max_results: int = 8, include_domains: list[str] | None = None,
               country: str | None = None) -> list[SearchResult]:
        payload: dict = {"query": query, "search_depth": "basic", "topic": topic, "max_results": max_results}
        if time_range:
            payload["time_range"] = time_range
        if include_domains:
            payload["include_domains"] = include_domains
        if country and topic == "general":
            payload["country"] = country
        key = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        day = timeutil.today_ist().isoformat()
        cached = self.db.cached_search(key, day)
        if cached is not None:
            return [SearchResult(**r) for r in cached]
        if not self.enabled:
            return []
        if self.credits_used_today() + 1 > self.max_credits_per_day:
            raise SearchBudgetExceeded(f"daily Tavily budget of {self.max_credits_per_day} credits reached")

        resp = request_with_retry(
            self.client, "POST", API_URL, json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")
        self.db.add_usage("tavily", 1)
        results = [
            SearchResult(
                title=(r.get("title") or "").strip(),
                url=r.get("url") or "",
                content=(r.get("content") or "").strip(),
                published=r.get("published_date"),
            )
            for r in resp.json().get("results", [])
            if r.get("url")
        ]
        self.db.store_search(key, day, [r.__dict__ for r in results])
        return results
