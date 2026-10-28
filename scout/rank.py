"""Rule-based scoring. The strong model's priority is added on top in the advice step."""

from __future__ import annotations

from . import timeutil


def keyword_bonus(blob: str, weights: dict[str, float], cap: float) -> float:
    blob = blob.lower()
    return min(cap, sum(w for kw, w in weights.items() if kw.lower() in blob))


def rule_score(*, blob: str, relevance: int, cfg: dict, deadline: str | None = None,
               published: str | None = None, verification: str = "page", eligibility: str | None = None) -> float:
    score = max(0, min(relevance, 10)) * 6.0
    score += keyword_bonus(blob, cfg.get("keywords", {}), cap=15)
    score += keyword_bonus(blob, cfg.get("locations", {}), cap=6)
    if eligibility == "open":
        score += 6
    days = timeutil.days_until(timeutil.parse_date(deadline))
    if days is not None and days >= 0:
        score += 5 if days <= 7 else 3 if days <= 21 else 1
    age = timeutil.days_until(timeutil.parse_date(published))
    if age is not None:
        score += 4 if age >= -2 else 2 if age >= -7 else 0
    if verification == "snippet":
        score -= 4
    elif verification == "page":
        score += 3
    return round(score, 1)
