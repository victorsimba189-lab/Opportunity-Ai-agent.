import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from scout import check as chk
from scout import telegram as tgmod
from scout.config import Settings

TOKEN = "123456:SECRET-TOKEN"
CHAT = "987654321"
KEYS = dict(telegram_bot_token=TOKEN, telegram_chat_id=CHAT, gemini_api_key="AIza-secret-gemini",
            tavily_api_key="tvly-secret", anthropic_api_key=None, youtube_api_key=None)


def settings(**overrides) -> Settings:
    return Settings(**{**KEYS, "llm_provider": "gemini", **overrides})


class Router:
    """httpx mock: answers by URL substring; defaults are a fully working setup."""

    def __init__(self, **overrides):
        self.calls: list[httpx.Request] = []
        self.routes = {
            "/getMe": lambda r: tg_ok({"username": "scout_bot"}),
            "/getChat": lambda r: tg_ok({"id": int(CHAT)}),
            "/getWebhookInfo": lambda r: tg_ok({"url": ""}),
            "/sendMessage": lambda r: tg_ok({"message_id": 1}),
            "/setMyCommands": lambda r: tg_ok(True),
            "generativelanguage": lambda r: httpx.Response(200, json={"candidates": []}),
            "api.tavily.com/usage": lambda r: httpx.Response(200, json={
                "key": {"usage": 40, "limit": 1000}, "account": {"plan_usage": 240, "plan_limit": 1000}}),
            "youtube": lambda r: httpx.Response(200, json={"items": []}),
        }
        self.routes.update(overrides)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for part, handler in self.routes.items():
            if part in str(request.url):
                return handler(request)
        raise AssertionError(f"unexpected request {request.url}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def hit(self, part: str) -> list[httpx.Request]:
        return [r for r in self.calls if part in str(r.url)]


def tg_ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def tg_fail(status, description):
    return httpx.Response(status, json={"ok": False, "description": description})


def google_error(status, message, state="", reason=None):
    details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}] if reason else []
    return httpx.Response(status, json={"error": {"code": status, "message": message, "status": state,
                                                  "details": details}})


def by_name(checks):
    return {c.name: c for c in checks}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tgmod.time, "sleep", lambda s: None)


def test_working_setup_passes_without_spending_search_credits(freeze):
    router = Router()
    checks, tg = chk.run_checks(settings(), router.client())
    assert [c.status for c in checks] == ["ok"] * 6 + ["skip"]
    assert tg is not None
    names = by_name(checks)
    assert names["Telegram bot"].detail == "@scout_bot"
    assert "240 of 1000 monthly credits used" in names["Tavily search"].detail
    assert "GitHub poller" in names["Bot commands"].detail
    # One tiny request per model; Tavily's /usage is read instead of running a search.
    models = [r.url.path.split("/")[-1] for r in router.hit("generativelanguage")]
    assert models == ["gemini-3.5-flash-lite:generateContent", "gemini-3.6-flash:generateContent"]
    assert router.hit("tavily")[0].method == "GET"
    assert chk.verdict(checks).startswith("No problems found. Next:")


def test_missing_secrets_are_named_and_nothing_is_called(freeze):
    router = Router()
    checks, tg = chk.run_checks(Settings(), router.client())
    lines = [c.line() for c in checks]
    assert lines == [
        "❌ Telegram bot: TELEGRAM_BOT_TOKEN is not set (setup steps 1 and 4)",
        "❌ AI key: GEMINI_API_KEY is not set (setup step 2)",
        "❌ Tavily search: TAVILY_API_KEY is not set (setup step 3)",
        "➖ YouTube: YOUTUBE_API_KEY is not set (optional)",
    ]
    assert tg is None and router.calls == []
    assert chk.verdict(checks).startswith("3 problems to fix")


def test_rejected_keys(freeze):
    router = Router(**{
        "/getChat": lambda r: tg_fail(400, "Bad Request: chat not found"),
        "generativelanguage": lambda r: google_error(400, "API key not valid. Please pass a valid API key.",
                                                     "INVALID_ARGUMENT", "API_KEY_INVALID"),
        "api.tavily.com/usage": lambda r: httpx.Response(401, json={"detail": {"error": "Unauthorized"}}),
    })
    checks, tg = chk.run_checks(settings(), router.client())
    names = by_name(checks)
    assert tg is None  # the chat can't be reached, so the result goes only to the job summary
    assert names["Telegram chat"].status == "fail" and "press Start" in names["Telegram chat"].detail
    assert "chat not found" in names["Telegram chat"].detail
    assert names["Gemini gemini-3.5-flash-lite"].status == "fail"
    assert "GEMINI_API_KEY was rejected" in names["Gemini gemini-3.5-flash-lite"].detail
    assert names["Tavily search"].status == "fail"


