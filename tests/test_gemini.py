import json

import httpx
import pytest

from scout import gemini
from scout.config import Settings
from scout.gemini import GeminiLLM, json_schema
from scout.llm import LLM, LLMBudgetExceeded, make_llm
from scout.models import AdviceList, InternshipPage

ADVICE = {"items": [{"id": 1, "priority": 4, "why_it_matters": "Fits.", "action": "Apply.", "concerns": None}]}


def ok(payload: dict, finish: str = "STOP") -> httpx.Response:
    parts = [{"text": "thinking...", "thought": True}, {"text": json.dumps(payload)}]
    return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}, "finishReason": finish}]})


def quota_error(quota_id: str, delay: str = "2s") -> httpx.Response:
    return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ]}})


def make(db, handler, **settings) -> GeminiLLM:
    s = Settings(anthropic_api_key=None, gemini_api_key="AIza-test", llm_provider="gemini", **settings)
    return GeminiLLM(s, db, client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(gemini.time, "sleep", slept.append)
    return slept


def test_schema_is_inlined_and_keeps_fields_named_title():
    schema = json_schema(InternshipPage)
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    item = schema["properties"]["opportunities"]["items"]
    assert "title" in item["properties"] and "title" in item["required"]


def test_request_shape_and_parsing(db):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(ADVICE)

    llm = make(db, handler)
    result = llm.analyse(AdviceList, "sys prompt", "user turn")
    assert result.items[0].action == "Apply."
    req = seen[0]
    assert req.url.path.endswith("/models/gemini-3.6-flash:generateContent")
    assert req.headers["x-goog-api-key"] == "AIza-test" and "key=" not in str(req.url)
    body = json.loads(req.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys prompt"
    assert body["contents"][0]["parts"][0]["text"] == "user turn"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["required"] == ["items"]

    llm.extract(InternshipPage, "s", "u")
    assert seen[1].url.path.endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert llm.calls == 2 and llm.cost == 0 and db.usage("llm_calls", "2026") == 2


def test_rate_limit_is_retried_with_server_delay(db, no_sleep):
    responses = [quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "7s"), ok(ADVICE)]
    llm = make(db, lambda r: responses.pop(0), llm_rpm=0)
    assert llm.analyse(AdviceList, "s", "u") is not None
    assert no_sleep == [7.0]


def test_daily_quota_stops_only_that_model(db):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "flash-lite" in request.url.path:
            return quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")
        return ok(ADVICE)

    llm = make(db, handler)
    for _ in range(2):
        with pytest.raises(LLMBudgetExceeded, match="daily free quota"):
            llm.extract(InternshipPage, "s", "u")
    assert len(calls) == 1  # the exhausted model is not called again this run
    assert llm.analyse(AdviceList, "s", "u") is not None


def test_bad_key_stops_without_retrying(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                   "message": "API key not valid."}})

    llm = make(db, handler)
    with pytest.raises(LLMBudgetExceeded, match="API key not valid"):
        llm.extract(InternshipPage, "s", "u")
    assert len(calls) == 1


def test_truncated_or_blocked_output_is_discarded(db):
    llm = make(db, lambda r: ok(ADVICE, finish="MAX_TOKENS"))
    assert llm.analyse(AdviceList, "s", "u") is None
    llm = make(db, lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    assert llm.analyse(AdviceList, "s", "u") is None


def test_provider_selection():
    gem = Settings(anthropic_api_key=None, gemini_api_key="g", llm_provider="auto")
    assert gem.llm_provider == "gemini" and gem.extract_model == "gemini-3.5-flash-lite" and gem.llm_rpm == 10
    claude = Settings(anthropic_api_key="a", gemini_api_key="g", llm_provider="auto")
    assert claude.llm_provider == "anthropic" and claude.analysis_model == "claude-opus-5-5" and claude.llm_rpm == 0
    forced = Settings(anthropic_api_key="a", gemini_api_key="g", llm_provider="gemini",
                      analysis_model="gemini-pro-latest")
    assert forced.llm_provider == "gemini" and forced.analysis_model == "gemini-pro-latest"
    assert Settings(anthropic_api_key=None, gemini_api_key=None, llm_provider="auto").llm_key_field == "gemini_api_key"


def test_make_llm_picks_backend(db):
    assert isinstance(make_llm(Settings(anthropic_api_key=None, gemini_api_key="g", llm_provider="auto"), db),
                      GeminiLLM)
    claude = make_llm(Settings(anthropic_api_key="a", gemini_api_key=None, llm_provider="auto"), db)
    assert type(claude) is LLM
    assert make_llm(Settings(anthropic_api_key=None, gemini_api_key=None, llm_provider="auto"), db) is None
