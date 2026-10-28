"""Verification helpers: check that extracted facts are backed by the source text."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin

from .dedupe import canonical_url
from .timeutil import parse_date

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
DATE_FIELDS = {"deadline", "start_date", "end_date", "event_date"}

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " ", "₹": "rs "})


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    text = re.sub(r"[^\w%./₹$-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _shingles(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def quote_supported(quote: str | None, source_text: str | None, threshold: float = 0.75) -> bool:
    """True when the quote (or almost all of it) appears verbatim in the source text.

    Exact substring after normalisation passes; otherwise at least `threshold` of the
    quote's word 3-grams must occur in the source (tolerates small extraction differences).
    """
    if not quote or not source_text:
        return False
    nq, nt = _norm(quote), _norm(source_text)
    if len(nq) < 4:
        return False
    if nq in nt:
        return True
    qw, tw = nq.split(), nt.split()
    n = 3 if len(qw) >= 3 else len(qw)
    qs = _shingles(qw, n)
    ts = _shingles(tw, n)
    return len(qs & ts) / len(qs) >= threshold


def date_matches_quote(value: str | None, quote: str | None) -> bool:
    """The ISO date the model wrote must be the date its quote states (day, month and any year)."""
    d = parse_date(value)
    if d is None or not quote:
        return False
    q = quote.lower()
    day_ok = re.search(rf"(?<!\d)0?{d.day}(st|nd|rd|th)?(?!\d)", q)
    month_ok = MONTHS[d.month - 1] in q or re.search(rf"(?<!\d)0?{d.month}(?!\d)", q)
    years = set(re.findall(r"(?<!\d)(20\d\d)(?!\d)", q))
    year_ok = not years or str(d.year) in years
    return bool(day_ok and month_ok and year_ok)


def check_fields(record: dict, source_text: str | None, pairs: dict[str, str]) -> tuple[list[str], list[str]]:
    """For each value field with a quote field, null the value unless its quote is in the source.

    Date fields additionally need the quote to state that same date. Returns
    (verified_fields, unverified_fields); empty fields are ignored.
    """
    verified, unverified = [], []
    for field, quote_field in pairs.items():
        value = record.get(field)
        if value in (None, "", [], "not_stated", "unknown"):
            continue
        quote = record.get(quote_field)
        if field in DATE_FIELDS and not date_matches_quote(value, quote):
            unverified.append(field)
            record[field] = None
            continue
        if quote_supported(quote, source_text):
            verified.append(field)
        else:
            unverified.append(field)
            record[field] = None
            record[quote_field] = None
    return verified, unverified


def trusted_link(url: str | None, source_text: str | None, page_url: str) -> str:
    """The extracted apply link (made absolute) if it appears in the page, else the page URL."""
    if url and url_in_source(url, source_text):
        absolute = urljoin(page_url, url.strip())
        if absolute.startswith(("http://", "https://")):
            return absolute
    return page_url


def url_in_source(url: str | None, source_text: str | None) -> bool:
    """An extracted link is only trusted if it literally appears in the fetched page."""
    if not url or not source_text:
        return False
    if url in source_text:
        return True
    canon = canonical_url(url)
    return any(canonical_url(m) == canon for m in re.findall(r"https?://[^\s)\]\"'>]+", source_text))
