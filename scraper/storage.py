"""SQLite storage with upserts and a price-history table (change data capture)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .extract import Product

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    shop TEXT, product_key TEXT, url TEXT, title TEXT, amount TEXT, currency TEXT,
    sku TEXT, availability TEXT, language TEXT, content_hash TEXT,
    first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (shop, product_key)
);
CREATE TABLE IF NOT EXISTS price_history (
    shop TEXT, product_key TEXT, amount TEXT, currency TEXT, seen_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_hist ON price_history (shop, product_key, seen_at);
"""


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def upsert(self, products: list[Product]) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        for p in products:
            row = self.db.execute(
                "SELECT content_hash, amount FROM products WHERE shop=? AND product_key=?",
                (p.shop, p.key)).fetchone()
            if row is None:
                counts["inserted"] += 1
                self.db.execute(
                    "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (p.shop, p.key, p.url, p.title, p.amount, p.currency, p.sku,
                     p.availability, p.language, p.content_hash, now, now))
                self._history(p, now)
            elif row[0] != p.content_hash:
                counts["updated"] += 1
                self.db.execute(
                    """UPDATE products SET url=?, title=?, amount=?, currency=?, availability=?,
                       content_hash=?, last_seen=? WHERE shop=? AND product_key=?""",
                    (p.url, p.title, p.amount, p.currency, p.availability, p.content_hash,
                     now, p.shop, p.key))
                if row[1] != p.amount:
                    self._history(p, now)
            else:
                counts["unchanged"] += 1
                self.db.execute("UPDATE products SET last_seen=? WHERE shop=? AND product_key=?",
                                (now, p.shop, p.key))
        self.db.commit()
        return counts

    def _history(self, p: Product, now: str):
        if p.amount is not None:
            self.db.execute("INSERT INTO price_history VALUES (?,?,?,?,?)",
                            (p.shop, p.key, p.amount, p.currency, now))

    def close(self):
        self.db.close()
