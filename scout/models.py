"""Structured-output schemas for Claude. Every critical fact carries a verbatim quote for verification."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Q = "Exact sentence copied verbatim from the source text that states this, or null."
ISO = "ISO date YYYY-MM-DD only if the source states it explicitly, else null."


# ---- Agent 1: internships ---------------------------------------------------

class Internship(BaseModel):
    title: str = Field(description="Name of the internship / training / research programme.")
    organisation: str
    org_type: Literal["company", "research_institute", "university", "government", "other"]
    location: str | None = Field(description="City and state as stated, or null.")
    mode: Literal["onsite", "remote", "hybrid", "not_stated"]
    focus_areas: list[str] = Field(description="Technical areas, e.g. PEB, steel structures, BIM, EPC.")
    period: str | None = Field(description="Internship period/duration as stated, or null.")
    start_date: str | None = Field(description=ISO)
    end_date: str | None = Field(description=ISO)
    stipend: str | None
    stipend_quote: str | None = Field(description=Q)
    fee: str | None = Field(description="Any fee the intern must pay, or null.")
    fee_quote: str | None = Field(description=Q)
    deadline: str | None = Field(description="Application deadline. " + ISO)
    deadline_quote: str | None = Field(description=Q)
    eligibility: str | None = Field(description="Stated eligibility (degree, year, marks), concise.")
    eligibility_quote: str | None = Field(description=Q)
    international_eligibility: Literal["explicitly_open", "indian_nationals_only", "not_stated"] = Field(
        description="Only 'explicitly_open' or 'indian_nationals_only' if the source says so in words.")
    international_quote: str | None = Field(description=Q)
    final_year_eligible: Literal["yes", "no", "not_stated"] = Field(
        description="Can a final-year B.E./B.Tech civil student apply, per the stated eligibility?")
    application_url: str | None = Field(description="Apply/notice link copied exactly from the source text, or null.")
    how_to_apply: str | None
    is_open_opportunity: bool = Field(
        description="True only if this is a concrete internship/training/research opening someone can apply to.")
    relevance: int = Field(description="0-10 fit for the student profile in the instructions.")
    relevance_reason: str


class InternshipPage(BaseModel):
    opportunities: list[Internship]


# ---- Agent 2: startup programmes -------------------------------------------

class Programme(BaseModel):
    name: str
    provider: str
    programme_type: Literal[
        "course", "certificate", "bootcamp", "workshop", "accelerator", "incubator", "pre_incubation",
        "competition", "hackathon", "grant", "fellowship", "scheme", "other",
    ]
    mode: Literal["online", "offline", "hybrid", "not_stated"]
    location: str | None
    cost: Literal["free", "fully_funded", "free_with_paid_certificate", "paid", "not_stated"]
    fee_details: str | None
    fee_quote: str | None = Field(description=Q)
    certificate: Literal["free_certificate", "paid_certificate", "no_certificate", "not_stated"]
    funding: str | None = Field(description="Grant/prize/funding amount offered, or null.")
    funding_quote: str | None = Field(description=Q)
    equity: str | None = Field(description="Equity taken, e.g. 'no equity' or '6%', or null if not stated.")
    equity_quote: str | None = Field(description=Q)
    deadline: str | None = Field(description="Application deadline. " + ISO)
    deadline_quote: str | None = Field(description=Q)
    start_date: str | None = Field(description=ISO)
    duration: str | None
    eligibility: str | None
    eligibility_quote: str | None = Field(description=Q)
    foreigner_eligibility: Literal["explicitly_open", "indian_only", "not_stated"] = Field(
        description="Only set if the source says so in words (e.g. 'Indian citizens', 'DPIIT-recognised').")
    foreigner_quote: str | None = Field(description=Q)
    practical_value: str = Field(description="One line: what a first-time founder concretely gets.")
    application_url: str | None = Field(description="Link copied exactly from the source text, or null.")
    is_open_opportunity: bool = Field(description="True only for a concrete programme someone can join/apply to.")
    relevance: int = Field(description="0-10 fit for the founder profile in the instructions.")
    relevance_reason: str


class ProgrammePage(BaseModel):
    programmes: list[Programme]


# ---- Agent 3: startup intelligence -----------------------------------------

class StartupStory(BaseModel):
    is_startup_story: bool = Field(description="True only if the article is mainly about one specific startup.")
    startup_name: str
    founders: list[str]
    country: str | None
    hq_city: str | None
    sector: str
    event_type: Literal[
        "funding_round", "valuation", "acquisition", "ipo", "revenue_milestone", "profitability",
        "down_round", "layoffs", "shutdown", "other",
    ]
    event_summary: str = Field(description="One factual sentence: what happened.")
    event_date: str | None = Field(description=ISO)
    round_stage: str | None = Field(description="e.g. Seed, Series A, or null.")
    amount_raised: str | None = Field(description="Funding raised with currency, or null. Never revenue.")
    amount_quote: str | None = Field(description=Q)
    investors: list[str]
    valuation: str | None
    valuation_status: Literal["confirmed", "reported", "estimated", "not_disclosed"] = Field(
        description="confirmed = company/investor/filing states it; reported = media citing sources; "
                    "estimated = third-party estimate.")
    valuation_quote: str | None = Field(description=Q)
    revenue: str | None = Field(description="Revenue figure with period and currency, or null. Never funding.")
    revenue_quote: str | None = Field(description=Q)
    problem: str | None
    business_model: str | None
    product: str | None
    customers: str | None
    challenges: str | None = Field(description="Losses, down-rounds, layoffs, regulatory issues stated, or null.")
    relevance: int = Field(description="0-10 interest for the founder profile (sector fit, lessons).")


# ---- Analysis (strong model) ------------------------------------------------

class Advice(BaseModel):
    id: int
    priority: int = Field(description="1 (low) to 5 (act today).")
    why_it_matters: str = Field(description="<= 25 words, specific to the profile.")
    action: str = Field(description="<= 20 words, a concrete next step with any date from the data.")
    concerns: str | None = Field(description="Eligibility/fee/verification caveats from the data, or null.")


class AdviceList(BaseModel):
    items: list[Advice]


class StartupAnalysis(BaseModel):
    id: int
    growth_story: str = Field(description="2-3 sentences on how it grew, using only the article's facts.")
    competitive_advantage: str
    lessons: list[str] = Field(description="2-3 concrete lessons for an early founder.")
    speculative: str | None = Field(description="Which claims are estimates/unconfirmed, or null.")
    priority: int = Field(description="1-5 how worth reading this is for the profile.")


class StartupAnalysisList(BaseModel):
    items: list[StartupAnalysis]
