"""Polite page fetching: robots.txt, per-host rate limit, retries, main-text extraction (HTML and PDF)."""

from __future__ import annotations

import io
import logging
import threading
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlsplit

import httpx
import trafilatura

from ..dedupe import domain

log = logging.getLogger(__name__)

USER_AGENT = "OpportunityScoutBot/1.0 (+https://github.com/victorsimba189-lab/Opportunity-Ai-agent.)"

# Platforms whose terms or login walls rule out automated fetching; we keep search snippets only.
NO_FETCH_DOMAINS = (
    "linkedin.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "threads.net",
    "youtube.com", "youtu.be", "naukri.com", "indeed.com", "glassdoor.com", "glassdoor.co.in",
)

MAX_TEXT_CHARS = 24000


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300 and bool(self.text.strip())


def is_no_fetch(url: str) -> bool:
    host = domain(url)
    return any(host == d or host.endswith("." + d) for d in NO_FETCH_DOMAINS)


def make_client(timeout: float = 20.0, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"},
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    )


def request_with_retry(client: httpx.Client, method: str, url: str, attempts: int = 3, **kwargs) -> httpx.Response:
    """Retry connection errors, 429 and 5xx with exponential backoff (honours Retry-After)."""
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.TransportError:
            if attempt == attempts:
                raise
        else:
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            if attempt == attempts:
                return resp
            retry_after = resp.headers.get("retry-after", "")
            if retry_after.isdigit():
                delay = min(float(retry_after), 60.0)
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def html_to_text(html: str, url: str | None = None) -> str:
    text = trafilatura.extract(
        html, url=url, output_format="markdown", include_links=True, include_tables=True,
        favor_recall=True, with_metadata=False,
    )
    return (text or "").strip()


def pdf_to_text(data: bytes, max_pages: int = 20) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:max_pages]
    return "\n".join((p.extract_text() or "") for p in pages).strip()


class Fetcher:
    def __init__(self, client: httpx.Client | None = None, min_interval: float = 1.0):
        self.client = client or make_client()
        self.min_interval = min_interval
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if base not in self._robots:
                rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
                try:
                    resp = self.client.get(f"{base}/robots.txt", timeout=10)
                    if resp.status_code in (401, 403):
                        rp.disallow_all = True
                    elif resp.status_code >= 400:
                        rp.allow_all = True
                    else:
                        rp.parse(resp.text.splitlines())
                except httpx.HTTPError:
                    rp = None  # robots unreachable: proceed politely
                self._robots[base] = rp
            rp = self._robots[base]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def _throttle(self, host: str) -> None:
        with self._lock:
            wait = self._last_hit.get(host, 0) + self.min_interval - time.monotonic()
            self._last_hit[host] = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)

    def fetch(self, url: str) -> Page:
        if is_no_fetch(url):
            return Page(url, url, 0, error="not fetched (platform restrictions)")
        try:
            if not self._allowed(url):
                return Page(url, url, 0, error="blocked by robots.txt")
            self._throttle(domain(url))
            resp = request_with_retry(self.client, "GET", url)
        except httpx.HTTPError as exc:
            return Page(url, url, 0, error=f"{type(exc).__name__}: {exc}"[:200])
        final = str(resp.url)
        if resp.status_code >= 400:
            return Page(url, final, resp.status_code, error=f"HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "").lower()
        try:
            if "pdf" in ctype or final.lower().endswith(".pdf"):
                text = pdf_to_text(resp.content)
            elif "html" in ctype or "xml" in ctype or not ctype:
                text = html_to_text(resp.text, final)
            elif ctype.startswith("text/"):
                text = resp.text
            else:
                return Page(url, final, resp.status_code, error=f"unsupported content-type {ctype}")
        except Exception as exc:  # parsers can fail on malformed documents
            return Page(url, final, resp.status_code, error=f"parse error: {exc}"[:200])
        if not text:
            return Page(url, final, resp.status_code, error="no readable text")
        return Page(url, final, resp.status_code, text=text[:MAX_TEXT_CHARS])
