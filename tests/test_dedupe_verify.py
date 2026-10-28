from scout.dedupe import canonical_url, fingerprint, same_org, similarity
from scout.verify import check_fields, date_matches_quote, quote_supported, url_in_source


def test_canonical_url_strips_tracking_and_normalises():
    a = canonical_url("http://www.Example.com/jobs/123/?utm_source=x&b=2&a=1#top")
    b = canonical_url("https://example.com/jobs/123?a=1&b=2")
    assert a == b == "https://example.com/jobs/123?a=1&b=2"


def test_fingerprint_ignores_case_and_punctuation():
    assert fingerprint("internships", "L&T Construction", "Summer Internship 2027") == \
        fingerprint("internships", "l&t construction", "Summer internship, 2027")


def test_fuzzy_duplicate_helpers():
    assert same_org("CSIR-SERC", "CSIR - SERC Chennai")
    assert similarity("Summer Research Internship 2027 – Structural Engg",
                      "Summer Research Internship 2027 Structural Engg") >= 0.7
    assert similarity("BIM internship", "Steel detailing trainee") < 0.3


SOURCE = """CSIR-SERC invites applications for Summer Internship during May/June 2027.
Eligibility: Students who have completed 6th semester of B.E./B.Tech.
Last date for receipt of applications: 15 April 2027. The Internship does not carry any stipend.
Apply at https://serc.res.in/internship-2027"""


def test_quote_supported_exact_and_fuzzy():
    assert quote_supported("Last date for receipt of applications: 15 April 2027.", SOURCE)
    assert quote_supported("last date for receipt of applications : 15 april 2027", SOURCE)
    assert not quote_supported("Open to foreign nationals.", SOURCE)
    assert not quote_supported(None, SOURCE)


def test_check_fields_nulls_unsupported_values():
    record = {
        "deadline": "2027-04-15", "deadline_quote": "Last date for receipt of applications: 15 April 2027.",
        "stipend": "Rs 10,000", "stipend_quote": "Stipend of Rs 10,000 per month",  # invented
        "international_eligibility": "not_stated", "international_quote": None,
    }
    verified, unverified = check_fields(record, SOURCE, {
        "deadline": "deadline_quote", "stipend": "stipend_quote",
        "international_eligibility": "international_quote"})
    assert verified == ["deadline"]
    assert unverified == ["stipend"]
    assert record["stipend"] is None and record["deadline"] == "2027-04-15"


def test_url_must_appear_in_source():
    assert url_in_source("https://serc.res.in/internship-2027", SOURCE)
    assert not url_in_source("https://serc.res.in/made-up", SOURCE)


def test_date_must_match_its_quote():
    assert date_matches_quote("2027-04-15", "Last date for receipt of applications: 15 April 2027.")
    assert date_matches_quote("2026-11-30", "Apply by 30/11/2026")
    assert date_matches_quote("2026-11-30", "Applications close on November 30th")
    assert not date_matches_quote("2026-10-01", "Last date to apply: 20 November 2026.")
    assert not date_matches_quote("2026-11-20", "Last date to apply: 20 November 2025.")
