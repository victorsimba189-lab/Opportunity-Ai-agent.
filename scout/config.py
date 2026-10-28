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


@dataclass
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    tavily_api_key: str | None = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    telegram_bot_token: str | None = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    youtube_api_key: str | None = field(default_factory=lambda: _env("YOUTUBE_API_KEY"))

    db_path: Path = field(default_factory=lambda: Path(_env("SCOUT_DB", str(ROOT / "data" / "scout.db"))))
    extract_model: str = field(default_factory=lambda: _env("SCOUT_EXTRACT_MODEL", "claude-haiku-5-5"))
    analysis_model: str = field(default_factory=lambda: _env("SCOUT_ANALYSIS_MODEL", "claude-opus-5-5"))
    analysis_effort: str = field(default_factory=lambda: _env("SCOUT_ANALYSIS_EFFORT", "medium"))

    max_tavily_credits_per_day: int = field(default_factory=lambda: int(_env("SCOUT_MAX_TAVILY_PER_DAY", "30")))
    max_llm_usd_per_month: float = field(default_factory=lambda: float(_env("SCOUT_MAX_LLM_USD_PER_MONTH", "12")))
    max_candidates_per_agent: int = field(default_factory=lambda: int(_env("SCOUT_MAX_CANDIDATES", "25")))
    workers: int = field(default_factory=lambda: int(_env("SCOUT_WORKERS", "4")))

    digest_hour: int = 10
    digest_minute: int = 0

    def missing(self, *names: str) -> list[str]:
        return [n for n in names if not getattr(self, n)]


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def profile() -> dict:
    return load_yaml("profile")
