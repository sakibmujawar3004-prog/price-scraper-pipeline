"""SQLite-backed HTTP response cache.

* Fresh entry (younger than TTL)  -> served from disk, no network call at all.
* Stale entry with ETag/Last-Modified -> conditional request; a 304 reuses the body.
This cuts repeat crawl cost, which matters when every request is a paid API call
or risks a rate limit.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass


@dataclass
class CachedResponse:
    url: str
    status: int
    body: str
    etag: str | None
    last_modified: str | None
    fetched_at: float


class ResponseCache:
    def __init__(self, path: str, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS http_cache (
                   url TEXT PRIMARY KEY, status INTEGER, body TEXT,
                   etag TEXT, last_modified TEXT, fetched_at REAL)"""
        )

    def get(self, url: str) -> CachedResponse | None:
        row = self.db.execute(
            "SELECT url, status, body, etag, last_modified, fetched_at "
            "FROM http_cache WHERE url = ?", (url,)
        ).fetchone()
        return CachedResponse(*row) if row else None

    def is_fresh(self, entry: CachedResponse) -> bool:
        return (time.time() - entry.fetched_at) < self.ttl

    def put(self, url, status, body, etag=None, last_modified=None):
        self.db.execute(
            "INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?, ?, ?)",
            (url, status, body, etag, last_modified, time.time()),
        )
        self.db.commit()

    def touch(self, url: str):
        self.db.execute("UPDATE http_cache SET fetched_at = ? WHERE url = ?", (time.time(), url))
        self.db.commit()

    def close(self):
        self.db.close()
