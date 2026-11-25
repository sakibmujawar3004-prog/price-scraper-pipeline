# Price Scraper Pipeline

An async web-scraping + ETL pipeline that collects product prices from several shops, turns messy multilingual prices into clean numbers, removes duplicates, caches responses, and writes a data-quality report.

It's built around the problems that usually break price-comparison and data-discovery platforms:

| Problem | How this project handles it |
|---|---|
| Slow crawling | `asyncio` + `httpx` with bounded concurrency (semaphore) |
| Structured price extraction from inconsistent sites | Per-site CSS selectors in JSON config; a price parser that works out the decimal separator itself |
| Multilingual pages | Language from `<html lang>`, with a stopword fallback when the attribute is missing; locale-aware number parsing |
| Duplicates | URL canonicalisation (drops `?ref=`, `utm_*` and fragments) + business-key dedup (SKU, or normalised title) |
| Repeated cost of re-fetching | SQLite response cache with TTL and `ETag`/`Last-Modified` revalidation |
| Flaky servers | Retries on 429/5xx with exponential backoff + jitter |
| Data accuracy | Validation flags (`missing_price`, `unparseable_price`, `price_out_of_range`, `missing_currency`) written to `flagged_records.json`, never silently dropped |
| Tracking changes over time | Upsert by business key + content hash; a `price_history` table records only real price changes |
| Polite crawling | Honours `robots.txt` and sends an honest User-Agent |

## Results (bundled demo shops)

Five demo shops, 230 pages, ~120 ms latency per request, and about 1 in 9 product pages fail once with HTTP 503:

| Run | Time | HTTP requests | Notes |
|---|---|---|---|
| Sequential (`--concurrency 1`) | 52.6 s | 263 | baseline |
| Async (`--concurrency 10`) | 6.4 s | 263 | **8.3× faster** |
| Async, second run with cache | 0.3 s | 0 | 230 cache hits, **100% of requests avoided** |

Data quality on the same run:
- 205 products extracted → **5 duplicates removed** → 200 unique products
- 24 duplicate URLs never fetched (tracking params, repeated "featured" links)
- 33 transient 503s retried successfully, **0 failed fetches**
- 10 records flagged for review: 5 "price on request" and 5 with no price element
- 1 URL skipped because `robots.txt` disallows it
- 5 languages detected (en, de, fr, hi, es), including one shop with no `lang` attribute
- 4 currencies normalised (GBP, EUR, INR, USD)

Examples of price parsing:

```
'£1,855.17'       -> 1855.17 GBP
'1.499,37 €'      -> 1499.37 EUR   (German format)
'2 337,09 €'      -> 2337.09 EUR   (French format, narrow no-break space)
'₹1,21,399'       -> 121399  INR   (Indian lakh grouping)
"CHF 1'299.90"    -> 1299.90 CHF   (Swiss format)
'Price on request' -> flagged as unparseable_price
```

## Architecture

```
 configs/*.json ──► Crawler (async, cache, retries, robots)
                        │ HTML
                        ▼
                 Extractor (CSS selectors per site)
                        │ raw fields
                        ▼
          Normaliser (price, currency, language, title)
                        │ Product records + issue flags
                        ▼
           Dedup (canonical URL + SKU/title key)
                        │
                        ▼
  SQLite: products (upsert) + price_history (changes only)
                        │
                        ▼
  output/quality_report.json · products.json · flagged_records.json
```

```
scraper/
  crawler.py    async fetching, URL canonicalisation, retries, robots.txt
  cache.py      SQLite HTTP cache (TTL + conditional requests)
  extract.py    config-driven parsing into Product records + validation
  normalize.py  price/currency parsing, language detection, title cleanup
  storage.py    upserts and price history
  pipeline.py   orchestration, dedup, report
demo_site/      generates and serves five deliberately messy test shops
configs/        site configs (demo shops + books.toscrape.com)
tests/          unit tests for price parsing, URL canonicalisation, language
```

## Run it

```bash
pip install -r requirements.txt

python run.py --demo                        # serve and scrape the demo shops
python run.py --demo --concurrency 1 --no-cache   # sequential benchmark
python run.py --config configs/books_toscrape.json --max-pages 3   # real practice site
pytest -q
```

To add a site, write a JSON config with its start URL and CSS selectors. No code changes are needed.

## What I'd add for production

- Playwright for pages rendered with JavaScript (behind the same `fetch` interface)
- Per-domain rate limits and proxy rotation
- Cost metrics per source (requests, bytes, paid API calls) to compare which sources earn their cost
- Scheduling with Airflow, plus alerting when the flagged-record rate jumps (usually a sign that a site's layout changed)
