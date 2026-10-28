from types import SimpleNamespace

import httpx
import pytest

from scout import telegram as tgmod
from scout.config import Settings
from scout.llm import LLM, LLMBudgetExceeded
from scout.models import AdviceList, InternshipPage
from scout.sources.tavily import SearchBudgetExceeded, Tavily
from scout.telegram import Telegram


def test_telegram_retries_429_and_falls_back_to_plain_text(monkeypatch):
    monkeypatch.setattr(tgmod.time, "sleep", lambda s: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 1}})
        if '"parse_mode"' in body:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    tg = Telegram("123:SECRET", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert tg.send_message(42, '<b>Hi</b> <a href="https://x.y">link</a>') == 7
    assert len(calls) == 3 and '"parse_mode"' not in calls[-1]
    assert "link (https://x.y)" in calls[-1]


def test_tavily_daily_budget(db):
    def handler(request):
        return httpx.Response(200, json={"results": [{"title": "t", "url": "https://a.b", "content": "c"}]})

    tav = Tavily("tvly-x", db, httpx.Client(transport=httpx.MockTransport(handler)), max_credits_per_day=2)
    tav.search("one")
    tav.search("two")
    tav.search("one")  # cached: free
    with pytest.raises(SearchBudgetExceeded):
        tav.search("three")
    assert tav.credits_used_today() == 2


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, usage=usage, model=kwargs["model"],
                               parsed_output=kwargs["output_format"].model_validate({"items": []})
                               if kwargs["output_format"] is AdviceList else None)


def fake_client():
    messages = FakeMessages()
    beta = SimpleNamespace(messages=FakeMessages())
    return SimpleNamespace(messages=messages, beta=beta)


def test_llm_call_shapes_and_cost_tracking(db):
    client = fake_client()
    llm = LLM(Settings(anthropic_api_key="x"), db, client=client)
    llm.extract(InternshipPage, "sys", "user")
    k = client.messages.kwargs
    assert k["model"] == "claude-haiku-5-5" and k["output_config"] == {"effort": "low"}
    assert k["system"][0]["cache_control"] == {"type": "ephemeral"}

    llm.analyse(AdviceList, "sys", "user")
    k = client.beta.messages.kwargs
    assert k["model"] == "claude-opus-5-5"
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert k["thinking"] == {"type": "adaptive"} and k["output_config"] == {"effort": "medium"}
    assert llm.calls == 2 and llm.cost > 0


def test_llm_monthly_budget(db):
    db.add_usage("llm_usd", 12.5)
    llm = LLM(Settings(anthropic_api_key="x", max_llm_usd_per_month=12), db, client=fake_client())
    with pytest.raises(LLMBudgetExceeded):
        llm.extract(InternshipPage, "s", "u")


def test_no_fallbacks_for_haiku_analysis(db):
    client = fake_client()
    llm = LLM(Settings(anthropic_api_key="x", analysis_model="claude-haiku-5-5"), db, client=client)
    llm.analyse(AdviceList, "s", "u")
    assert "fallbacks" not in client.beta.messages.kwargs
