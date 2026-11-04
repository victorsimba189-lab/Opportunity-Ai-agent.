"""Setup check (`python -m scout check`): tests every key with one cheap call and says what to fix.

Run it after adding or changing secrets, instead of waiting 10-20 minutes for a daily run to fail.
Costs: no Tavily credit (it reads /usage), one tiny request per AI model, one YouTube quota unit.
Nothing here prints a key or the chat ID: workflow logs of a public repository are public.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from . import timeutil
from .config import Settings
from .gemini import _error as gemini_error
from .gemini import _is_daily_quota
from .telegram import Telegram, TelegramError, TelegramNetworkError

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TAVILY_USAGE_URL = "https://api.tavily.com/usage"
YOUTUBE_URL = "https://www.googleapis.com/youtube/v3/i18nRegions"  # 1 quota unit
PROMPT = "Reply with the word OK."
ICONS = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "➖"}
TAVILY_LOW_CREDITS = 100


@dataclass
class Check:
    name: str
    status: str  # ok, warn, fail or skip
    detail: str

    def line(self) -> str:
        return f"{ICONS[self.status]} {self.name}: {self.detail}"


def _short(exc: Exception) -> str:
    # TelegramError reads "method: status description"; the method name means nothing to the reader.
    return str(exc).split(": ", 1)[-1][:150]


# ---- Telegram ------------------------------------------------------------------------------

def check_telegram(settings: Settings, client: httpx.Client) -> tuple[list[Check], Telegram | None]:
    """The Telegram checks, plus a client that can reach TELEGRAM_CHAT_ID (None if it can't)."""
    if not settings.telegram_bot_token:
        return [Check("Telegram bot", "fail", "TELEGRAM_BOT_TOKEN is not set (setup steps 1 and 4)")], None
    tg = Telegram(settings.telegram_bot_token, client=client)
    try:
        me = tg.call("getMe", attempts=2) or {}
    except TelegramNetworkError as exc:
        return [Check("Telegram bot", "warn", f"could not reach Telegram ({_short(exc)}). Run the check again later")], None
    except TelegramError as exc:
        return [Check("Telegram bot", "fail",
                      f"the token was rejected ({_short(exc)}). Copy it again from @BotFather")], None
    checks = [Check("Telegram bot", "ok", f"@{me.get('username', '?')}")]

    reachable = None
    if not settings.telegram_chat_id:
        checks.append(Check("Telegram chat", "fail",
                            "TELEGRAM_CHAT_ID is not set. Run this workflow with mode whoami (setup step 5)"))
    else:
        try:
            tg.call("getChat", attempts=2, chat_id=settings.telegram_chat_id)
            checks.append(Check("Telegram chat", "ok", "the bot can message TELEGRAM_CHAT_ID"))
            reachable = tg
        except TelegramNetworkError as exc:
            checks.append(Check("Telegram chat", "warn", f"could not reach Telegram ({_short(exc)})"))
        except TelegramError as exc:
            checks.append(Check("Telegram chat", "fail",
                                f"the bot can't reach TELEGRAM_CHAT_ID ({_short(exc)}). Open the bot, press "
                                "Start, and check the secret matches the ID from whoami"))
    checks.append(_webhook_check(tg))
    return checks, reachable


def _webhook_check(tg: Telegram) -> Check:
    try:
        info = tg.call("getWebhookInfo", attempts=2) or {}
    except TelegramError as exc:
        return Check("Bot commands", "warn", f"could not read how commands are delivered ({_short(exc)})")
    if not info.get("url"):
        return Check("Bot commands", "ok",
                     "answered by the GitHub poller, which can take hours. README step 9 makes replies instant")
    error_at = info.get("last_error_date") or 0
    if error_at and timeutil.now_utc().timestamp() - error_at < 24 * 3600:
        return Check("Bot commands", "warn",
                     f"Telegram could not reach the Cloudflare Worker in the last day "
                     f"({str(info.get('last_error_message') or 'no details')[:120]}). Open the Worker's /setup link")
    return Check("Bot commands", "ok", "answered within seconds by the Cloudflare Worker")


# ---- AI ------------------------------------------------------------------------------------

def check_llm(settings: Settings, client: httpx.Client, anthropic_client=None) -> list[Check]:
    gemini = settings.llm_provider == "gemini"
    key_name = settings.llm_key_field.upper()
    if not getattr(settings, settings.llm_key_field):
        where = "setup step 2" if gemini else "README: Free Gemini vs paid Claude"
        return [Check("AI key", "fail", f"{key_name} is not set ({where})")]
    models: dict[str, str] = {}
    for model, var in ((settings.extract_model, "SCOUT_EXTRACT_MODEL"),
                       (settings.analysis_model, "SCOUT_ANALYSIS_MODEL")):
        models.setdefault(model, var)
    if gemini:
        return [_gemini_model(settings, client, model, var) for model, var in models.items()]
    if anthropic_client is None:
        import anthropic

        anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=1, timeout=60.0)
    return [_claude_model(anthropic_client, model, var) for model, var in models.items()]


