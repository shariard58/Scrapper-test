import json
import os
import re
import time
from collections import Counter

from dotenv import load_dotenv
from zenrows import ZenRowsClient

load_dotenv()
client = ZenRowsClient(os.environ["ZENROWS_API_KEY"])


# Seconds to wait for one ZenRows request; JS rendering can take a while, but a site should never hang a run
TIMEOUT = 90

# Try the cheap/fast settings first; if the site blocks us (422), move to the next one
ATTEMPTS = [
    {},
    {},
    {"js_render": "true", "premium_proxy": "true"},
]


def get_response(url):
    """Return (html, status). Retries only when blocked (422); returns a 404 immediately."""
    for params in ATTEMPTS:
        response = client.get(url, params=params, timeout=TIMEOUT)
        if response.status_code != 422:
            return response.text, response.status_code
        # ZenRows puts the error reason in the JSON "title"; never print the URL since it contains the API key
        try:
            reason = response.json().get("title")
        except ValueError:
            reason = response.text[:200]
        print(f"Retry ({response.status_code}) {url}: {reason}")
    return response.text, response.status_code


def fetch_html(url):
    html, status = get_response(url)
    if status != 200:
        raise RuntimeError(f"status {status}")
    return html


def scrape_refermate(url):
    html = fetch_html(url)
    # Codes live as "couponCode" inside the hidden Next.js payload (self.__next_f.push)
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    payload = "".join(json.loads(c) for c in chunks)
    codes = re.findall(r'"couponCode":"([^"]+)"', payload)
    return list(dict.fromkeys(c for c in codes if c != "No Code Needed"))


def scrape_worthepenny(url):
    html = fetch_html(url)
    # Each coupon card has data-code="SASCHA20"; deals have an empty data-code
    codes = re.findall(r'data-code="([^"]+)"', html)
    return list(dict.fromkeys(codes))


def scrape_hotdeals(url):
    """Return all codes (active first, then expired) for the store on a hotdeals page, from one request."""
    html = fetch_html(url)

    # Active codes: <article class="offer-card" data-merchant="cozyearth.com" ... data-code="GETCOZY50">.
    # The page also shows cards from other stores, so keep only the merchant with the most cards.
    cards = re.findall(r'<(?:article|section) class="offer-card[^"]*"[^>]*>', html)
    offers = []
    for card in cards:
        merchant = re.search(r'data-merchant="([^"]+)"', card)
        code = re.search(r'data-code="([^"]+)"', card)
        if merchant and code:
            offers.append((merchant.group(1), code.group(1)))
    active = []
    if offers:
        store = Counter(merchant for merchant, _ in offers).most_common(1)[0][0]
        active = list(dict.fromkeys(code for merchant, code in offers if merchant == store))

    # Expired codes: <section class="expire-coupons"> with each code in <div class="expire-tran-btn-sign">
    expired = []
    section = re.search(r'<section class="expire-coupons".*?</section>', html, re.S)
    if section:
        codes = re.findall(r'<div class="expire-tran-btn-sign"[^>]*>([^<]+)</div>', section.group(0))
        expired = list(dict.fromkeys(code.strip() for code in codes if code.strip()))

    return list(dict.fromkeys(active + expired))


def fetch_coupons_from_worthepenny(urls):
    """Return {url: [code, ...]} for each URL. A failed URL gets an empty list."""
    results = {}
    for url in urls:
        try:
            results[url] = scrape_worthepenny(url)
        except Exception as e:
            print(f"Failed: {url} ({e})")
            results[url] = []
    return results


if __name__ == "__main__":
    start = time.time()
    codes = scrape_hotdeals("https://www.hotdeals.com/coupons/rowe-casa-organics-promo-codes")
    print(f"Codes ({len(codes)}): {codes}")
    print(f"Response time: {time.time() - start:.2f} seconds")
