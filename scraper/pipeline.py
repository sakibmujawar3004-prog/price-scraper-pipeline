"""Orchestration: crawl -> extract -> validate -> dedup -> store -> report."""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

from .cache import ResponseCache
from .crawler import Crawler, canonical_url
from .extract import Product, parse_listing, parse_product
from .storage import Store


async def crawl_site(crawler: Crawler, cfg: dict, max_pages: int) -> list[Product]:
    """Walk listing pages in order (each one tells us the next) while product
    pages are fetched concurrently in the background as soon as they're found."""
    tasks: list[asyncio.Task] = []

    async def fetch_product(url: str):
        html = await crawler.fetch(url)
        return parse_product(html, url, cfg) if html else None

    for start in cfg["start_urls"]:
        url, pages = canonical_url(start), 0
        while url and pages < max_pages:
            if not crawler.claim(url):
                break
            html = await crawler.fetch(url)
            if not html:
                break
            pages += 1
            links, nxt = parse_listing(html, cfg)
            for link in links:
                purl = canonical_url(link, base=url)
                if crawler.claim(purl):
                    tasks.append(asyncio.create_task(fetch_product(purl)))
            url = canonical_url(nxt, base=url) if nxt else None

    results = await asyncio.gather(*tasks)
    return [p for p in results if p is not None]


def dedup(products: list[Product]) -> tuple[list[Product], int]:
    """Same product reachable under different URLs (e.g. /p/123 and /item?id=123)
    shares a business key. Keep the first, count the rest."""
    seen, out = set(), []
    for p in products:
        k = (p.shop, p.key)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out, len(products) - len(out)


async def run(configs: list[dict], db_path: str, cache_path: str | None,
              concurrency: int, max_pages: int, report_dir: str) -> dict:
    cache = ResponseCache(cache_path, ttl_seconds=3600) if cache_path else None
    crawler = Crawler(cache, concurrency=concurrency)
    try:
        per_site = await asyncio.gather(*(crawl_site(crawler, c, max_pages) for c in configs))
    finally:
        await crawler.close()
    raw = [p for site in per_site for p in site]
    unique, dupes = dedup(raw)

    valid = [p for p in unique if not p.issues]
    store = Store(db_path)
    db_counts = store.upsert(unique)   # store everything; issues are flagged in the report
    store.close()
    if cache:
        cache.close()

    s = crawler.stats
    issues = Counter(i for p in unique for i in p.issues)
    report = {
        "sites": [c["name"] for c in configs],
        "concurrency": concurrency,
        "elapsed_seconds": round(s.elapsed, 2),
        "http_requests_sent": s.requests_sent,
        "cache_hits": s.cache_hits,
        "revalidated_304": s.not_modified,
        "retries": s.retries,
        "failed_fetches": s.failures,
        "blocked_by_robots": s.blocked_by_robots,
        "duplicate_urls_skipped": s.duplicate_urls_skipped,
        "products_extracted": len(raw),
        "duplicate_products_removed": dupes,
        "unique_products": len(unique),
        "valid_products": len(valid),
        "issues": dict(issues),
        "by_language": dict(Counter(p.language for p in unique)),
        "by_currency": dict(Counter(p.currency for p in unique if p.currency)),
        "db": db_counts,
    }
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "quality_report.json").write_text(json.dumps(report, indent=2))
    (out / "products.json").write_text(
        json.dumps([p.to_dict() for p in unique], indent=2, ensure_ascii=False))
    flagged = [p.to_dict() for p in unique if p.issues]
    (out / "flagged_records.json").write_text(json.dumps(flagged, indent=2, ensure_ascii=False))
    return report
