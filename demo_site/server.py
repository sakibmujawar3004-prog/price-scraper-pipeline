"""Generates and serves five small fake shops for offline testing.

The shops are deliberately messy, the way real sites are:
  * five languages, five price formats (GBP, EUR German/French style, INR lakh, USD)
  * one shop with no <html lang>, so language must be detected from the text
  * the same product linked with tracking params (?ref=...) and under a second URL
  * "price on request" items, a missing price element, sold-out items
  * each request takes ~120 ms (like a real server) and some pages fail once with 503
  * robots.txt disallows /private/
"""
from __future__ import annotations

import random
import shutil
import threading
import time
import zlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WWW = Path(__file__).parent / "www"
LATENCY = 0.12
PAGES, PER_PAGE = 5, 8

SHOPS = [
    # slug, lang attr, language-specific words, price formatter
    ("uk-books", "en-GB", {"stock": "In stock", "out": "Out of stock", "por": "Price on request",
                           "next": "Next", "words": "the best book with notes for the reader"},
     lambda v: f"£{v:,.2f}"),
    ("de-technik", "de", {"stock": "Auf Lager", "out": "Ausverkauft", "por": "Preis auf Anfrage",
                          "next": "Weiter", "words": "das Gerät mit der Garantie und die Lieferung"},
     lambda v: f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")),
    ("fr-maison", "fr", {"stock": "En stock", "out": "Épuisé", "por": "Prix sur demande",
                         "next": "Suivant", "words": "le produit avec la livraison pour les clients"},
     lambda v: f"{v:,.2f}".replace(",", "\u202f").replace(".", ",") + " €"),
    ("in-electronics", "hi", {"stock": "स्टॉक में", "out": "स्टॉक में नहीं", "por": "कीमत के लिए संपर्क करें",
                              "next": "अगला", "words": "उत्पाद और वारंटी के साथ"},
     lambda v: "₹" + _lakh(int(v))),
    # no lang attribute on purpose -> detector falls back to stopwords
    ("es-tienda", None, {"stock": "Disponible", "out": "Agotado", "por": "Precio a consultar",
                         "next": "Siguiente", "words": "el producto con la garantía para los clientes y envío"},
     lambda v: f"US$ {v:,.2f}"),
]

NOUNS = ["Lamp", "Kettle", "Headphones", "Notebook", "Backpack", "Monitor", "Chair", "Router",
         "Blender", "Camera", "Speaker", "Keyboard", "Watch", "Jacket", "Novel", "Drill"]
ADJ = ["Classic", "Pro", "Compact", "Deluxe", "Eco", "Smart", "Vintage", "Ultra"]


def _lakh(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + groups) + "," + tail


def _page(lang, title, body):
    lang_attr = f' lang="{lang}"' if lang else ""
    return (f"<!doctype html><html{lang_attr}><head><meta charset='utf-8'><title>{title}</title>"
            f"</head><body>{body}</body></html>")


def build():
    if WWW.exists():
        shutil.rmtree(WWW)
    rng = random.Random(42)
    (WWW / "private").mkdir(parents=True)
    (WWW / "robots.txt").write_text("User-agent: *\nDisallow: /private/\n")
    (WWW / "private" / "admin.html").write_text("secret")

    for slug, lang, t, fmt in SHOPS:
        base = WWW / slug
        (base / "p").mkdir(parents=True)
        products = []
        for i in range(PAGES * PER_PAGE):
            sku = f"{slug[:2].upper()}-{1000 + i}"
            name = f"{rng.choice(ADJ)} {rng.choice(NOUNS)} {i + 1}"
            value = round(rng.uniform(5, 2500) * (80 if slug == "in-electronics" else 1), 2)
            if i == 7:
                price_html = f"<p class='price'>{t['por']}</p>"          # unparseable on purpose
            elif i == 19:
                price_html = ""                                           # missing on purpose
            else:
                price_html = f"<p class='price'>{fmt(value)}</p>"
            stock = t["out"] if i % 11 == 0 else t["stock"]
            html = _page(lang, name,
                         f"<h1 class='title'>{name}</h1>{price_html}"
                         f"<p class='stock'>{stock}</p><span class='sku'>SKU: {sku}</span>"
                         f"<p class='desc'>{t['words']}</p>")
            (base / "p" / f"{sku}.html").write_text(html, encoding="utf-8")
            products.append(sku)
        # The same product published under a second URL (a common CMS quirk).
        shutil.copy(base / "p" / f"{products[3]}.html", base / "p" / f"{products[3]}-copy.html")

        for page in range(1, PAGES + 1):
            chunk = products[(page - 1) * PER_PAGE: page * PER_PAGE]
            links = "".join(f"<li><a class='product-link' href='p/{s}.html'>{s}</a></li>" for s in chunk)
            if page == 1:
                # "Featured" box repeats products with tracking params -> URL canonicalisation
                links += "".join(f"<li><a class='product-link' href='p/{s}.html?ref=featured&utm_source=home'>"
                                 f"{s}</a></li>" for s in products[10:14])
                links += f"<li><a class='product-link' href='p/{products[3]}-copy.html'>dup</a></li>"
                links += "<li><a class='product-link' href='/private/admin.html'>admin</a></li>"
            nxt = (f"<a class='next' href='page-{page + 1}.html'>{t['next']}</a>"
                   if page < PAGES else "")
            (base / f"page-{page}.html").write_text(
                _page(lang, f"{slug} {page}", f"<ul>{links}</ul>{nxt}"), encoding="utf-8")


class SlowFlakyHandler(SimpleHTTPRequestHandler):
    failed_once: set[str] = set()
    lock = threading.Lock()

    def do_GET(self):
        time.sleep(LATENCY)
        path = self.path.split("?")[0]
        # Every 9th product page fails once with 503 -> exercises retry/backoff.
        if "/p/" in path and zlib.crc32(path.encode()) % 9 == 0:
            with self.lock:
                first = path not in self.failed_once
                self.failed_once.add(path)
            if first:
                self.send_error(503, "Temporarily unavailable")
                return
        super().do_GET()

    def log_message(self, *args):
        pass


def start_server(port: int = 8765):
    build()
    handler = partial(SlowFlakyHandler, directory=str(WWW))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    srv, url = start_server()
    print(f"Demo shops at {url}/uk-books/page-1.html (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()
