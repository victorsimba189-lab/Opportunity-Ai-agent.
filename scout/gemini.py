"""Gemini API backend (Google AI Studio key, free tier) with the same extract/analyse interface as LLM.

Calls the REST endpoint directly with httpx. Free-tier quotas are per minute and per day for each model:
calls are spaced out (SCOUT_LLM_RPM), per-minute 429s are retried, and once a model's daily quota is used
up it is skipped for the rest of the run so the remaining pages are picked up the next day.
"""

from __future__ import annotations

import logging
import time

import httpx
from pydantic import BaseModel, ValidationError

from .llm import LLM, LLMBudgetExceeded, T

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_WAIT_SECONDS = 90.0


class GeminiError(RuntimeError):
    pass


def json_schema(model: type[BaseModel]) -> dict:
    """The model's JSON Schema with $defs inlined (Gemini accepts a subset of JSON Schema)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, list):
            return [inline(v) for v in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return inline(defs[node["$ref"].rsplit("/", 1)[-1]])
        return {k: inline(v) for k, v in node.items()}

    return inline(schema)


def _error(resp: httpx.Response) -> dict:
    try:
        return resp.json().get("error") or {}
    except ValueError:
        return {"message": resp.text[:200]}


def _is_daily_quota(error: dict) -> bool:
    if str(error.get("code")) == "quota_exceeded":
        return True
    for detail in error.get("details") or []:
        for violation in detail.get("violations") or []:
            if "PerDay" in (violation.get("quotaId") or ""):
                return True
    return False


def _retry_delay(resp: httpx.Response, error: dict) -> float | None:
    for detail in error.get("details") or []:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            try:
                return float(str(detail.get("retryDelay", "")).rstrip("s"))
            except ValueError:
                pass
    header = resp.headers.get("retry-after", "")
    return float(header) if header.isdigit() else None


class GeminiLLM(LLM):
    provider = "Gemini"

    def __init__(self, settings, db, client: httpx.Client | None = None):
        super().__init__(settings, db, client)
        self._next_slot = 0.0
        self._unavailable: dict[str, str] = {}  # model -> reason it can't be used again this run

    def _make_client(self) -> httpx.Client:
        return httpx.Client(timeout=300.0)

    def _throttle(self) -> None:
        if self.settings.llm_rpm <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + 60.0 / self.settings.llm_rpm
        if slot > now:
            time.sleep(slot - now)

    def _stop(self, model: str, reason: str) -> LLMBudgetExceeded:
        with self._lock:
            self._unavailable[model] = reason
        return LLMBudgetExceeded(reason)

    def _post(self, model: str, body: dict) -> dict:
        delay = 5.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                resp = self.client.post(API.format(model=model), json=body,
                                        headers={"x-goog-api-key": self.settings.gemini_api_key})
            except httpx.TransportError:
                if attempt == MAX_ATTEMPTS:
                    raise
                wait = delay
            else:
                if resp.status_code == 200:
                    return resp.json()
                error = _error(resp)
                detail = f"{model}: HTTP {resp.status_code} {error.get('status', '')} {error.get('message', '')}"
                if resp.status_code == 429 and _is_daily_quota(error):
                    raise self._stop(model, f"Gemini daily free quota for {model} is used up; "
                                            "the remaining pages are retried tomorrow")
                if resp.status_code not in RETRY_STATUSES:
                    # Bad key, unknown model, region without a free tier...: every later call would fail too.
                    raise self._stop(model, detail.strip()[:300])
                if attempt == MAX_ATTEMPTS:
                    raise GeminiError(detail.strip()[:300])
                wait = _retry_delay(resp, error) or delay
            log.info("Gemini %s busy or rate-limited; retrying in %.0f s", model, min(wait, MAX_WAIT_SECONDS))
            time.sleep(min(wait, MAX_WAIT_SECONDS))
            delay *= 2
        raise RuntimeError("unreachable")

    def _generate(self, model: str, schema: type[T], system: str, user: str, max_tokens: int) -> T | None:
        self._check_budget()
        if model in self._unavailable:
            raise LLMBudgetExceeded(self._unavailable[model])
        data = self._post(model, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": json_schema(schema),
                "maxOutputTokens": max_tokens,
            },
        })
        self._add_usage(0.0)  # free tier; with billing enabled, see spend in Google AI Studio
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            log.warning("model %s returned no answer (blockReason=%s)", model, reason)
            return None
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish == "MAX_TOKENS":
            log.warning("model %s hit maxOutputTokens; output discarded", model)
            return None
        if finish not in (None, "STOP"):
            log.warning("model %s stopped early (finishReason=%s)", model, finish)
            return None
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            log.warning("model %s returned no parseable %s: %s", model, schema.__name__, str(exc)[:200])
            return None

    def extract(self, schema: type[T], system: str, user: str, max_tokens: int = 12000) -> T | None:
        return self._generate(self.settings.extract_model, schema, system, user, max_tokens)

    def analyse(self, schema: type[T], system: str, user: str, max_tokens: int = 16000) -> T | None:
        return self._generate(self.settings.analysis_model, schema, system, user, max_tokens)
