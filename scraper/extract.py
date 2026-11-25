"""Config-driven extraction: CSS selectors per site live in JSON, not in code."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from .normalize import PriceParseError, detect_language, normalize_title, parse_price


@dataclass
class Product:
    shop: str
    url: str
    title: str | None
    price_raw: str | None
    amount: str | None          # Decimal stored as string to keep exact cents
    currency: str | None
    sku: str | None
    availability: str | None
    language: str
    issues: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Business key used for dedup: SKU if the site has one, else the clean title."""
        return f"sku:{self.sku}" if self.sku else f"title:{normalize_title(self.title or self.url)}"

    @property
    def content_hash(self) -> str:
        """Hash of the fields that matter; changes only when the data really changes."""
        payload = "|".join(str(x) for x in (self.title, self.amount, self.currency, self.availability))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"], d["content_hash"] = self.key, self.content_hash
        return d


def _select(soup: BeautifulSoup, spec) -> str | None:
    """spec is either "css selector" or {"css": ..., "attr": ..., "regex": ...}."""
    if spec is None:
        return None
    if isinstance(spec, str):
        spec = {"css": spec}
    el = soup.select_one(spec["css"])
    if el is None:
        return None
    value = el.get(spec["attr"]) if spec.get("attr") else el.get_text(" ", strip=True)
    if value and spec.get("regex"):
        m = re.search(spec["regex"], value)
        value = m.group(1) if m else None
    return value.strip() if isinstance(value, str) else value


def parse_listing(html: str, cfg: dict) -> tuple[list[str], str | None]:
    """Return (product links, next-page link) from a listing page."""
    soup = BeautifulSoup(html, "lxml")
    links = [a.get("href") for a in soup.select(cfg["listing"]["item_link"]) if a.get("href")]
    nxt = soup.select_one(cfg["listing"]["next_page"]) if cfg["listing"].get("next_page") else None
    return links, (nxt.get("href") if nxt else None)


def parse_product(html: str, url: str, cfg: dict) -> Product:
    soup = BeautifulSoup(html, "lxml")
    fields = cfg["product"]
    title = _select(soup, fields.get("title"))
    price_raw = _select(soup, fields.get("price"))
    html_lang = soup.html.get("lang") if soup.html else None
    language = detect_language(html_lang, soup.get_text(" ", strip=True)[:2000])

    p = Product(shop=cfg["name"], url=url, title=title, price_raw=price_raw, amount=None,
                currency=None, sku=_select(soup, fields.get("sku")),
                availability=_select(soup, fields.get("availability")), language=language)

    if not title:
        p.issues.append("missing_title")
    if not price_raw:
        p.issues.append("missing_price")
    else:
        try:
            amount, currency = parse_price(price_raw, cfg.get("default_currency"))
            p.amount, p.currency = str(amount), currency
            if amount <= 0 or amount > 10_000_000:
                p.issues.append("price_out_of_range")
            if currency is None:
                p.issues.append("missing_currency")
        except PriceParseError:
            p.issues.append("unparseable_price")
    return p
