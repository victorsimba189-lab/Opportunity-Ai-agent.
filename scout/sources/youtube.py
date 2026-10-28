"""Optional YouTube Data API v3 search (free quota: 10,000 units/day; one search = 100 units)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import httpx

from .. import timeutil
from .fetch import request_with_retry

API_URL = "https://www.googleapis.com/youtube/v3/search"


@dataclass
class Video:
    title: str
    url: str
    description: str
    published: str | None
    channel: str


def search_videos(client: httpx.Client, api_key: str, query: str, days: int = 30, max_results: int = 5) -> list[Video]:
    after = (timeutil.now_utc() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = request_with_retry(client, "GET", API_URL, params={
        "part": "snippet", "q": query, "type": "video", "order": "date", "regionCode": "IN",
        "publishedAfter": after, "maxResults": max_results, "key": api_key,
    })
    if resp.status_code >= 400:
        raise RuntimeError(f"YouTube HTTP {resp.status_code}")
    videos = []
    for item in resp.json().get("items", []):
        vid = item.get("id", {}).get("videoId")
        sn = item.get("snippet", {})
        if vid:
            videos.append(Video(
                title=sn.get("title", ""), url=f"https://www.youtube.com/watch?v={vid}",
                description=sn.get("description", ""), published=(sn.get("publishedAt") or "")[:10] or None,
                channel=sn.get("channelTitle", ""),
            ))
    return videos
