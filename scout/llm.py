"""Claude API wrapper: structured extraction (cheap model) and analysis (strong model) with cost tracking."""

from __future__ import annotations

import logging
import threading
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from . import timeutil
from .config import Settings
from .db import DB

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# USD per million tokens (input, output). Cache writes ~1.25x input, cache reads ~0.1x input.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5-1": (10.00, 50.00),
}
# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMBudgetExceeded(RuntimeError):
    pass


def estimate_cost(model: str, usage) -> float:
    inp, out = PRICES.get(model, PRICES["claude-opus-5-5"])
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    tokens_in = (usage.input_tokens or 0) + cache_write * 1.25 + cache_read * 0.1
    return (tokens_in * inp + (usage.output_tokens or 0) * out) / 1_000_000


class LLM:
    def __init__(self, settings: Settings, db: DB, client: anthropic.Anthropic | None = None):
        self.settings = settings
        self.db = db
        self.client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key, max_retries=4, timeout=300.0
        )
        self._lock = threading.Lock()
        self.calls = 0
        self.cost = 0.0

    def month_spend(self) -> float:
        return self.db.usage("llm_usd", timeutil.today_ist().strftime("%Y-%m"))

    def _check_budget(self) -> None:
        if self.month_spend() >= self.settings.max_llm_usd_per_month:
            raise LLMBudgetExceeded(
                f"monthly LLM budget of ${self.settings.max_llm_usd_per_month:.2f} reached"
            )

    def _record(self, model: str, response) -> None:
        cost = estimate_cost(model, response.usage)
        with self._lock:
            self.calls += 1
            self.cost += cost
            self.db.add_usage("llm_usd", cost)
            self.db.add_usage("llm_calls", 1)

    @staticmethod
    def _system(text: str) -> list[dict]:
        # Stable system prompt first, marked cacheable; volatile content goes in the user turn.
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def _result(self, model: str, response, schema: type[T]) -> T | None:
        # After a server-side fallback the response names the model that actually served it.
        self._record(getattr(response, "model", None) or model, response)
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            log.warning("model %s declined the request (category=%s)", model, category)
            return None
        if response.stop_reason == "max_tokens":
            log.warning("model %s hit max_tokens; output discarded", model)
            return None
        parsed = response.parsed_output
        if parsed is None:
            log.warning("model %s returned no parseable %s", model, schema.__name__)
        return parsed

    def extract(self, schema: type[T], system: str, user: str, max_tokens: int = 12000) -> T | None:
        """Bulk structured extraction with the cheap model (low effort)."""
        self._check_budget()
        model = self.settings.extract_model
        response = self.client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=self._system(system),
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": "low"},
        )
        return self._result(model, response, schema)

    def analyse(self, schema: type[T], system: str, user: str, max_tokens: int = 16000) -> T | None:
        """Judgement-heavy work (ranking, advice, startup analysis) with the strong model."""
        self._check_budget()
        model = self.settings.analysis_model
        kwargs: dict = {}
        if model in FALLBACK_MODELS:
            kwargs = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        response = self.client.beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=self._system(system),
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.analysis_effort},
            **kwargs,
        )
        return self._result(model, response, schema)
