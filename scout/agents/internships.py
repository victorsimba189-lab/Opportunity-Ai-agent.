"""Agent 1: civil-engineering internships, training and research opportunities (Dec 2026 – 2027, South India)."""

from __future__ import annotations

from datetime import date

from .. import timeutil
from ..dedupe import canonical_url, fingerprint
from ..models import InternshipPage
from ..rank import rule_score
from ..sources.youtube import search_videos
from ..verify import check_fields, trusted_link
from .base import EXTRACTION_RULES, Candidate, Scout

QUOTED = {
    "deadline": "deadline_quote",
    "stipend": "stipend_quote",
    "fee": "fee_quote",
    "eligibility": "eligibility_quote",
    "international_eligibility": "international_quote",
}


class InternshipScout(Scout):
    name = "internships"
    label = "Civil engineering internships"

    def system_prompt(self) -> str:
        return (
            "You extract civil-engineering internship, industrial training and research-internship "
            "opportunities from one web page for this student:\n"
            f"{self.profile_text()}\n\n"
            "Priorities: pre-engineered buildings (PEB) and prefabrication, structural/steel engineering, "
            "construction management, EPC/infrastructure/industrial construction, BIM; research at IITs, NITs, "
            "IISc, CSIR labs. South India preferred. The student is a foreign national studying in India, so "
            "record exactly what the page says about nationality/citizenship and international students.\n"
            "Job ads for experienced hires, course adverts, news about past internships and listicles without a "
            "specific opening are not opportunities (is_open_opportunity=false).\n\n" + EXTRACTION_RULES
        )

    def extra_candidates(self) -> list[Candidate]:
        """Optional: recent YouTube videos (description text only) when YOUTUBE_API_KEY is set."""
        key = self.ctx.settings.youtube_api_key
        if not key:
            return []
        out = []
        for q in self.cfg.get("youtube_queries", []):
            try:
                videos = search_videos(self.ctx.http, key, q)
            except Exception as exc:
                self.error(f"YouTube search failed for {q!r}: {exc}")
                continue
            out += [Candidate(url=v.url, title=v.title, snippet=f"{v.channel}: {v.description}",
                              source=f"youtube:{q}", published=v.published) for v in videos]
        return out

    def extract(self, c: Candidate, text: str) -> list[dict]:
        page = self.ctx.llm.extract(InternshipPage, self.system_prompt(), self.source_block(c, text))
        return [o.model_dump() for o in page.opportunities] if page else []

    def window(self) -> tuple[date, date]:
        w = self.ctx.profile.get("internship_window", {})
        return (timeutil.parse_date(str(w.get("start", "2026-12-01"))),
                timeutil.parse_date(str(w.get("end", "2027-12-31"))))

    def to_item(self, c: Candidate, r: dict, text: str, basis: str) -> dict | None:
        if not r.get("is_open_opportunity"):
            self.stats["not_opportunity"] += 1
            return None
        verified, unverified = check_fields(r, text, QUOTED)
        r["verified_fields"], r["unverified_fields"] = verified, unverified
        intl = {"explicitly_open": "open", "indian_nationals_only": "indian_only"}.get(
            r.get("international_eligibility") or "", "unclear")

        status, reason = "active", None
        start, end = timeutil.parse_date(r.get("start_date")), timeutil.parse_date(r.get("end_date"))
        win_start, win_end = self.window()
        deadline = timeutil.parse_date(r.get("deadline"))
        if deadline and deadline < timeutil.today_ist():
            status, reason = "expired", "deadline passed"
        elif intl == "indian_only":
            status, reason = "ineligible", "Indian nationals only"
        elif r.get("final_year_eligible") == "no":
            status, reason = "ineligible", "not open to final-year students"
        elif (end and end < win_start) or (start and start > win_end):
            status, reason = "out_of_window", "outside Dec 2026 – 2027"
        elif (r.get("relevance") or 0) < self.min_relevance:
            status, reason = "low_relevance", r.get("relevance_reason")

        url = trusted_link(r.get("application_url"), text, c.url)
        blob = " ".join(str(r.get(k) or "") for k in ("title", "organisation", "location", "focus_areas",
                                                      "relevance_reason"))
        return {
            "agent": self.name,
            "fingerprint": fingerprint(self.name, r["organisation"], r["title"]),
            "title": r["title"],
            "org": r["organisation"],
            "url": url,
            "source_url": c.url,
            "canonical_url": canonical_url(url),
            "location": r.get("location"),
            "deadline": deadline.isoformat() if deadline else None,
            "start_date": start.isoformat() if start else None,
            "eligibility_intl": intl,
            "cost": "paid" if r.get("fee") else "free",
            "status": status,
            "status_reason": reason,
            "verification": basis,
            "relevance": r.get("relevance") or 0,
            "score": rule_score(blob=blob, relevance=r.get("relevance") or 0, cfg=self.cfg,
                                deadline=r.get("deadline"), verification=basis, eligibility=intl),
            "published_at": c.published,
            "data": {**r, "found_via": c.source},
        }
