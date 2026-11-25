from decimal import Decimal

import pytest

from scraper.crawler import canonical_url
from scraper.normalize import PriceParseError, detect_language, parse_price


@pytest.mark.parametrize("text, amount, currency", [
    ("$1,299.50", "1299.50", "USD"),
    ("US$ 129,999.00", "129999.00", "USD"),
    ("1.299,00 €", "1299.00", "EUR"),
    ("1 299,50 €", "1299.50", "EUR"),
    ("1 299,50 €", "1299.50", "EUR"),
    ("₹1,29,999", "129999", "INR"),
    ("Rs. 2,499", "2499", "INR"),
    ("CHF 1'299.90", "1299.90", "CHF"),
    ("£51.77", "51.77", "GBP"),
    ("12,99 €", "12.99", "EUR"),
    ("€12", "12", "EUR"),
])
def test_parse_price(text, amount, currency):
    assert parse_price(text) == (Decimal(amount), currency)


@pytest.mark.parametrize("text", ["Price on request", "Preis auf Anfrage", "", None])
def test_unparseable(text):
    with pytest.raises(PriceParseError):
        parse_price(text)


def test_default_currency():
    assert parse_price("1.299,00", "EUR") == (Decimal("1299.00"), "EUR")


def test_canonical_url_strips_tracking():
    a = canonical_url("p/1.html?ref=featured&utm_source=home", base="http://X.com/shop/page-1.html")
    b = canonical_url("http://x.com/shop/p/1.html#reviews")
    assert a == b == "http://x.com/shop/p/1.html"


def test_language_detection():
    assert detect_language("de-DE", "") == "de"
    assert detect_language(None, "el producto con la garantía para los clientes") == "es"
