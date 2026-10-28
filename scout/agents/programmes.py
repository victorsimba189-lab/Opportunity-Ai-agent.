"""Agent 2: free / fully funded startup learning and founder opportunities in India."""

from __future__ import annotations

from .. import timeutil
from ..dedupe import canonical_url, fingerprint
from ..models import ProgrammePage
from ..rank import rule_score
from ..verify import check_fields, trusted_link
from .base import EXTRACTION_RULES, Candidate, Scout

QUOTED = {
    "deadline": "deadline_quote",
    "fee_details": "fee_quote",
    "funding": "funding_quote",
    "equity": "equity_quote",
    "eligibility": "eligibility_quote",
    "foreigner_eligibility": "foreigner_quote",
}


class ProgrammeScout(Scout):
    name = "programmes"
    label = "Free startup courses & founder opportunities"

    def system_prompt(self) -> str:
        return (
            "You extract startup learning and founder opportunities available in India from one web page: "
            "entrepreneurship courses and certificates, bootcamps, founder workshops, accelerators, incubators "
            "and pre-incubation, startup competitions, hackathons, grants, fellowships and government schemes.\n"
            f"Person:\n{self.profile_text()}\n\n"
            "Record fees precisely (including paid certificate exams), whether equity is taken, and what the "
            "page says about eligibility of foreign nationals or the need for an Indian-registered company.\n"
            "Generic articles, listicles without a specific programme, and paid courses dressed up as free "
            "are not opportunities unless the specific programme details are present.\n\n" + EXTRACTION_RULES
        )

    def extract(self, c: Candidate, text: str) -> list[dict]:
        page = self.ctx.llm.extract(ProgrammePage, self.system_prompt(), self.source_block(c, text))
        return [p.model_dump() for p in page.programmes] if page else []

    def to_item(self, c: Candidate, r: dict, text: str, basis: str) -> dict | None:
        if not r.get("is_open_opportunity"):
            self.stats["not_opportunity"] += 1
            return None
        verified, unverified = check_fields(r, text, QUOTED)
        r["verified_fields"], r["unverified_fields"] = verified, unverified
        foreign = {"explicitly_open": "open", "indian_only": "indian_only"}.get(
            r.get("foreigner_eligibility") or "", "unclear")
        cost = r.get("cost") or "not_stated"

        status, reason = "active", None
        deadline = timeutil.parse_date(r.get("deadline"))
        if deadline and deadline < timeutil.today_ist():
            status, reason = "expired", "deadline passed"
        elif cost == "paid":
            status, reason = "paid", r.get("fee_details") or "fee charged"
        elif foreign == "indian_only":
            status, reason = "ineligible", "Indian citizens/entities only"
        elif (r.get("relevance") or 0) < self.min_relevance:
            status, reason = "low_relevance", r.get("relevance_reason")

        url = trusted_link(r.get("application_url"), text, c.url)
        blob = " ".join(str(r.get(k) or "") for k in ("name", "provider", "programme_type", "location",
                                                      "practical_value"))
        score = rule_score(blob=blob, relevance=r.get("relevance") or 0, cfg=self.cfg,
                           deadline=r.get("deadline"), verification=basis, eligibility=foreign)
        if cost == "not_stated":
            score -= 5  # the brief asks for programmes that disclose they are free
        return {
            "agent": self.name,
            "fingerprint": fingerprint(self.name, r["provider"], r["name"]),
            "title": r["name"],
            "org": r["provider"],
            "url": url,
            "source_url": c.url,
            "canonical_url": canonical_url(url),
            "location": r.get("location") or r.get("mode"),
            "deadline": deadline.isoformat() if deadline else None,
            "start_date": r.get("start_date"),
            "eligibility_intl": foreign,
            "cost": cost,
            "status": status,
            "status_reason": reason,
            "verification": basis,
            "relevance": r.get("relevance") or 0,
            "score": score,
            "published_at": c.published,
            "data": {**r, "found_via": c.source},
        }
