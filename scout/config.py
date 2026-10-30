"""Settings from environment variables and YAML files in config/."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


# (extraction model, analysis model) per provider. Gemini defaults are GA models with a free tier.
DEFAULT_MODELS = {
    "anthropic": ("claude-haiku-5-5", "claude-opus-5-5"),
    "gemini": ("gemini-3.5-flash-lite", "gemini-3.6-flash"),
}
# Free-tier Gemini quotas are per minute as well as per day, so calls are spaced out by default.
DEFAULT_RPM = {"anthropic": 0, "gemini": 10}


@dataclass
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    tavily_api_key: str | None = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    telegram_bot_token: str | None = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    youtube_api_key: str | None = field(default_factory=lambda: _env("YOUTUBE_API_KEY"))

    db_path: Path = field(default_factory=lambda: Path(_env("SCOUT_DB", str(ROOT / "data" / "scout.db"))))
    # auto = Claude when ANTHROPIC_API_KEY is set, otherwise Gemini.
    llm_provider: str = field(default_factory=lambda: _env("SCOUT_LLM_PROVIDER", "auto").lower())
    extract_model: str | None = field(default_factory=lambda: _env("SCOUT_EXTRACT_MODEL"))
    analysis_model: str | None = field(default_factory=lambda: _env("SCOUT_ANALYSIS_MODEL"))
    analysis_effort: str = field(default_factory=lambda: _env("SCOUT_ANALYSIS_EFFORT", "medium"))

    max_tavily_credits_per_day: int = field(default_factory=lambda: int(_env("SCOUT_MAX_TAVILY_PER_DAY", "30")))
    max_llm_usd_per_month: float = field(default_factory=lambda: float(_env("SCOUT_MAX_LLM_USD_PER_MONTH", "12")))
    max_candidates_per_agent: int = field(default_factory=lambda: int(_env("SCOUT_MAX_CANDIDATES", "25")))
    workers: int = field(default_factory=lambda: int(_env("SCOUT_WORKERS", "4")))
    llm_rpm: int = field(default_factory=lambda: int(_env("SCOUT_LLM_RPM", "-1")))  # -1 = provider default

    digest_hour: int = 10
    digest_minute: int = 0

    def __post_init__(self) -> None:
        if self.llm_provider not in DEFAULT_MODELS:
            self.llm_provider = "anthropic" if self.anthropic_api_key else "gemini"
        extract, analysis = DEFAULT_MODELS[self.llm_provider]
        self.extract_model = self.extract_model or extract
        self.analysis_model = self.analysis_model or analysis
        if self.llm_rpm < 0:
            self.llm_rpm = DEFAULT_RPM[self.llm_provider]

    @property
    def llm_key_field(self) -> str:
        """Settings attribute holding the API key for the chosen provider."""
        return f"{self.llm_provider}_api_key"

    def missing(self, *names: str) -> list[str]:
        return [n for n in names if not getattr(self, n)]


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def profile() -> dict:
    return load_yaml("profile")
