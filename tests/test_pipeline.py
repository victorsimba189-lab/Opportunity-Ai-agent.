from scout.agents.internships import InternshipScout
from scout.agents.startups import StartupScout
from scout.db import item_data
from scout.models import InternshipPage, StartupStory

from .fakes import FakeFetcher, FakeLLM, make_context

COMPANY_URL = "https://pebco.example.com/careers/intern"
PAGE = """PEBCo invites final year civil engineering students for a PEB design internship in Chennai.
Duration: 15 December 2026 to 15 February 2027. Stipend: Rs 12,000 per month.
Last date to apply: 20 November 2026. Open to all students including international students.
Apply: https://pebco.example.com/apply"""

INDIAN_ONLY_URL = "https://lab.example.org/notice"
INDIAN_PAGE = """Research internship at the Structures Lab, Bengaluru, January 2027.
Only Indian nationals are eligible to apply. Last date: 30 November 2026."""


def internship(**overrides):
    base = dict(
        title="PEB Design Internship", organisation="PEBCo", org_type="company", location="Chennai, Tamil Nadu",
        mode="onsite", focus_areas=["PEB"], period="15 Dec 2026 - 15 Feb 2027", start_date="2026-12-15",
        end_date="2027-02-15", stipend="Rs 12,000/month", stipend_quote="Stipend: Rs 12,000 per month.",
        fee=None, fee_quote=None, deadline="2026-11-20", deadline_quote="Last date to apply: 20 November 2026.",
        eligibility="Final year civil", eligibility_quote="PEBCo invites final year civil engineering students",
        international_eligibility="explicitly_open",
        international_quote="Open to all students including international students.",
        final_year_eligible="yes", application_url="https://pebco.example.com/apply", how_to_apply=None,
        is_open_opportunity=True, relevance=9, relevance_reason="PEB in Chennai")
    base.update(overrides)
    return base


QUERIES = {
    "q1": [
        {"title": "PEB intern", "url": COMPANY_URL + "?utm_source=tavily", "content": "PEB internship"},
        {"title": "Lab notice", "url": INDIAN_ONLY_URL, "content": "research internship"},
        {"title": "LinkedIn post", "url": "https://www.linkedin.com/posts/abc", "content":
            "Hiring civil interns for steel detailing in Hyderabad, apply by 25 November 2026"},
    ]
}


def test_internship_pipeline_verifies_filters_and_skips_seen(db, monkeypatch):
    llm = FakeLLM({
        "pebco.example.com": InternshipPage(opportunities=[internship()]),
        "lab.example.org": InternshipPage(opportunities=[internship(
            title="Structures Lab Research Internship", organisation="Structures Lab", location="Bengaluru",
            international_eligibility="indian_nationals_only",
            international_quote="Only Indian nationals are eligible to apply.",
            deadline="2026-11-30", deadline_quote="Last date: 30 November 2026.", stipend=None,
            stipend_quote=None, application_url=None)]),
        "linkedin.com": InternshipPage(opportunities=[internship(
            title="Steel detailing intern", organisation="SteelWorks", location="Hyderabad",
            stipend="Rs 50,000", stipend_quote="Stipend Rs 50,000",  # not in snippet -> must be dropped
            deadline="2026-11-25", deadline_quote="apply by 25 November 2026",
            international_eligibility="not_stated", international_quote=None, application_url=None)]),
    })
    fetcher = FakeFetcher({COMPANY_URL + "?utm_source=tavily": PAGE, INDIAN_ONLY_URL: INDIAN_PAGE})
    counter = []
    ctx = make_context(db, llm, fetcher, QUERIES, counter)
    scout = InternshipScout(ctx)
    monkeypatch.setitem(scout.cfg, "queries", ["q1"])
    monkeypatch.setitem(scout.cfg, "watchlist", [])
    result = scout.run()

    assert result.status == "ok", result.errors
    rows = {r["org"]: r for r in db.items("internships", statuses=None)}
    peb = rows["PEBCo"]
    assert peb["status"] == "active" and peb["eligibility_intl"] == "open"
    assert peb["url"] == "https://pebco.example.com/apply"  # link verified against page text
    assert peb["deadline"] == "2026-11-20" and peb["verification"] == "page"
    assert item_data(peb)["advice"]["action"].startswith("Email")
    assert rows["Structures Lab"]["status"] == "ineligible"
    linkedin = rows["SteelWorks"]
    assert linkedin["verification"] == "snippet"
    assert item_data(linkedin)["stipend"] is None  # unverifiable fact removed
    assert linkedin["deadline"] == "2026-11-25"  # supported by the snippet
    assert llm.extract_calls == 3 and len(counter) == 1

    # Second run the same day: search cached, URLs already seen -> no new LLM or Tavily calls.
    scout2 = InternshipScout(ctx)
    monkeypatch.setitem(scout2.cfg, "queries", ["q1"])
    monkeypatch.setitem(scout2.cfg, "watchlist", [])
    scout2.run()
    assert llm.extract_calls == 3 and len(counter) == 1
    assert len(db.items("internships", statuses=None)) == 3


