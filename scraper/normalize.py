"""Turn messy, multilingual price strings into (Decimal amount, ISO currency).

Handles formats such as:
    "$1,299.50"      -> 1299.50 USD
    "1.299,00 €"     -> 1299.00 EUR   (German style)
    "1 299,00 €"     -> 1299.00 EUR   (French style, with a narrow no-break space)
    "₹1,29,999"      -> 129999  INR   (Indian lakh grouping)
    "CHF 1'299.90"   -> 1299.90 CHF   (Swiss apostrophe)
    "£51.77"         -> 51.77   GBP
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = {
    "₹": "INR", "rs.": "INR", "rs": "INR", "inr": "INR",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
    "$": "USD", "usd": "USD", "us$": "USD",
    "chf": "CHF",
    "¥": "JPY", "jpy": "JPY",
}

# Longest tokens first so "us$" wins over "$" and "rs." over "rs".
_CURRENCY_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(CURRENCY_SYMBOLS, key=len, reverse=True)),
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d[\d.,'\s]*")
_SPACES = dict.fromkeys(map(ord, "    '"), None)


class PriceParseError(ValueError):
    pass


def detect_currency(text: str, default: str | None = None) -> str | None:
    m = _CURRENCY_RE.search(text)
    return CURRENCY_SYMBOLS[m.group(0).lower()] if m else default


def parse_amount(text: str) -> Decimal:
    """Parse the numeric part, working out which separator is the decimal one."""
    m = _NUMBER_RE.search(text)
    if not m:
        raise PriceParseError(f"no number in {text!r}")
    raw = m.group(0).strip().translate(_SPACES)

    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Both present: whichever comes last is the decimal separator.
        dec = "." if last_dot > last_comma else ","
    elif last_dot >= 0 or last_comma >= 0:
        sep = "." if last_dot >= 0 else ","
        digits_after = len(raw) - raw.rfind(sep) - 1
        # One separator followed by exactly 3 digits is a thousands separator
        # ("1.299" / "1,299"); otherwise it's a decimal ("12,99" / "12.5").
        dec = None if (digits_after == 3 or raw.count(sep) > 1) else sep
    else:
        dec = None

    if dec is None:
        cleaned = raw.replace(".", "").replace(",", "")
    else:
        thousands = "," if dec == "." else "."
        cleaned = raw.replace(thousands, "").replace(dec, ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise PriceParseError(f"bad number {raw!r} in {text!r}") from exc


def parse_price(text: str | None, default_currency: str | None = None):
    """Return (amount, currency). Raises PriceParseError if no price is present."""
    if not text:
        raise PriceParseError("empty price")
    text = unicodedata.normalize("NFKC", text)  # folds odd spaces/digits to plain ones
    return parse_amount(text), detect_currency(text, default_currency)


def normalize_title(title: str) -> str:
    """Lower-case, strip accents and extra whitespace. Used for dedup keys."""
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


# --- very small language detector (html lang attribute first, stopwords second) ---
_STOPWORDS = {
    "en": {"the", "and", "with", "for", "in", "stock"},
    "de": {"und", "mit", "der", "die", "das", "auf", "lager"},
    "fr": {"et", "avec", "le", "la", "les", "en", "stock", "pour"},
    "es": {"y", "con", "el", "la", "los", "para", "disponible"},
    "hi": {"और", "के", "में", "स्टॉक"},
}


def detect_language(html_lang: str | None, text: str) -> str:
    if html_lang:
        return html_lang.split("-")[0].lower()
    words = set(re.findall(r"\w+", text.lower()))
    scores = {lang: len(words & sw) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "unknown"
