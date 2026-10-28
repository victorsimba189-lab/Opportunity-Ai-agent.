from __future__ import annotations

import json

import httpx

from scout.agents.base import Context
from scout.config import Settings
from scout.models import AdviceList, StartupAnalysisList
from scout.sources.fetch import Page
from scout.sources.tavily import Tavily

PROFILE = {"student": {"status": "Foreign national studying in India", "year": "Final year"},
           "internship_window": {"start": "2026-12-01", "end": "2027-12-31"}}


class FakeLLM:
    """Returns canned extractions keyed by a substring of the URL in the prompt."""

    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.extract_calls = 0
        self.analyse_calls = 0
        self.calls = 0
        self.cost = 0.0

    def extract(self, schema, system, user, max_tokens=12000):
        self.extract_calls += 1
        for key, value in self.responses.items():
            if key in user:
                return value
        return None

    def analyse(self, schema, system, user, max_tokens=16000):
        self.analyse_calls += 1
        payload = user.split("\n", 2)[-1]
        if schema is AdviceList:
            ids = [i["id"] for i in json.loads(payload.split("Items:\n", 1)[-1])]
            return AdviceList(items=[{"id": i, "priority": 4, "why_it_matters": "Fits PEB focus.",
                                      "action": "Email the HR contact this week.", "concerns": None} for i in ids])
        if schema is StartupAnalysisList:
            ids = [s["id"] for s in json.loads(user.split("Stories:\n", 1)[-1])]
            return StartupAnalysisList(items=[{"id": i, "growth_story": "Grew via enterprise pilots.",
                                               "competitive_advantage": "Data moat.", "lessons": ["Start narrow."],
                                               "speculative": "Valuation is reported, not confirmed.",
                                               "priority": 3} for i in ids])
        return None


class FakeFetcher:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url: str) -> Page:
        self.fetched.append(url)
        if "linkedin.com" in url:
            return Page(url, url, 0, error="not fetched (platform restrictions)")
        if url in self.pages:
            return Page(url, url, 200, text=self.pages[url])
        return Page(url, url, 404, error="HTTP 404")


def tavily_transport(results_by_query: dict[str, list[dict]], counter: list):
    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        q = json.loads(request.content)["query"]
        return httpx.Response(200, json={"results": results_by_query.get(q, [])})
    return httpx.MockTransport(handler)


def make_context(db, llm, fetcher, results_by_query=None, counter=None, settings=None) -> Context:
    settings = settings or Settings(anthropic_api_key="test", tavily_api_key="tvly-test",
                                    telegram_bot_token=None, telegram_chat_id=None, workers=2)
    counter = counter if counter is not None else []
    http = httpx.Client(transport=tavily_transport(results_by_query or {}, counter))
    return Context(settings=settings, db=db, llm=llm,
                   tavily=Tavily(settings.tavily_api_key, db, http, settings.max_tavily_credits_per_day),
                   fetcher=fetcher, http=http, profile=PROFILE)