def _gemini_model(settings: Settings, client: httpx.Client, model: str, var: str) -> Check:
    name = f"Gemini {model}"
    try:
        resp = client.post(GEMINI_URL.format(model=model), timeout=60,
                           headers={"x-goog-api-key": settings.gemini_api_key},
                           json={"contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
                                 "generationConfig": {"maxOutputTokens": 64}})
    except httpx.TransportError as exc:
        return Check(name, "warn", f"could not reach Gemini ({type(exc).__name__}). Run the check again later")
    if resp.status_code == 200:
        return Check(name, "ok", "the key works and the model answers")
    error = gemini_error(resp)
    message = str(error.get("message") or "").strip()
    reasons = {str(d.get("reason")) for d in error.get("details") or [] if isinstance(d, dict)}
    if "API_KEY_INVALID" in reasons or "API key not valid" in message:
        return Check(name, "fail", "GEMINI_API_KEY was rejected. Create a new key in Google AI Studio "
                                   "and update the secret")
    if resp.status_code == 404:
        return Check(name, "fail", f"Gemini has no model called {model} for this key. Pick a current Flash "
                                   f"model in Google AI Studio and set the repository variable {var} to its name")
    if resp.status_code == 429:
        if "limit: 0" in message:
            return Check(name, "fail", f"this model has no free tier for your key. Set the variable {var} to a "
                                       "Flash model, or turn on billing in Google AI Studio")
        if _is_daily_quota(error):
            return Check(name, "warn", "the key works, but today's free quota for this model is used up. "
                                       "Daily runs continue tomorrow")
        return Check(name, "warn", "the key works but is rate-limited right now. Run the check again in a minute")
    if resp.status_code >= 500:
        return Check(name, "warn", f"Gemini is busy (HTTP {resp.status_code}). Run the check again later")
    return Check(name, "fail", f"HTTP {resp.status_code} {error.get('status', '')}: {message[:200]}".replace("  ", " "))


def _claude_model(client, model: str, var: str) -> Check:
    import anthropic

    name = f"Claude {model}"
    try:
        client.messages.create(model=model, max_tokens=16, messages=[{"role": "user", "content": PROMPT}])
    except anthropic.AuthenticationError:
        return Check(name, "fail", "ANTHROPIC_API_KEY was rejected. Create a new key at platform.claude.com")
    except anthropic.NotFoundError:
        return Check(name, "fail", f"Claude has no model called {model}. Fix or delete the variable {var}")
    except anthropic.RateLimitError:
        return Check(name, "warn", "the key works but is rate-limited right now. Run the check again in a minute")
    except (anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
        return Check(name, "warn", f"could not reach Claude ({type(exc).__name__}). Run the check again later")
    except anthropic.APIStatusError as exc:
        if "credit" in str(exc).lower():
            return Check(name, "fail", "the Claude account has no credit. Add some under Billing at platform.claude.com")
        return Check(name, "fail", f"HTTP {exc.status_code}: {str(exc)[:200]}")
    return Check(name, "ok", "the key works and the model answers")


# ---- Search --------------------------------------------------------------------------------

def check_tavily(settings: Settings, client: httpx.Client) -> Check:
    if not settings.tavily_api_key:
        return Check("Tavily search", "fail", "TAVILY_API_KEY is not set (setup step 3)")
    try:
        resp = client.get(TAVILY_USAGE_URL, timeout=30, headers={"Authorization": f"Bearer {settings.tavily_api_key}"})
    except httpx.TransportError as exc:
        return Check("Tavily search", "warn", f"could not reach Tavily ({type(exc).__name__}). Run the check again later")
    if resp.status_code in (401, 403):
        return Check("Tavily search", "fail", "TAVILY_API_KEY was rejected. Copy it again from app.tavily.com")
    if resp.status_code != 200:
        return Check("Tavily search", "warn", f"could not read usage (HTTP {resp.status_code}); the key may still work")
    try:
        data = resp.json()
    except ValueError:
        data = {}
    account, key = data.get("account") or {}, data.get("key") or {}
    used = account.get("plan_usage", key.get("usage"))
    limit = account.get("plan_limit") or key.get("limit")
    if not isinstance(used, (int, float)) or not limit:
        return Check("Tavily search", "ok", "the key works")
    left = limit - used
    detail = f"the key works; {used:g} of {limit:g} monthly credits used"
    if left < TAVILY_LOW_CREDITS:
        return Check("Tavily search", "warn", f"{detail}. Only {left:g} left: searches stop when they run out")
    return Check("Tavily search", "ok", detail)


def check_youtube(settings: Settings, client: httpx.Client) -> Check:
    if not settings.youtube_api_key:
        return Check("YouTube", "skip", "YOUTUBE_API_KEY is not set (optional)")
    try:
        resp = client.get(YOUTUBE_URL, timeout=30, params={"part": "snippet", "key": settings.youtube_api_key})
    except httpx.TransportError as exc:
        # The request URL carries the key, so only the exception type is shown.
        return Check("YouTube", "warn", f"could not reach YouTube ({type(exc).__name__})")
    if resp.status_code == 200:
        return Check("YouTube", "ok", "the key works")
    message = str(gemini_error(resp).get("message") or f"HTTP {resp.status_code}")
    return Check("YouTube", "warn", f"the key doesn't work ({message[:160]}). Fix it, or delete the secret "
                                    "to skip video results")


# ---- All -----------------------------------------------------------------------------------

def run_checks(settings: Settings, client: httpx.Client, anthropic_client=None) -> tuple[list[Check], Telegram | None]:
    checks, tg = check_telegram(settings, client)
    checks += check_llm(settings, client, anthropic_client)
    checks.append(check_tavily(settings, client))
    checks.append(check_youtube(settings, client))
    for check in checks:
        log.log(logging.WARNING if check.status in ("warn", "fail") else logging.INFO, "%s", check.line())
    return checks, tg


def verdict(checks: list[Check]) -> str:
    failed = sum(c.status == "fail" for c in checks)
    if failed:
        return f"{failed} problem{'s' if failed > 1 else ''} to fix. Fix the ❌ lines, then run the check again."
    note = " Read the ⚠️ lines too." if any(c.status == "warn" for c in checks) else ""
    return f"No problems found.{note} Next: Actions → Daily digest → Run workflow with dry run ticked (setup step 7)."
