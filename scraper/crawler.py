"""Async crawler: bounded concurrency, retries with backoff, robots.txt, caching."""
from __future__ import annotations

import asyncio
import random
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from .cache import ResponseCache

TRACKING_PARAMS = {"ref", "utm_source", "utm_medium", "utm_campaign", "utm_content",
                   "gclid", "fbclid", "sessionid", "sid"}
RETRY_STATUSES = {429, 500, 502, 503, 504}


def canonical_url(url: str, base: str | None = None) -> str:
    """Absolute URL, lower-case host, no fragment, no tracking params, sorted query.

    Two links to the same page with different ?ref= tags collapse to one URL,
    so we never fetch (or store) the same page twice.
    """
    if base:
        url = urljoin(base, url)
    p = urlparse(url)
    query = sorted((k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING_PARAMS)
    path = p.path or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, "", urlencode(query), ""))


@dataclass
class CrawlStats:
    requests_sent: int = 0
    cache_hits: int = 0          # served from disk, no network
    not_modified: int = 0        # 304 revalidations
    retries: int = 0
    failures: int = 0
    blocked_by_robots: int = 0
    duplicate_urls_skipped: int = 0
    started: float = field(default_factory=time.perf_counter)

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started


class Crawler:
    def __init__(self, cache: ResponseCache | None, concurrency: int = 10,
                 max_retries: int = 3, timeout: float = 15.0,
                 user_agent: str = "PriceScraperPipeline/1.0 (+portfolio project)",
                 respect_robots: bool = True):
        self.cache = cache
        self.sem = asyncio.Semaphore(concurrency)
        self.max_retries = max_retries
        self.respect_robots = respect_robots
        self.client = httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                        headers={"User-Agent": user_agent})
        self.user_agent = user_agent
        self.stats = CrawlStats()
        self._seen: set[str] = set()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    async def close(self):
        await self.client.aclose()

    def claim(self, url: str) -> bool:
        """Return True the first time a canonical URL is seen, False afterwards."""
        if url in self._seen:
            self.stats.duplicate_urls_skipped += 1
            return False
        self._seen.add(url)
        return True

    async def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = await self.client.get(root + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])
            self._robots[root] = rp
        return self._robots[root].can_fetch(self.user_agent, url)

    async def fetch(self, url: str) -> str | None:
        """Fetch a page's HTML, using the cache where possible. None on failure."""
        if not await self._allowed(url):
            self.stats.blocked_by_robots += 1
            return None

        cached = self.cache.get(url) if self.cache else None
        if cached and self.cache.is_fresh(cached):
            self.stats.cache_hits += 1
            return cached.body

        headers = {}
        if cached and cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified

        async with self.sem:
            for attempt in range(self.max_retries + 1):
                try:
                    self.stats.requests_sent += 1
                    r = await self.client.get(url, headers=headers)
                    if r.status_code == 304 and cached:
                        self.stats.not_modified += 1
                        self.cache.touch(url)
                        return cached.body
                    if r.status_code in RETRY_STATUSES and attempt < self.max_retries:
                        raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                    if r.status_code != 200:
                        self.stats.failures += 1
                        return None
                    if self.cache:
                        self.cache.put(url, r.status_code, r.text,
                                       r.headers.get("etag"), r.headers.get("last-modified"))
                    return r.text
                except (httpx.TransportError, httpx.HTTPStatusError):
                    if attempt == self.max_retries:
                        self.stats.failures += 1
                        return None
                    self.stats.retries += 1
                    # Exponential backoff with jitter: 0.5s, 1s, 2s ... (+ up to 0.25s)
                    await asyncio.sleep(0.5 * 2 ** attempt + random.random() * 0.25)
        return None