def test_expired_and_out_of_window(db, monkeypatch, freeze):
    llm = FakeLLM({"pebco": InternshipPage(opportunities=[
        internship(title="Old", deadline="2026-10-01", deadline_quote="Last date to apply: 20 November 2026."),
        internship(title="Summer 2026 PEB internship", organisation="Other PEB Ltd", start_date="2026-05-01",
                   end_date="2026-06-30", deadline=None, deadline_quote=None),
    ])})
    ctx = make_context(db, llm, FakeFetcher({COMPANY_URL: PAGE}),
                       {"q": [{"title": "x", "url": COMPANY_URL, "content": ""}]})
    scout = InternshipScout(ctx)
    monkeypatch.setitem(scout.cfg, "queries", ["q"])
    monkeypatch.setitem(scout.cfg, "watchlist", [])
    scout.run()
    statuses = {r["title"]: r["status"] for r in db.items("internships", statuses=None)}
    # The "Old" quote states 20 November, not 1 October, so the date is dropped rather than trusted.
    old = next(r for r in db.items("internships", statuses=None) if r["title"] == "Old")
    assert old["deadline"] is None and "deadline" in item_data(old)["unverified_fields"]
    assert statuses["Summer 2026 PEB internship"] == "out_of_window"


def story(**overrides):
    base = dict(is_startup_story=True, startup_name="BuildAI", founders=["A. Rao"], country="India",
                hq_city="Bengaluru", sector="Construction Tech", event_type="funding_round",
                event_summary="BuildAI raised $12M Series A.", event_date="2026-10-27", round_stage="Series A",
                amount_raised="$12 million", amount_quote="BuildAI has raised $12 million in a Series A round",
                investors=["Example Ventures"], valuation="$60 million", valuation_status="reported",
                valuation_quote="valuing the company at about $60 million, according to people familiar",
                revenue=None, revenue_quote=None, problem="Site progress tracking", business_model="SaaS",
                product="AI site monitoring", customers="Developers", challenges=None, relevance=9)
    base.update(overrides)
    return base


ARTICLE = ("BuildAI has raised $12 million in a Series A round led by Example Ventures, "
           "valuing the company at about $60 million, according to people familiar with the matter.")


def test_startup_story_dedupes_across_outlets(db, monkeypatch):
    llm = FakeLLM({"inc42": story(), "entrackr": story()})
    llm.responses = {k: StartupStory(**v) for k, v in llm.responses.items()}
    pages = {"https://inc42.example/buildai": ARTICLE, "https://entrackr.example/buildai": ARTICLE}
    ctx = make_context(db, llm, FakeFetcher(pages), {"news": [
        {"title": "BuildAI raises $12M Series A", "url": "https://inc42.example/buildai", "content": "raises funding"},
        {"title": "BuildAI raises funding", "url": "https://entrackr.example/buildai", "content": "Series A"},
    ]})
    scout = StartupScout(ctx)
    monkeypatch.setitem(scout.cfg, "queries", ["news"])
    monkeypatch.setitem(scout.cfg, "feeds", [])
    scout.run()
    rows = db.items("startups", statuses=None)
    assert len(rows) == 1
    row = rows[0]
    assert row["sources_count"] == 2
    data = item_data(row)
    assert data["valuation_status"] == "reported" and data["amount_raised"] == "$12 million"
    assert data["analysis"]["lessons"] == ["Start narrow."]


def test_watchlist_follows_notice_links_and_resolves_relative_apply_links(db, monkeypatch):
    home = "https://institute.example.edu/"
    home_text = ("Welcome. [Summer Internship 2027 notice](/files/internship-2027.pdf) "
                 "[Tender for canteen](/files/tender.pdf)")
    notice = "https://institute.example.edu/files/internship-2027.pdf"
    notice_text = ("Summer internship for final year civil students, Chennai, May 2027. "
                   "Last date: 30 November 2026. Apply at [portal](/apply-2027)")
    full = internship(
        title="Institute Summer Internship 2027", organisation="Institute", location="Chennai",
        start_date="2027-05-01", end_date="2027-06-30", deadline="2026-11-30",
        deadline_quote="Last date: 30 November 2026.", stipend=None, stipend_quote=None,
        eligibility="final year civil", eligibility_quote="Summer internship for final year civil students",
        international_eligibility="not_stated", international_quote=None, application_url="/apply-2027")
    # The listing page mentions the same opening with fewer facts; the notice must win the merge.
    sparse = {**full, "deadline": None, "deadline_quote": None, "eligibility": None,
              "eligibility_quote": None, "application_url": None}
    llm = FakeLLM({f"URL: {notice}": InternshipPage(opportunities=[full]),
                   f"URL: {home}": InternshipPage(opportunities=[sparse])})
    fetcher = FakeFetcher({home: home_text, notice: notice_text})
    scout = InternshipScout(make_context(db, llm, fetcher))
    monkeypatch.setitem(scout.cfg, "queries", [])
    monkeypatch.setitem(scout.cfg, "watchlist", [{"name": "Institute", "url": home}])
    scout.run()
    assert notice in fetcher.fetched
    assert "https://institute.example.edu/files/tender.pdf" not in fetcher.fetched
    rows = db.items("internships")
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://institute.example.edu/apply-2027"
    assert row["deadline"] == "2026-11-30" and row["eligibility_intl"] == "unclear"