def test_gemini_model_problems_point_at_the_right_variable(freeze):
    def gemini(request):
        if "flash-lite" in str(request.url):
            return google_error(404, "models/gemini-3.5-flash-lite is not found", "NOT_FOUND")
        return google_error(429, "Quota exceeded for metric generate_content_free_tier_requests, limit: 0",
                            "RESOURCE_EXHAUSTED")

    checks, _ = chk.run_checks(settings(), Router(generativelanguage=gemini).client())
    names = by_name(checks)
    lite, flash = names["Gemini gemini-3.5-flash-lite"], names["Gemini gemini-3.6-flash"]
    assert lite.status == "fail" and "SCOUT_EXTRACT_MODEL" in lite.detail
    assert flash.status == "fail" and "no free tier" in flash.detail and "SCOUT_ANALYSIS_MODEL" in flash.detail


def test_used_up_daily_quota_is_a_warning(freeze):
    quota = httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                                "details": [{"violations": [{"quotaId": "RequestsPerDay-FreeTier"}]}]}})
    checks, _ = chk.run_checks(settings(), Router(generativelanguage=lambda r: quota).client())
    assert by_name(checks)["Gemini gemini-3.6-flash"].status == "warn"
    assert chk.verdict(checks).startswith("No problems found. Read the ⚠️ lines too.")


def test_unreachable_telegram_is_not_blamed_on_the_token(freeze):
    def down(request):
        raise httpx.ConnectError("proxy refused")

    checks, tg = chk.run_checks(settings(), Router(**{"/getMe": down}).client())
    bot = by_name(checks)["Telegram bot"]
    assert bot.status == "warn" and "could not reach Telegram" in bot.detail and tg is None


def test_worker_webhook_state(freeze):
    now = freeze("2026-11-04T04:00:00Z").timestamp()
    ok = Router(**{"/getWebhookInfo": lambda r: tg_ok({"url": "https://w.example.workers.dev/telegram"})})
    assert "Cloudflare Worker" in by_name(chk.run_checks(settings(), ok.client())[0])["Bot commands"].detail

    failing = Router(**{"/getWebhookInfo": lambda r: tg_ok({
        "url": "https://w.example.workers.dev/telegram", "last_error_date": int(now) - 600,
        "last_error_message": "Wrong response from the webhook: 403 Forbidden"})})
    commands = by_name(chk.run_checks(settings(), failing.client())[0])["Bot commands"]
    assert commands.status == "warn" and "403 Forbidden" in commands.detail
    assert "workers.dev" not in commands.detail  # the Worker's address stays out of public logs


def test_low_tavily_credits_and_youtube(freeze):
    router = Router(**{
        "api.tavily.com/usage": lambda r: httpx.Response(200, json={"account": {"plan_usage": 950, "plan_limit": 1000}}),
        "youtube": lambda r: google_error(400, "API key not valid. Please pass a valid API key."),
    })
    names = by_name(chk.run_checks(settings(youtube_api_key="AIza-yt"), router.client())[0])
    assert names["Tavily search"].status == "warn" and "Only 50 left" in names["Tavily search"].detail
    assert names["YouTube"].status == "warn" and "API key not valid" in names["YouTube"].detail


def _status_error(cls, status, message):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body=None)


def test_claude_checks(freeze):
    class Messages:
        def __init__(self, errors):
            self.errors, self.models = errors, []

        def create(self, model, **kwargs):
            self.models.append(model)
            if model in self.errors:
                raise self.errors[model]
            return SimpleNamespace(content=[])

    s = settings(llm_provider="anthropic", anthropic_api_key="sk-ant-secret")
    messages = Messages({
        "claude-opus-5-5": _status_error(anthropic.BadRequestError, 400,
                                         "Your credit balance is too low to access the Anthropic API."),
    })
    checks = chk.check_llm(s, httpx.Client(), anthropic_client=SimpleNamespace(messages=messages))
    assert messages.models == ["claude-haiku-5-5", "claude-opus-5-5"]
    assert [c.status for c in checks] == ["ok", "fail"] and "no credit" in checks[1].detail

    messages = Messages({"claude-haiku-5-5": _status_error(anthropic.AuthenticationError, 401, "invalid x-api-key")})
    checks = chk.check_llm(s, httpx.Client(), anthropic_client=SimpleNamespace(messages=messages))
    assert "ANTHROPIC_API_KEY was rejected" in checks[0].detail


def test_check_command_reports_to_summary_and_telegram(tmp_path, monkeypatch, freeze):
    from scout import cli

    router = Router()
    monkeypatch.setattr(cli, "make_client", router.client)
    for name, value in KEYS.items():
        if value:
            monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("SCOUT_DB", str(tmp_path / "scout.db"))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert cli.main(["check"]) == 0
    text = summary.read_text()
    assert "## Setup check" in text and "✅ Tavily search" in text and "No problems found" in text
    sent = json.loads(router.hit("/sendMessage")[0].content)
    assert sent["chat_id"] == CHAT and "setup check" in sent["text"] and "✅ Telegram bot" in sent["text"]
    assert router.hit("/setMyCommands")
    # Workflow logs and summaries are public: no key, token or chat ID in them.
    for secret in (TOKEN, CHAT, "AIza-secret-gemini", "tvly-secret"):
        assert secret not in text

    router.routes["generativelanguage"] = lambda r: google_error(400, "API key not valid.", reason="API_KEY_INVALID")
    assert cli.main(["check"]) == 1
