"""Command-line entry point.

Examples
    python run.py --demo                      # start the bundled demo shops and scrape them
    python run.py --demo --concurrency 1      # same, one request at a time (benchmark)
    python run.py --config configs/books_toscrape.json --max-pages 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from scraper.pipeline import run

ROOT = Path(__file__).parent


def load_configs(paths: list[str], base_url: str | None) -> list[dict]:
    configs = []
    for path in paths:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        items = cfg if isinstance(cfg, list) else [cfg]
        for c in items:
            if base_url:
                c["start_urls"] = [u.replace("{BASE}", base_url) for u in c["start_urls"]]
            configs.append(c)
    return configs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", nargs="*", default=[], help="site config JSON file(s)")
    ap.add_argument("--demo", action="store_true", help="serve and scrape the bundled demo shops")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--max-pages", type=int, default=50, help="listing pages per start URL")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "output" / "products.db"))
    ap.add_argument("--cache-db", default=str(ROOT / "output" / "http_cache.db"))
    ap.add_argument("--report-dir", default=str(ROOT / "output"))
    args = ap.parse_args()

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    server, base = None, None
    if args.demo:
        from demo_site.server import start_server
        server, base = start_server()
        args.config = args.config or [str(ROOT / "configs" / "demo_shops.json")]
    if not args.config:
        ap.error("give --config or --demo")

    try:
        report = asyncio.run(run(load_configs(args.config, base), args.db,
                                 None if args.no_cache else args.cache_db,
                                 args.concurrency, args.max_pages, args.report_dir))
    finally:
        if server:
            server.shutdown()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
