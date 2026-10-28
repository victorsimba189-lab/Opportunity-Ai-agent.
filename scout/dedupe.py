"""URL canonicalisation, fingerprints and fuzzy duplicate detection."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src", "source", "igshid", "si",
    "trk", "trackingid", "refid", "_ga", "cmpid", "ito",
}

STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "in", "at", "to", "on", "with", "by", "&",
    "programme", "program", "internship", "internships", "intern", "applications", "apply", "open", "now",
}


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalise_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str | None) -> set[str]:
    return {t for t in normalise_text(text).split() if t not in STOPWORDS}


def similarity(a: str | None, b: str | None) -> float:
    """Jaccard similarity of significant tokens."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def fingerprint(agent: str, *parts: str | None) -> str:
    key = "|".join([agent, *(normalise_text(p) for p in parts)])
    return hashlib.sha1(key.encode()).hexdigest()


def same_org(a: str | None, b: str | None) -> bool:
    na, nb = normalise_text(a), normalise_text(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na or similarity(a, b) >= 0.6
