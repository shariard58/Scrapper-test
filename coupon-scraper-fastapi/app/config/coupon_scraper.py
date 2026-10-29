import asyncio
import base64
import json
import random
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import parse_qs, urlparse

import cloudscraper
import requests  # type: ignore
from bs4 import BeautifulSoup
from zenrows import ZenRowsClient

from app.config import settings
from app.config.headers import headers
from app.lib.logger import get_logger

# set log level
logger = get_logger(__name__)

# import helpers
from app.lib.helpers import (extract_coupons_from_html, fetch_page,
                             fetchRequest, fetchURL, getZenResponseAlter,
                             getZenrowsResponse, getZenrowsWithCookiesResponse,
                             postZenrowsResponse, scrape_with_requests,
                             zenrowsClient)


# ---------------------------------------------------------------------------
# Per-site code scrapers: each scrape_<site>(url) returns the codes found on one store page.
# They have their own ZenRows client and retry logic; fetch_coupons_from_<site>(links) runs them via _collect.
# ---------------------------------------------------------------------------
client = ZenRowsClient(settings.ZENROWS_API_KEY)

# Seconds to wait for one ZenRows request; JS rendering can take a while, but a site should never hang a run
TIMEOUT = 90

# Try the cheap/fast settings first; if the site blocks us (422), move to the next one
ATTEMPTS = [
    {},
    {},
    {"js_render": "true", "premium_proxy": "true"},
]


def get_response(url):
    """Return (html, status). Retries when blocked (422) or when the request itself fails
    (timeout, network error); any other status, like a 404, returns immediately."""
    for attempt, params in enumerate(ATTEMPTS, 1):
        try:
            response = client.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            # The exception text contains the ZenRows URL with the API key, so only its type is shown
            if attempt == len(ATTEMPTS):
                raise RuntimeError(f"request failed: {type(e).__name__}") from None
            logger.warning(f"Retry ({type(e).__name__}) {url}")
            continue
        if response.status_code != 422:
            return response.text, response.status_code
        # ZenRows puts the error reason in the JSON "title"; never print the URL since it contains the API key
        try:
            reason = response.json().get("title")
        except ValueError:
            reason = response.text[:200]
        logger.warning(f"Retry ({response.status_code}) {url}: {reason}")
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
    """Return the store's codes from a worthepenny {store}.worthepenny.com/coupon/ page, from one request."""
    html = fetch_html(url)
    # Code cards look like data-code="CE-CADE" ... data-coupon_type="code" data-name="Cozy Earth"; deals have
    # an empty data-code. Keep the most common store name in case cards of other stores show up.
    offers = re.findall(r'data-code="([^"]+)"[^>]*data-name="([^"]+)"', html)
    if not offers:
        return []
    store = Counter(name for _, name in offers).most_common(1)[0][0]
    return list(dict.fromkeys(unescape(code).strip() for code, name in offers if name == store))


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


def scrape_coupert(url):
    """Return the store's codes from a coupert /shop/{domain} page, from one request."""
    html = fetch_html(url)
    # The store's own codes are in the Next.js data at props.pageProps.storeInfo.coupons;
    # codes elsewhere on the page belong to other featured stores. Coupert lists no expired codes.
    data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not data:
        return []
    store = json.loads(data.group(1)).get("props", {}).get("pageProps", {}).get("storeInfo") or {}
    codes = [(coupon.get("code") or "").strip() for coupon in store.get("coupons") or []]
    return list(dict.fromkeys(code for code in codes if code))


def scrape_joinhoney(url):
    """Return the store's codes from a joinhoney /shop/{store} page, from one request."""
    html = fetch_html(url)
    # Store data is embedded as JSON: "publicCoupons":[{"code":"UNLOCKCOZY","type":"COUPON",...}].
    # Other "code" keys on the page belong to other stores, so only this list is read.
    start = html.find('"publicCoupons":')
    if start == -1:
        return []
    coupons, _ = json.JSONDecoder().raw_decode(html, start + len('"publicCoupons":'))
    codes = [(coupon.get("code") or "").strip() for coupon in coupons if not coupon.get("hidden")]
    return list(dict.fromkeys(code for code in codes if code))


def dontpayfull_reveal(coupon_id, mask):
    """Return the full code from dontpayfull's "Show code" popup, or None."""
    html, status = get_response(f"https://www.dontpayfull.com/coupons/getcoupon?id={coupon_id}")
    code = re.search(r'class="code-box[^"]*"[^>]*>\s*<h2>([^<]+)</h2>', html) if status == 200 else None
    if not code:
        return None
    code = code.group(1).strip()
    # The card shows the last characters ("***OME10"); a code that doesn't match belongs to another offer
    return code if code.upper().endswith(mask.lstrip("*").upper()) else None


def scrape_dontpayfull(url):
    """Return all codes (active first, then expired) from a dontpayfull /at/{domain} page."""
    soup = BeautifulSoup(fetch_html(url), "html.parser")

    # Active codes are masked ("***OME10") until clicked; the click opens /coupons/getcoupon?id=<card id>
    # with the full code, so each active code costs one extra request.
    active_cards = []
    section = soup.select_one("#active-coupons")
    for card in section.select("article.code") if section else []:
        mask = card.select_one(".ocode")
        if card.get("data-id") and mask:
            active_cards.append((card["data-id"], mask.get_text(strip=True)))
    with ThreadPoolExecutor(max_workers=5) as pool:
        active = [code for code in pool.map(lambda c: dontpayfull_reveal(*c), active_cards) if code]

    # Expired codes are shown in full
    expired = []
    section = soup.select_one("#expired-coupons")
    for element in section.select("[data-clipboard-text]") if section else []:
        expired.append(element["data-clipboard-text"].strip())

    return list(dict.fromkeys(code for code in active + expired if code))


def scrape_promocodes(url):
    """Return the store's codes from a promocodes /{store}-coupons page, from one request."""
    html = fetch_html(url)
    # Offers are embedded JSON: "couponCode":"GETCOZY"; deals have "couponCode":null
    codes = re.findall(r'"couponCode":"([^"]+)"', html)
    return list(dict.fromkeys(code.strip() for code in codes if code.strip()))


def couponchief_reveal(coupon_id, last_char):
    """Return the full code from couponchief's coupon overlay, or None."""
    html, status = get_response(f"https://www.couponchief.com/pages/coupon_overlay/{coupon_id}")
    code = re.search(r'<input[^>]*id="coupon-code"[^>]*value="([^"]+)"', html) if status == 200 else None
    if not code:
        return None
    code = unescape(code.group(1)).strip()
    # The card shows only the code's last character; a mismatch means the overlay is for another offer
    return code if code.upper().endswith(last_char.upper()) else None


def scrape_couponchief(url):
    """Return all codes (active first, then expired) from a couponchief /{store} page."""
    soup = BeautifulSoup(fetch_html(url), "html.parser")

    # Store coupons are <div data-coupon-id="7133304"> cards; code cards show only the last character
    # in <span class="covered-coupon-code">. Clicking reloads the page with #coupon=<id>, which opens
    # /pages/coupon_overlay/<id> holding the full code, so each code costs one extra request.
    # Sidebar cards for other stores have no data-coupon-id.
    active, expired = [], []
    for card in soup.select("[data-coupon-id]"):
        covered = card.select_one(".covered-coupon-code")
        if covered:
            target = expired if "Expired" in card.get_text() else active
            target.append((card["data-coupon-id"], covered.get_text(strip=True)))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda c: couponchief_reveal(*c), active + expired))

    return list(dict.fromkeys(code for code in codes if code))


def scrape_simplycodes(url):
    """Return all codes (working first, then failing) from a simplycodes /store/{domain} page, from one request."""
    html = fetch_html(url)
    # Code buttons look like data-code="welcome10" ... data-merchant-url="https://cozyearth.com/".
    # Related stores use data-merchant-url="/store/other.com", so keep the most common absolute merchant URL.
    # Cards carry data-health="green" (working now) or "red" (failing recently).
    offers = []
    for tag in re.findall(r'<[^>]*data-code="[^"]+"[^>]*>', html):
        code = re.search(r'data-code="([^"]+)"', tag).group(1).strip()
        merchant = re.search(r'data-merchant-url="(https?://[^"]+)"', tag)
        health = re.search(r'data-health="([^"]+)"', tag)
        if merchant:
            offers.append((merchant.group(1), unescape(code), health.group(1) if health else None))
    if not offers:
        return []
    store = Counter(merchant for merchant, _, _ in offers).most_common(1)[0][0]
    offers = [(code, health) for merchant, code, health in offers if merchant == store]
    # A code appears on several buttons; only the card button carries its health
    failing = {code for code, health in offers if health == "red"}
    codes = list(dict.fromkeys(code for code, _ in offers))
    return [c for c in codes if c not in failing] + [c for c in codes if c in failing]


def scrape_tenereteam(url):
    """Return the store's codes from a tenereteam {store}.tenereteam.com/coupons page, from one request."""
    html = fetch_html(url)
    # Offers are JSON inside the Next.js payload (self.__next_f.push): {"coupon_code":"WELCOME10",...,
    # "store":{"alias":"cozy-earth",...}}. Some offers reference the store indirectly and carry no alias.
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    payload = "".join(json.loads(chunk) for chunk in chunks)
    matches = list(re.finditer(r'"coupon_code":"([^"]+)"', payload))
    offers = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(payload)
        alias = re.search(r'"alias":"([^"]+)"', payload[match.end():end])
        offers.append((match.group(1).strip(), alias.group(1) if alias else None))
    aliases = [alias for _, alias in offers if alias]
    if not aliases:
        return []
    store = Counter(aliases).most_common(1)[0][0]
    # The same code is sometimes listed in different case ("lovett" and "LOVETT"); keep the first
    codes = {}
    for code, alias in offers:
        if code and alias in (store, None):
            codes.setdefault(code.upper(), code)
    return list(codes.values())


def fetch_plain_retry(url, tries=8, pause=3):
    """For sites that block most requests at random: plain requests only, a few seconds apart.
    JS rendering and premium proxies did not get through any better on these sites."""
    for attempt in range(1, tries + 1):
        try:
            response = client.get(url, timeout=TIMEOUT)
            # A cut-off page with only <head> counts as a failed try
            if response.status_code == 200 and "<body" in response.text.lower():
                return response.text
            if response.status_code not in (422, 429, 200) and response.status_code < 500:
                raise RuntimeError(f"status {response.status_code}")
        except requests.RequestException as e:
            logger.warning(f"Retry ({type(e).__name__}) {url}")
        if attempt < tries:
            time.sleep(pause)
    raise RuntimeError(f"blocked {tries} times")


def scrape_valuecom(url):
    """Return the store's codes from a valuecom {store}.valuecom.com page."""
    # valuecom blocks most requests (422) at random, so it gets plain retries instead of get_response
    html = fetch_plain_retry(url)
    # The store's offers are JSON-LD: {"@type":"Offer",...,"couponCode":"SA50-tuoiVn",...}.
    # The "Similar Store Coupons" section shows other stores' codes in the HTML, but not in the JSON-LD.
    codes = re.findall(r'"couponCode":"([^"]+)"', html)
    return list(dict.fromkeys(code.strip() for code in codes if code.strip()))


def scrape_discountreactor(url):
    """Return the store's codes from a discountreactor /coupons/{store} page, with one extra request."""
    # Codes are masked on the page: <a data-shop-alias="cozyearth.com"> <div data-offer-code="2669028"
    # data-code="••••••OZY">. The page's own script reveals them all at once with
    # POST /offer/codes {"ids": [...]}, which needs the page's CSRF token and cookies from the same session.
    session = {"session_id": str(random.randint(1, 99999))}
    for attempt in range(len(ATTEMPTS)):
        page = client.get(url, params=session, timeout=TIMEOUT)
        if page.status_code != 422:
            break
        logger.warning(f"Retry ({page.status_code}) {url}")
    if page.status_code != 200:
        raise RuntimeError(f"status {page.status_code}")
    html = page.text

    # Cards for related stores are mixed in, so keep the most common shop alias
    cards = re.findall(
        r'data-shop-alias="([^"]+)"[^>]*>\s*<div class="offer-list-item-button_hidden-code" '
        r'data-offer-code="(\d+)" data-code="([^"]*)"', html,
    )
    if not cards:
        return []
    store = Counter(alias for alias, _, _ in cards).most_common(1)[0][0]
    masks = {int(offer_id): mask for alias, offer_id, mask in cards if alias == store}

    token = re.search(r'name="csrf-token" content="([^"]+)"', html)
    headers = {
        "X-CSRF-TOKEN": token.group(1) if token else "",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
        "Cookie": page.headers.get("Zr-Cookies", ""),
    }
    codes = []
    ids = list(masks)
    for start in range(0, len(ids), 50):  # the site's script sends at most 50 ids per request
        response = client.post(
            "https://www.discountreactor.com/offer/codes",
            params={**session, "custom_headers": "true"},
            headers=headers,
            data=json.dumps({"ids": ids[start:start + 50]}),
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            logger.warning(f"Could not reveal codes ({response.status_code}) {url}")
            continue
        for item in response.json().get("data", {}).get("codes", []):
            code = (item.get("code") or "").strip()
            # The card shows the code's last characters ("••••••OZY"); skip anything that doesn't match
            if code and code.upper().endswith(masks.get(item.get("id"), "").lstrip("•").upper()):
                codes.append(code)
    return list(dict.fromkeys(codes))


def scrape_dealdrop(url):
    """Return the store's codes from a dealdrop /{store} page, from one request."""
    html = fetch_html(url)
    # The page shows only the last 2 characters of each code, but the SvelteKit data script holds them in full,
    # either as b.coupon_code="OKSANA45" or as {coupon_code:"SUSAN40",...}
    data = next((s for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if "__sveltekit" in s), "")
    codes = re.findall(r'\.coupon_code="([^"]+)"|\bcoupon_code:"([^"]+)"', data)
    return list(dict.fromkeys(code.strip() for pair in codes for code in pair if code.strip()))


def scrape_greenpromocode(url):
    """Return the store's codes from a greenpromocode /coupons/{store}/ page, with one extra request."""
    page = url.split("?")[0]
    html = fetch_html(page)
    # Code offers show only the last characters: <div class="go_link" view-href="/coupons/cozy-earth/?view=11609534">.
    # Clicking reloads the page with ?view=<id>, and that version shows every code on the page in full
    # as data-clipboard-text, so one extra request reveals them all.
    slug = page.rstrip("/").split("/")[-1]
    view = re.search(rf'view-href="/coupons/{re.escape(slug)}/\?view=(\d+)"', html)
    if not view:
        return []
    soup = BeautifulSoup(fetch_html(f"{page}?view={view.group(1)}"), "html.parser")
    codes = [unescape(el["data-clipboard-text"]).strip() for el in soup.select(".coupon_item [data-clipboard-text]")]
    return list(dict.fromkeys(code for code in codes if code))


def scrape_couponbind(url):
    """Return all codes (active first, then expired) from a couponbind /coupons/{domain} page, from one request."""
    html = fetch_html(url)
    # Each code card (<div class="... codeblock ... code" data-cid="...">) holds the full code in
    # <span class="hiddenCode">; expired codes sit lower on the page under "Expired ... Promo Codes".
    soup = BeautifulSoup(html, "html.parser")
    codes = [span.get_text(strip=True) for span in soup.select(".codeblock span.hiddenCode")]
    return list(dict.fromkeys(code for code in codes if code))


def scrape_deala(url):
    """Return all codes (active first, then inactive) from a deala /{store} page, from one request."""
    html = fetch_html(url)
    # The page hides codes behind "Get code", but Angular's transfer state
    # (<script id="serverApp-state">, with &q; for quotes) holds them in full:
    # "store-view-page-coupons-highlight" (ACTIVE) and "store-view-page-coupons-other" (INACTIVE).
    state = re.search(r'<script[^>]*id="serverApp-state"[^>]*>(.*?)</script>', html, re.S)
    if not state:
        return []
    text = state.group(1)
    for escaped, char in (("&q;", '"'), ("&s;", "'"), ("&l;", "<"), ("&g;", ">"), ("&a;", "&")):
        text = text.replace(escaped, char)
    data = json.loads(text)
    store = (data.get("store-view-page-store", {}).get("result") or {}).get("alias")
    codes = []
    for key in ("store-view-page-coupons-highlight", "store-view-page-coupons-other"):
        result = data.get(key, {}).get("result") or {}
        if (result.get("totalPages") or 1) > 1:
            logger.warning(f"Only the first page of {key} is read: {url}")
        for coupon in result.get("data") or []:
            if (coupon.get("shop") or {}).get("alias") == store and coupon.get("code"):
                codes.append(coupon["code"].strip())
    return list(dict.fromkeys(code for code in codes if code))


def codes_by_store_url(html):
    """Codes from tags like data-code="PJ-BOGO" data-url="https://cozyearth.com" (promosreview, getscoupon).
    "Featured Coupons" of other stores point elsewhere, so keep the most common data-url."""
    offers = re.findall(r'data-code="([^"]+)"\s+data-url="([^"]*)"', html)
    if not offers:
        return []
    store = Counter(link for _, link in offers).most_common(1)[0][0]
    return list(dict.fromkeys(unescape(code).strip() for code, link in offers if link == store))


def scrape_promosreview(url):
    """Return the store's codes (active and expired) from a promosreview /store/{Name}-coupon-codes page."""
    return codes_by_store_url(fetch_html(url))


def scrape_getscoupon(url):
    """Return the store's codes (active and expired) from a getscoupon /store/{domain} page."""
    return codes_by_store_url(fetch_html(url))


def scrape_dealam(url):
    """Return the store's codes from a dealam /coupons/{store}/ page, from one request."""
    html = fetch_html(url)
    # Code cards are <div class="dx_code_coupons ..."> holding data-clipboard-text="COZY300" and the store name in
    # <ul class="time_success"><li>Cozy Earth</li>. Cards with "new-class-ads" are ads for other offers.
    starts = [m.start() for m in re.finditer(r'class="dx_code_coupons', html)] + [len(html)]
    offers = []
    for start, end in zip(starts, starts[1:]):
        card = html[start:end]
        if card.startswith('class="dx_code_coupons new-class-ads'):
            continue
        code = re.search(r'data-clipboard-text="([^"]+)"', card)
        store = re.search(r'<ul class="time_success[^"]*">\s*<li>([^<]+)</li>', card)
        if code:
            offers.append((unescape(code.group(1)).strip(), store.group(1).strip() if store else None))
    names = [name for _, name in offers if name]
    store = Counter(names).most_common(1)[0][0] if names else None
    # Some cards only ever show a masked code ("••••••FE", even in their popup) or the placeholder "Applied"
    return list(dict.fromkeys(
        code for code, name in offers
        if code and name in (store, None) and "•" not in code and code.lower() != "applied"
    ))


def scrape_coupons(url):
    """Return the store's codes from a coupons.com /coupon-codes/{store} page, from one request."""
    html = fetch_html(url)
    # Vouchers are JSON inside the Next.js payload (self.__next_f.push): ..."code":"WELCOME10","description":...
    # followed by "retailer":{..."name":"Cozy Earth"...}. Matching the "description" key skips icon names
    # stored under "code"; the last voucher's retailer is sometimes left out, so a missing name is kept.
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    payload = "".join(json.loads(chunk) for chunk in chunks)
    matches = list(re.finditer(r'"code":"([^"]+)","description', payload))
    offers = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(payload)
        retailer = re.search(r'"retailer":\{[^{}]*?"name":"([^"]+)"', payload[match.end():end])
        offers.append((match.group(1).strip(), retailer.group(1) if retailer else None))
    names = [name for _, name in offers if name]
    store = Counter(names).most_common(1)[0][0] if names else None
    return list(dict.fromkeys(code for code, name in offers if code and name in (store, None)))


def goodshop_codes(html):
    """Unmasked codes from goodshop deal cards: <article data-deal-id data-merchant-id> with
    <div class="deal-button has-code" data-clipboard-text="CE-DISCOVERELLE">. Masked ones look like "••••••LOW"."""
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for card in soup.select("article[data-deal-id]"):
        button = card.select_one(".deal-button[data-clipboard-text]")
        if button:
            offers.append((unescape(button["data-clipboard-text"]).strip(), card.get("data-merchant-id")))
    merchants = [merchant for _, merchant in offers if merchant]
    store = Counter(merchants).most_common(1)[0][0] if merchants else None
    return [code for code, merchant in offers if code and "•" not in code and merchant in (store, None)]


def scrape_goodshop(url):
    """Return the store's unmasked codes from a goodshop /coupons/{domain} page, with one extra request."""
    page = url.split("?")[0]
    # The normal page masks most new codes ("••••••LOW"), and no click reveals them without leaving for the store.
    # Any ?open=<id> URL returns an older version of the page with every code in full (the same codes Goodsearch
    # shows), so both versions are read.
    codes = goodshop_codes(fetch_html(page)) + goodshop_codes(fetch_html(f"{page}?open=1"))
    return list(dict.fromkeys(codes))


def scrape_goodsearch(url):
    """Return the store's codes from a goodsearch /coupons/{domain} page, from one request."""
    html = fetch_html(url)
    # Same company as Goodshop. Offers are JSON-LD, each tied to the store page:
    # {"@id":"https://www.goodsearch.com/coupons/cozyearth.com#72866609",...,"discountCode":"CE-DISCOVERELLE"}
    page = re.escape(url.split("?")[0].rstrip("/"))
    codes = re.findall(rf'{page}#\d+"[^{{}}]*"discountCode":"([^"]+)"', html)
    return list(dict.fromkeys(code.strip() for code in codes if code.strip() and "•" not in code))


def couponcause_reveal(page, offer_id):
    """Return the full code from couponcause's coupon modal for one offer, or None."""
    html, status = get_response(f"{page}?_c={offer_id}")
    code = re.search(r'id="offerCode_js"[^>]*>([^<]+)<', html) if status == 200 else None
    return unescape(code.group(1)).strip() if code else None


def scrape_couponcause(url):
    """Return the store's codes from a couponcause /stores/{store}/ page."""
    page = url.split("?")[0]
    html = fetch_html(page)
    # Codes are hidden on the page. Code offers carry data-redirect-url="/stores/cozy-earth/?_c=20099"
    # (deals have no ?_c=); that URL opens the page with a modal holding the code in <h3 id="offerCode_js">,
    # so each code costs one extra request. The /go2/ links go to the store and are never requested.
    path = re.escape(page.split("couponcause.com")[-1])
    ids = list(dict.fromkeys(re.findall(rf'data-redirect-url="{path}\?_c=(\d+)"', html)))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda offer_id: couponcause_reveal(page, offer_id), ids))
    return list(dict.fromkeys(code for code in codes if code))


def dazzdeals_reveal(cp, tail):
    """Return the full code from dazzdeals' /offer/<cp>/ page, or None."""
    html, status = get_response(f"https://www.dazzdeals.com/offer/{cp}/")
    code = re.search(r'class="copy_code[^"]*"[^>]*>([^<]+)<', html) if status == 200 else None
    if not code:
        return None
    code = unescape(code.group(1)).strip()
    # The card shows the code's last characters ("PNIK"); a mismatch means the page is for another offer
    return code if code.upper().endswith(tail.upper()) else None


def scrape_dazzdeals(url):
    """Return the store's codes from a dazzdeals /store/{store}/ page."""
    html = fetch_html(url)
    # Code cards hold only the code's tail: <a class="couponBtn hidden showCouponCode showcode"
    # href="/redirect/..." data-cp="42926a...">PNIK</a>. Clicking opens the site's own /offer/<data-cp>/
    # page with the full code in class="copy_code"; /redirect/ goes to the store and is never requested.
    offers = dict(re.findall(r'class="couponBtn hidden showCouponCode showcode"[^>]*data-cp="([^"]+)"[^>]*>([^<]+)<', html))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda item: dazzdeals_reveal(*item), offers.items()))
    return list(dict.fromkeys(code for code in codes if code))


def joincheckmate_reveal(page, code_id):
    """Return the full code from joincheckmate's code modal page, or None."""
    html, status = get_response(f"{page}/modal?code={code_id}")
    if status != 200:
        return None
    # The modal's React Router data has ...\"id\",\"<code id>\",\"value\",\"INSTYLEEXCLUSIVE\"...
    code = re.search(rf'\\?"id\\?",\\?"{re.escape(code_id)}\\?",\\?"value\\?",\\?"([^"\\]+)', html)
    return code.group(1).strip() if code else None


def scrape_joincheckmate(url):
    """Return the store's codes from a joincheckmate /merchants/{domain} page."""
    page = url.split("?")[0].rstrip("/")
    html = fetch_html(page)
    # Codes are masked on the page ("INS#############") and every card links to /mr/..., which goes to the store.
    # The page data lists each code's modal, {page}/modal?code=<id>, and that page holds the full code,
    # so each code costs one extra request.
    ids = list(dict.fromkeys(re.findall(rf'{re.escape(page)}/modal\?code=([0-9a-z]+)', html)))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda code_id: joincheckmate_reveal(page, code_id), ids))
    return list(dict.fromkeys(code for code in codes if code))


def couponsgo_reveal(url, coupon_id, tail):
    """Return the full code from couponsgo's coupon dialog (?cid=<id>), or None."""
    separator = "&" if "?" in url else "?"
    html, status = get_response(f"{url}{separator}cid={coupon_id}")
    code = re.search(r'class="code-str[^"]*"[^>]*>([^<]+)<', html) if status == 200 else None
    if not code:
        return None
    code = unescape(code.group(1)).strip()
    # The card shows the code's last characters; a mismatch means the dialog is for another offer
    return code if code.upper().endswith(tail.upper()) else None


def scrape_couponsgo(url):
    """Return the store's codes from a couponsgo.org store or search page."""
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    # Offers are <div class="coupon-item" data-domain="nobullproject.com" data-value="835949f5b27f815e">; code offers
    # show only the code's tail in <a class="coupon_button"><p>UDEN</p>, deals say "Get Deal". Clicking opens the
    # same page with ?cid=<data-value>, whose dialog has the full code in class="code-str". /go/ links go to the store.
    offers = []
    for item in soup.select(".coupon-item[data-domain][data-value]"):
        button = item.select_one("a.coupon_button p")
        tail = button.get_text(strip=True) if button else ""
        if tail and tail != "Get Deal":
            offers.append((item["data-domain"], item["data-value"], tail))
    if not offers:
        return []
    # Search pages also list other stores, so keep the most common domain
    store = Counter(domain for domain, _, _ in offers).most_common(1)[0][0]
    targets = list(dict.fromkeys((coupon_id, tail) for domain, coupon_id, tail in offers if domain == store))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda target: couponsgo_reveal(url, *target), targets))
    return list(dict.fromkeys(code for code in codes if code))


def scrape_couponpromo(url):
    """Return the store's codes from a couponpromo.net search page, from one request."""
    html = fetch_html(url)
    # Same company and codes as couponsgo.org, but full codes are in the page:
    # <span data-domain="nobullproject.com"> ... data-clipboard-text="GOVX20", in that order within each result.
    # Results for other stores are mixed in, so each code belongs to the last data-domain before it.
    offers, domain = [], None
    for match in re.finditer(r'data-domain="([^"]+)"|data-clipboard-text="([^"]+)"', html):
        if match.group(1):
            domain = match.group(1)
        elif domain:
            offers.append((domain, unescape(match.group(2)).strip()))
    if not offers:
        return []
    store = Counter(domain for domain, _ in offers).most_common(1)[0][0]
    return list(dict.fromkeys(code for domain, code in offers if domain == store and code))


def scrape_kotag(url):
    """Return all codes (active first, then expired) from a kotag /us/marques/codes-promo-{store}/ page."""
    html = fetch_html(url)
    # The store's influencer codes are in the Next.js data at
    # props.pageProps.coupons = [{"code": "CHAMPAGNE45", "expired": false, ...}]
    data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not data:
        return []
    coupons = json.loads(data.group(1)).get("props", {}).get("pageProps", {}).get("coupons") or []
    coupons = [coupon for coupon in coupons if isinstance(coupon, dict) and (coupon.get("code") or "").strip()]
    active = [coupon["code"].strip() for coupon in coupons if not coupon.get("expired")]
    expired = [coupon["code"].strip() for coupon in coupons if coupon.get("expired")]
    return list(dict.fromkeys(active + expired))


def retailmenot_reveal(uuid):
    """Return the full code from retailmenot's post-click modal for one offer, or None."""
    body, status = get_response(f"https://www.retailmenot.com/modals/outclick/{uuid}/?template=merchant&trigger=entrance_modal")
    if status != 200:
        return None
    try:
        modal = json.loads(body).get("modalHtml") or ""
    except ValueError:
        return None
    # The modal belongs to one offer and copies its code with navigator.clipboard.writeText('first25')
    if f'data-content-uuid="{uuid}"' not in modal:
        return None
    code = re.search(r"navigator\.clipboard\.writeText\('([^']+)'\)", unescape(modal))
    # Some offers hold a note instead of a code ("Code Provided with Signup"); real codes have no spaces
    return code.group(1).strip() if code and " " not in code.group(1).strip() else None


def scrape_retailmenot(url):
    """Return the store's codes (active first, then expired) from a retailmenot /view/{domain} page."""
    html = unescape(fetch_html(url))
    # Each offer link is <a href="/out/O/<uuid>?...module=...&offer_uuid=<uuid>..." x-data="outclickHandler({...,
    # 'offerType': 'COUPON', 'requestSlug': 'tommyjohn.com', 'offerUuid': '<uuid>'})">; expired offers have
    # module=expired_offer. Clicking reloads the page with ?u=<uuid>, which loads the modal from
    # /modals/outclick/<uuid>/ holding the code, so each code costs one extra request. /out/ links are never requested.
    offers = re.findall(r"'offerType': 'COUPON', 'requestSlug': '([^']+)', 'offerUuid': '([A-Z0-9]+)'", html)
    if not offers:
        return []
    store = Counter(slug for slug, _ in offers).most_common(1)[0][0]
    uuids = list(dict.fromkeys(uuid for slug, uuid in offers if slug == store))
    expired = set(re.findall(r"module=expired_offer[^\"]*offer_uuid=([A-Z0-9]+)", html))
    uuids = [u for u in uuids if u not in expired] + [u for u in uuids if u in expired]
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(retailmenot_reveal, uuids))
    return list(dict.fromkeys(code for code in codes if code))


def offers_reveal(page, uuid):
    """Return the full code from offers.com's code modal (?em=<uuid>), or None."""
    html, status = get_response(f"{page}?em={uuid}")
    # The modal renders as x-data="{ copiedButtonClicked: false, code: 'first25' }"
    code = re.search(r"copiedButtonClicked: false, code: '([^']+)'", unescape(html)) if status == 200 else None
    # Some offers hold a note instead of a code ("Code Provided with Signup"); real codes have no spaces
    return code.group(1).strip() if code and " " not in code.group(1).strip() else None


def scrape_offers(url):
    """Return the store's codes from an offers.com /stores/{store}/ page."""
    page = url.split("?")[0]
    html = unescape(fetch_html(page))
    # Code offers are links with x-data="commerceLink('<uuid>', '')" ... hasCode: true (deals say hasCode: false).
    # Clicking opens the same page with ?em=<uuid>, which renders a modal holding the code, so each code costs one
    # extra request. The /exit/outbound/ links go to the store and are never requested.
    # (offers.com shares offer ids and codes with RetailMeNot.)
    uuids = re.findall(r"commerceLink\('([A-Z0-9]+)'[^>]*hasCode: true", html)
    uuids = list(dict.fromkeys(uuids))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda uuid: offers_reveal(page, uuid), uuids))
    return list(dict.fromkeys(code for code in codes if code))


def scrape_trustdeals(url):
    """Return the codes shown on a trustdeals /view/{domain} page, from one request."""
    html = fetch_html(url)
    # Offers are <article class="coupon ..."> with <span class="code">. The store's own codes stay empty: their
    # modal (?id=offer<id>) is also empty, and the code only shows after /api/jump/ (the store redirect), which is
    # never requested. "coupon common" articles ("most common codewords at similar shops", e.g. SAVE10) are shown
    # in full and count as codes, as agreed; "coupon expired" ones are skipped.
    codes = re.findall(
        r'<article[^>]*class="coupon (?!expired)[^"]*"(?:(?!</article>).)*?<span class="code">([^<]+)</span>', html, re.S
    )
    return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))


def latestdeals_posts(path):
    """Return the post objects from a latestdeals JSON API response, or [] if it fails."""
    body, status = get_response(f"https://www.latestdeals.co.uk/api/{path}")
    if status != 200:
        return [], 0
    try:
        data = json.loads(body)
    except ValueError:
        return [], 0
    if isinstance(data, list):  # /posts/expired-vouchers/ returns a plain list
        return data, 1
    posts = data.get("entities") or {}
    return list(posts.values()) if isinstance(posts, dict) else posts, data.get("totalPages") or 1


def scrape_latestdeals(url):
    """Return the store's codes (active first, then expired) from latestdeals, via the site's own JSON API."""
    # The store page sits behind an AWS WAF challenge and shows only each code's last 2 characters, but the
    # page loads its offers from a JSON API that answers plain requests with full codes:
    # /api/posts?merchantSlugDomain=<domain>&type=voucher&type=offer&page=N and /api/posts/expired-vouchers/<domain>
    domain = url.split("?")[0].rstrip("/").split("/")[-1]
    query = f"posts?merchantSlugDomain={domain}&type=voucher&type=offer&noExcerpts=true"
    posts, pages = latestdeals_posts(query)
    for page in range(2, pages + 1):
        posts += latestdeals_posts(f"{query}&page={page}")[0]
    expired, _ = latestdeals_posts(f"posts/expired-vouchers/{domain}")
    offers = [(post.get("code") or "", post.get("merchantName")) for post in posts + expired if isinstance(post, dict)]
    names = [name for _, name in offers if name]
    store = Counter(names).most_common(1)[0][0] if names else None
    return list(dict.fromkeys(code.strip() for code, name in offers if code.strip() and name in (store, None)))


def scrape_troupon(url):
    """Return the store's codes from a troupon {store}.troupon.com page, from one request."""
    html = fetch_html(url)
    # Codes look like data-coupon-code="HOMELIFE" data-coupon-url="https://cozyearth.com"; keep the most common store URL
    offers = re.findall(r'data-coupon-code="([^"]+)" data-coupon-url="([^"]*)"', html)
    if not offers:
        return []
    store = Counter(link for _, link in offers).most_common(1)[0][0]
    return list(dict.fromkeys(unescape(code).strip() for code, link in offers if link == store and code.strip()))


def scrape_couponupto(url):
    """Return the store's codes from a couponupto /coupons/{store} page, from one request."""
    page = url.split("?")[0].rstrip("/")
    soup = BeautifulSoup(fetch_html(page), "html.parser")
    # Code offers show the full code: <a class="get-deal-btn a_code" href=".../coupons/cozy-earth?..."><p id="code-...">.
    # Codes of other stores are mixed in; their links point to those stores' pages.
    codes = []
    for element in soup.select('a.a_code p[id^="code-"]'):
        link = element.find_parent("a").get("href", "").split("?")[0].rstrip("/")
        if link == page:
            codes.append(element.get_text(strip=True))
    return list(dict.fromkeys(code for code in codes if code))


def scrape_capitaloneshopping(url):
    """Return the store's codes from a capitaloneshopping /s/{domain}/coupon page, from one request."""
    html = unescape(fetch_html(url))
    # Each code offer has a button aria-label="Copy code PANTS40 and open NOBULL in new tab"; deals say
    # "No code required". Keep the most common store name in case other stores' offers show up.
    offers = re.findall(r'aria-label="Copy code (.+?) and open (.+?) in new tab"', html)
    if not offers:
        return []
    store = Counter(name for _, name in offers).most_common(1)[0][0]
    return list(dict.fromkeys(code.strip() for code, name in offers if name == store and code.strip()))


def scrape_shipthedeal(url):
    """Return the store's codes from a shipthedeal /store/{store}-coupon page."""
    html = fetch_html(url)
    # A plain request sometimes gets a small waiting page that reloads itself after 5 seconds
    if "window.location.reload()" in html and "_code" not in html:
        response = client.get(url, params={"js_render": "true", "premium_proxy": "true", "wait": "5000"}, timeout=TIMEOUT)
        html = response.text
    # "Show Code" opens a modal filled from hidden elements on the page:
    # id="getCode205108_code">WHOOPISNOBULL< and id="getCode205108_storename">NOBULL<
    codes = dict(re.findall(r'id="getCode(\d+)_code"[^>]*>([^<]*)<', html))
    stores = dict(re.findall(r'id="getCode(\d+)_storename"[^>]*>([^<]*)<', html))
    names = [name.strip() for name in stores.values() if name.strip()]
    store = Counter(names).most_common(1)[0][0] if names else None
    # Deals hold a note like "(No Code Needed)" instead of a code; real codes have no spaces
    return list(dict.fromkeys(
        unescape(code).strip() for offer_id, code in codes.items()
        if code.strip() and " " not in code.strip() and (stores.get(offer_id, "").strip() or None) in (store, None)
    ))


def lovecoupons_reveal(offer_id):
    """Return the full code from lovecoupons' offer modal (/go/3/<id>), or None."""
    html, status = get_response(f"https://www.lovecoupons.com/go/3/{offer_id}")
    code = re.search(r'<input[^>]*id="coupon-[0-9a-f]+"[^>]*value="([^"]+)"', html) if status == 200 else None
    return unescape(code.group(1)).strip() if code else None


def scrape_lovecoupons(url):
    """Return the store's codes from a lovecoupons /{store} page."""
    page = url.split("?")[0].split("#")[0].rstrip("/")
    html = fetch_html(page)
    # Offers are <article class="Offer" data-id="..." data-type="2" data-brand-slug="/tommy-john">; the site's script
    # treats types 2 and 17 as codes (3 is a sale, 9 shipping). Clicking opens the page with #r-<id>, which loads
    # the modal from /go/3/<id> holding the code, so each code costs one extra request. /go/2/ is the store redirect
    # and is never requested.
    slug = "/" + page.split("/")[-1]
    offers = re.findall(
        r'<article class="Offer[^"]*"[^>]*data-id="([^"]+)"[^>]*data-type="(\d+)"[^>]*data-brand-slug="([^"]+)"', html
    )
    ids = list(dict.fromkeys(offer_id for offer_id, kind, brand in offers if kind in ("2", "17") and brand == slug))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lovecoupons_reveal, ids))
    return list(dict.fromkeys(code for code in codes if code))


def couponbox_reveal(voucher_id):
    """Return the full code from couponbox's voucher API, or None."""
    body, status = get_response(f"https://www.couponbox.com/api/voucher/{voucher_id}")
    if status != 200:
        return None
    try:
        code = (json.loads(body).get("code") or "").strip()
    except ValueError:
        return None
    return code if code and " " not in code else None


def scrape_couponbox(url):
    """Return the store's codes (active first, then expired) from a couponbox /coupons/{store} page."""
    html = fetch_html(url)
    # The store's own offers are JSON in the Next.js payload: "activeVouchersGrouped":[[{...}]] and
    # "expiredVouchers":[{...}]; offers further down belong to cross-sell stores. Code offers have
    # "codeType":"copy_paste" but no code; the page's script gets it from /api/voucher/<id>, so each code costs
    # one extra request.
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    payload = "".join(json.loads(chunk) for chunk in chunks)
    decoder = json.JSONDecoder()
    vouchers = []
    for key in ('"activeVouchersGrouped":', '"expiredVouchers":'):
        start = payload.find(key)
        if start == -1:
            continue
        data, _ = decoder.raw_decode(payload, start + len(key))
        for item in data:
            vouchers += item if isinstance(item, list) else [item]
    ids = list(dict.fromkeys(
        voucher["id"] for voucher in vouchers if isinstance(voucher, dict) and voucher.get("codeType") == "copy_paste"
    ))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(couponbox_reveal, ids))
    # The same code is sometimes listed in different case ("DRLAURA" and "drlaura"); keep the first
    unique = {}
    for code in codes:
        if code:
            unique.setdefault(code.upper(), code)
    return list(unique.values())


def promopro_reveal(coupon_id, tail):
    """Return the full code from promopro's coupon detail page, or None."""
    html, status = get_response(f"https://www.promopro.com/coupon-codes/detail/{coupon_id}.html")
    code = re.search(r'<div id="codeText">([^<]+)</div>', html) if status == 200 else None
    if not code:
        return None
    code = unescape(code.group(1)).strip()
    # The card shows the code's last characters ("ST15"); a mismatch means the page is for another offer
    return code if code.upper().endswith(tail.upper()) else None


def scrape_promopro(url):
    """Return the store's codes from a promopro /coupon-codes/{store} page."""
    html = fetch_html(url)
    # Code offers are <article class="offer_card ... detail_filter_code"> showing only the code's tail in
    # <div class="code">ST15</div>, with data-cid="5876670" on the link. Clicking opens the site's own
    # /coupon-codes/detail/<cid>.html with the full code in <div id="codeText">, so each code costs one extra
    # request. The /redirect/voucher/ link goes to the store and is never requested.
    offers = []
    for card in re.findall(r'<article class="offer_card[^"]*\bdetail_filter_code\b.*?</article>', html, re.S):
        coupon_id = re.search(r'data-cid="(\d+)"', card)
        tail = re.search(r'<div class="code">([^<]+)</div>', card)
        # Some cards hold a note instead of a tail ("Learn How to Get Code at Tommy John")
        if coupon_id and tail and tail.group(1).strip() and " " not in tail.group(1).strip():
            offers.append((coupon_id.group(1), tail.group(1).strip()))
    offers = list(dict.fromkeys(offers))
    # Detail pages get blocked when many are requested at once, so fewer run in parallel than on other sites
    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = list(pool.map(lambda offer: promopro_reveal(*offer), offers))
    return list(dict.fromkeys(code for code in codes if code))


def scrape_rebatesme(url):
    """Return the store's codes (active first, then expired) from a rebatesme /en/stores/{store} page."""
    html = fetch_html(url)
    # Offers are in `var couponListObj = [[{...}], ...]`; each offer has "merchantUrlName" and
    # "couponCodeList":[{"code":"EARLY",...}] (empty for deals). The page's own store is `var urlNameInpage`.
    data = re.search(r"var couponListObj = (.*?);\s*\n", html)
    store = re.search(r'var urlNameInpage = "([^"]+)"', html)
    if not data:
        return []
    offers = [offer for group in json.loads(data.group(1)) for offer in (group if isinstance(group, list) else [group])]
    offers = [o for o in offers if isinstance(o, dict) and (not store or o.get("merchantUrlName") == store.group(1))]
    offers.sort(key=lambda offer: bool(offer.get("expired")))
    codes = [(item.get("code") or "").strip() for offer in offers for item in offer.get("couponCodeList") or []]
    return list(dict.fromkeys(code for code in codes if code))


def savings_reveal(popup_path):
    """Return the full code from a savings.com coupon popup, or None."""
    # Under load the popup sometimes redirects to the store page instead of blocking, so ask once more
    for attempt in range(2):
        html, status = get_response(f"https://www.savings.com{popup_path}")
        code = re.search(r'<input class="code[^"]*"[^>]*value="([^"]+)"', html) if status == 200 else None
        if code:
            return unescape(code.group(1)).strip()
        time.sleep(2)
    return None


def scrape_savings(url):
    """Return the store's codes from a savings.com /coupons/{domain} page."""
    html = fetch_html(url)
    # Code offers are <div class="module-deal ... hasCode ..." data-provider-name="DHGate"> holding
    # <input name="property-popup-url" value="/popup/detail/coupon-10911475.html">; that popup has the full code in
    # <input class="code ..." value="...">, so each code costs one extra request. Sponsored offers of other brands
    # are mixed in, so keep the most common provider.
    offers = re.findall(
        r'class="module-deal [^"]*\bhasCode\b[^"]*"[^>]*data-provider-name="([^"]+)"[^>]*>\s*'
        r'<input type="hidden" name="property-popup-url" value="([^"]+)"', html,
    )
    if not offers:
        return []
    store = Counter(name for name, _ in offers).most_common(1)[0][0]
    popups = list(dict.fromkeys(popup for name, popup in offers if name == store))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(savings_reveal, popups))
    return list(dict.fromkeys(code for code in codes if code))


def fetch_past_challenge(url):
    """fetch_html, but small WordPress sites sometimes answer with a "One moment, please..." challenge page first;
    a JS-rendered request gets past it."""
    html = fetch_html(url)
    if "<title>One moment, please...</title>" in html:
        response = client.get(url, params={"js_render": "true", "premium_proxy": "true", "wait": "5000"}, timeout=TIMEOUT)
        html = response.text
    return html


def scrape_savingheist(url):
    """Return the store's codes from a savingheist /store/{store}/ page, from one request."""
    html = fetch_past_challenge(url)
    # Code buttons look like <button ... data-coupon-id="205324" data-code="BULKRATE">Get Code</button>
    codes = re.findall(r'data-coupon-id="\d+" data-code="([^"]+)"', html)
    return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))


def scrape_coupongrouphy(url):
    """Return the store's codes from a coupongrouphy /dealstore/{store}/ page, from one request."""
    html = fetch_past_challenge(url)
    # Code offers are <span class="... masked_coupon" data-clipboard-text="JULIANASPICOLUK0" ...>
    codes = re.findall(r'masked_coupon[^"]*" data-clipboard-text="([^"]+)"', html)
    return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))


def _gocashback_codes(html):
    soup = BeautifulSoup(html, "html.parser")
    # www.gocashback.com/stores/{store}: <div class="coupon-code">... <span class="coupon-text">FRIEND</span>
    # promo.gocashback.com/{country}/store/{store}: <div class="coupon-item item-code">... <span class="code-box">BOR25</span>
    elements = soup.select(".coupon-code .coupon-text") + soup.select(".coupon-item .code-box")
    codes = [element.get_text(strip=True) for element in elements]
    return list(dict.fromkeys(code for code in codes if code and " " not in code))


def scrape_gocashback(url):
    """Return the store's codes from a gocashback store page. The promo.gocashback.com pages have the codes
    in plain HTML; the www.gocashback.com /stores/ pages only show them after JS rendering."""
    codes = _gocashback_codes(fetch_html(url))
    if codes:
        return codes
    # The render sometimes returns the page before the coupons load, so render once more when nothing is found
    for _ in range(2):
        response = client.get(url, params={"js_render": "true", "premium_proxy": "true", "wait": "5000"}, timeout=TIMEOUT)
        if response.status_code != 200:
            raise RuntimeError(f"status {response.status_code}")
        codes = _gocashback_codes(response.text)
        if codes:
            break
    return codes


def scrape_reediscount(url):
    """Return the store's codes from a reediscount /{store}/ page, from one request."""
    html = fetch_html(url)
    # Code buttons look like <a rel="nofollow" data-code="SK20" data-store_info="453780">
    codes = re.findall(r'<a rel="nofollow" data-code="([^"]+)" data-store_info=', html)
    return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))


def scrape_couponterra(url):
    """Return the store's codes from a couponterra /coupons/{domain} page, from one request."""
    html = fetch_html(url)
    # Codes are in full: <div class="get-code getCodeBtn" data-coupon-id="1235" data-code="VORTEX15" ...>
    codes = re.findall(r'data-coupon-id="\d+"\s+data-code="([^"]+)"', html)
    return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))


def scrape_couponskiss(url):
    """Return the store's codes from a couponskiss {store}.couponskiss.com page, from one request."""
    html = fetch_html(url)
    # Unknown stores show the homepage (canonical www.couponskiss.com), which has other stores' codes
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    if not canonical or "www.couponskiss.com" in canonical.group(1):
        return []
    # Codes are in data-clipboard-text="WELCOMEBACK"; deals say "Click to Redeem"
    codes = re.findall(r'data-clipboard-text="([^"]+)"', html)
    return list(dict.fromkeys(
        unescape(code).strip() for code in codes if code.strip() and code != "Click to Redeem" and " " not in code.strip()
    ))


def scrape_couponblender(url):
    """Return the store's codes from a couponblender /{store}/ page, from one request."""
    html = fetch_html(url)
    # Unknown stores show the homepage (canonical "https://www.couponblender.com//")
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    if not canonical or canonical.group(1).rstrip("/").endswith("couponblender.com"):
        return []
    # Each offer shows only the code's tail but holds it in full, next to a link on the store's site:
    # <input class="special_url" value="https://www.shopbop.com"> <div class="cr"> <input value="APP15" class="hidden_cv">
    # Expired offers further down use <div class="cr cr-n cr_exp">. Keep the most common site domain.
    offers = re.findall(
        r'class="special_url" value="([^"]*)">\s*<div class="cr[^"]*">\s*<input type="hidden" value="([^"]+)" class="hidden_cv">',
        html,
    )
    offers = [(re.sub(r"^www\.", "", re.sub(r"^https?://", "", site).split("/")[0]), code) for site, code in offers]
    if not offers:
        return []
    store = Counter(site for site, _ in offers).most_common(1)[0][0]
    return list(dict.fromkeys(unescape(code).strip() for site, code in offers if site == store and code.strip()))


def scrape_xyzcodes(url):
    """Return the store's codes from an xyzcodes /store/{store}/ page, from one request."""
    html = fetch_past_challenge(url)
    # WordPress "WP Coupons and Deals" plugin: <a data-type="code" data-coupon-id="13909" ...
    # data-clipboard-text="Welcome10">; deals use data-type="deal"
    codes = re.findall(r'<a data-type="code" data-coupon-id="\d+"[^>]*data-clipboard-text="([^"]+)"', html)
    return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))


def scrape_coponcodes(url):
    """Return the store's codes from a coponcodes /store/{store}/ page, from one request."""
    html = fetch_html(url)
    # Each code is hidden in <span id="code-2505" hidden>BFCM2025</span>
    codes = re.findall(r'<span id="code-\d+" hidden>([^<]+)</span>', html)
    return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))


def redeempromos_reveal(store_slug, cid):
    """Return the full code of one redeempromos offer from the site's getcode API, or None."""
    for attempt in range(len(ATTEMPTS)):
        try:
            response = client.post("https://redeempromos.com/welcome/getcode", data={"cid": cid, "grabit": 0}, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if response.status_code == 200:
            break
    else:
        return None
    try:
        data = json.loads(response.text)
    except ValueError:
        return None
    # The answer is [{"seourl":"nike","code":"SAVE20",...}]; unknown ids return 0. Some "Get Code" offers only
    # hold a placeholder like "OFFER ACTIVATED", so anything with a space is not a code.
    offer = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    code = (offer.get("code") or "").strip()
    return code if code and " " not in code and offer.get("seourl") == store_slug else None


def scrape_redeempromos(url):
    """Return the store's codes from a redeempromos /{store} page."""
    page = url.split("?")[0].rstrip("/")
    soup = BeautifulSoup(fetch_html(page), "html.parser")
    # Code offers are <a class="btn-reveal-wrap" href=".../nike?offer=7040"><span class="btn-label">Get Code</span>;
    # deals say "Get Deal". The page's script gets each code from POST /welcome/getcode (cid=<offer>), so each code
    # costs one extra request.
    cids = []
    for button in soup.select("a.btn-reveal-wrap[href]"):
        label = button.select_one("span.btn-label")
        match = re.search(r"[?&]offer=(\d+)", button["href"])
        if label and label.get_text(strip=True).lower() == "get code" and match:
            cids.append(match.group(1))
    slug = page.split("/")[-1]
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda cid: redeempromos_reveal(slug, cid), dict.fromkeys(cids)))
    return list(dict.fromkeys(code for code in codes if code))


def scrape_rebates(url):
    """Return the store's codes from a rebates.com /coupons/{domain} page, from one request."""
    return rebates_codes(fetch_html(url), url.split("?")[0].rstrip("/").split("/")[-1])


def rebates_codes(html, domain):
    """Codes of one store from a rebates.com page. Also used by url.py to check links, so both agree."""
    # Offers are JSON in the Next.js payload: {"title":...,"store":"macys.com",...,"code":"FRIEND","type":"coupon",...}.
    # Offers of other stores are mixed in, so keep only the page's own domain.
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    payload = "".join(json.loads(chunk) for chunk in chunks)
    # Links inside an offer contain braces ("sid={rebates-shopperid}"), so each code is matched to the nearest
    # "store" before it instead of parsing the object.
    codes = []
    # The two keys come in either order: "code":"FRIEND","type":"coupon" or "type":"coupon","code":"HONEY25OFF"
    for match in re.finditer(r'"code":"([^"]*)","type":"coupon"|"type":"coupon","code":"([^"]*)"', payload):
        stores = re.findall(r'"store":"([^"]+)"', payload[max(0, match.start() - 3000):match.start()])
        if stores and stores[-1] == domain:
            codes.append((match.group(1) or match.group(2) or "").strip())
    return list(dict.fromkeys(code for code in codes if code and " " not in code))


def _collect(scraper, links):
    """Run one site's scraper over every link and return all codes found. A failed link is logged and skipped."""
    coupons = []
    for url in dict.fromkeys(link.strip() for link in links or [] if link and link.strip()):
        try:
            codes = scraper(url)
            logger.info(f"{len(codes)} codes from {url}")
            coupons.extend(codes)
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
    return list(dict.fromkeys(coupons))


# Main function: fetch coupons from coupert
def fetch_coupons_from_coupert(links):
    return _collect(scrape_coupert, links)


# Main function: fetch coupons from hotdeals
def fetch_coupons_from_hotdeals(links):
    return _collect(scrape_hotdeals, links)


def fetch_coupons_from_slickdeals(links):
    """
    Fetch coupons from Slickdeals by extracting from voucher popups.
    Args: links (list) - List of Slickdeals page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            coupon_elements = soup.select('[data-attribute="code"]')

            for element in coupon_elements:
                id = element.get_attribute_list("data-id")
                if len(id) == 0:
                    continue

                try:
                    html2 = getZenrowsResponse(f"{url}#voucher-{id[0]}")
                    soup2 = BeautifulSoup(html2, "html.parser")
                    coupon_elements2 = soup2.select(
                        '[data-testid="voucherPopup-codeHolder-voucherType-code"] h4'
                    )
                    for element2 in coupon_elements2:
                        coupon = element2.text.strip()
                        if coupon:
                            coupons.append(coupon.upper())

                except Exception as e:
                    logger.error(f"Error scraping Slickdeals: {e}")
                    continue

        return coupons
    except Exception as e:
        logger.error(f"Error slickdeals fetching {url}: {e}")
        return []


def fetch_coupons_from_capitalone(links):
    return _collect(scrape_capitaloneshopping, links)


def fetch_coupons_from_rakuten(url):
    """
    Fetch coupons from Rakuten by selecting coupon code elements.
    Args: url (list) - List of Rakuten page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not url:
            return []

        urls = [link.strip() for link in url]  # remove whitespace
        coupons = []
        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            coupon_elements = soup.select(".click-to-copy-box .coupon-code")
            for element in coupon_elements:
                code = element.text.strip()
                if code is not None:
                    coupons.append(code.upper())
        return coupons
    except Exception as e:
        logger.error(f"Error rakuten fetching {url}: {e}")
        return []


# fetch coupons from rebates
def fetch_coupons_from_rebates(links):
    return _collect(scrape_rebates, links)


# Helper function to extract coupon code from offer object
def _extract_coupon_from_offer(offer):
    """
    Extract coupon code from an offer dictionary.
    Args: offer (dict) - Dictionary with offer/coupon data
    Returns: str or None - Uppercase coupon code or None
    """
    if not isinstance(offer, dict):
        return None

    # Check if it's a coupon type
    if offer.get("type") == "coupon" or offer.get("offerType") == "COUPON":
        if "code" in offer and offer["code"]:
            return offer["code"].upper()
        elif "couponCode" in offer and offer["couponCode"]:
            return offer["couponCode"].upper()
    return None


# Method 1: Try API endpoint
def _try_api_method(url):
    """
    Fetch coupons from RetailMeNot API endpoint.
    Args: url (str) - RetailMeNot store page URL
    Returns: list - List of uppercase coupon codes
    """
    try:
        store_slug = url.split("/")[-1]
        api_url = f"https://www.retailmenot.com/api/offers?merchant={store_slug}"

        response = fetchRequest(api_url)
        if response and response.status_code == 200:
            data = response.json()
            coupons = []
            if "offers" in data:
                for offer in data["offers"]:
                    code = _extract_coupon_from_offer(offer)
                    if code:
                        coupons.append(code)
            return coupons
    except Exception as e:
        logger.debug(f"API method failed for {url}: {e}")
    return []


# Method 2: Extract from __NEXT_DATA__ (Next.js structure)
def _try_next_data_method(soup):
    """
    Extract coupons from Next.js __NEXT_DATA__ script tag.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        next_data = soup.select_one("script#__NEXT_DATA__")
        if not next_data:
            return coupons

        json_data = json.loads(next_data.string)

        # Navigate through Next.js data structure
        if "props" in json_data:
            props = json_data["props"]
            if "pageProps" in props:
                page_props = props["pageProps"]

                # Look for offers/coupons in various possible locations
                offers = None
                if "offers" in page_props:
                    offers = page_props["offers"]
                elif (
                    "initialState" in page_props
                    and "offers" in page_props["initialState"]
                ):
                    offers = page_props["initialState"]["offers"]
                elif "merchant" in page_props and "offers" in page_props["merchant"]:
                    offers = page_props["merchant"]["offers"]

                if offers:
                    for offer in offers:
                        code = _extract_coupon_from_offer(offer)
                        if code:
                            coupons.append(code)
    except Exception as e:
        logger.debug(f"__NEXT_DATA__ parsing failed: {e}")
    return coupons


# Method 3: Extract from embedded JSON in scripts
def _find_coupons_in_json(obj, codes_list):
    """
    Recursively search for coupon codes in JSON structure.
    Args: obj (dict/list) - JSON object to search, codes_list (list) - List to append codes to
    Returns: None - Modifies codes_list in place
    """
    if isinstance(obj, dict):
        # Check if this object has a coupon code
        code = _extract_coupon_from_offer(obj)
        if code:
            codes_list.append(code)

        # Recursively check nested objects
        for value in obj.values():
            _find_coupons_in_json(value, codes_list)
    elif isinstance(obj, list):
        for item in obj:
            _find_coupons_in_json(item, codes_list)


def _try_script_json_method(soup):
    """
    Extract coupons from embedded JSON in script tags.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        script_tags = soup.find_all("script")

        for script in script_tags:
            if not script.string:
                continue

            try:
                # Look for window.__INITIAL_STATE__ or similar
                if (
                    "window.__INITIAL_STATE__" in script.string
                    or "initialState" in script.string
                ):
                    # Try to extract JSON
                    matches = re.findall(
                        r"window\.__INITIAL_STATE__\s*=\s*({.*?});",
                        script.string,
                        re.DOTALL,
                    )
                    if not matches:
                        matches = re.findall(
                            r"initialState\s*[:=]\s*({.*?})[,;]",
                            script.string,
                            re.DOTALL,
                        )

                    for match in matches:
                        try:
                            json_data = json.loads(match)
                            _find_coupons_in_json(json_data, coupons)
                        except Exception:
                            continue

                # Also look for direct coupon code patterns in JSON
                if '"code"' in script.string or '"couponCode"' in script.string:
                    code_matches = re.findall(
                        r'"(?:code|couponCode)"\s*:\s*"([A-Z0-9]{4,30})"',
                        script.string,
                        re.IGNORECASE,
                    )
                    for code in code_matches:
                        if 4 <= len(code) <= 30:
                            coupons.append(code.upper())
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Script JSON method failed: {e}")
    return coupons


# Method 4: Extract from HTML elements (original method)
def _extract_code_from_modal(html2):
    """
    Extract coupon code from modal HTML (JSON or HTML format).
    Args: html2 (str) - Modal HTML content (may be JSON or HTML)
    Returns: list - List of uppercase coupon codes
    """
    codes = []
    try:
        modal_data = json.loads(html2)
        if "modalHtml" in modal_data:
            soup2 = BeautifulSoup(modal_data["modalHtml"], "html.parser")

            # Try multiple selectors
            code_elements = soup2.select('[x-show="outclicked"] div[x-data]')
            if not code_elements:
                code_elements = soup2.select(".coupon-code, [data-code], .code")

            for elem in code_elements:
                code = elem.text.strip().replace("COPY", "").strip()
                if code and 4 <= len(code) <= 30:
                    codes.append(code.upper())
    except Exception:
        # If not JSON, try direct HTML parsing
        try:
            soup2 = BeautifulSoup(html2, "html.parser")
            code_elements = soup2.select(
                '.coupon-code, [data-code], .code, [class*="code"]'
            )
            for elem in code_elements:
                code = elem.text.strip()
                if code and 4 <= len(code) <= 30:
                    codes.append(code.upper())
        except Exception:
            pass
    return codes


def _try_html_elements_method(soup, url):
    """
    Extract coupons from HTML elements using original selectors.
    Args: soup (BeautifulSoup) - Parsed HTML page, url (str) - Page URL
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        coupon_elements = soup.select('a[data-content-datastore="offer"]')

        for element in coupon_elements:
            try:
                xdata = element.get("x-data") or (
                    element.get_attribute_list("x-data")[0]
                    if element.get_attribute_list("x-data")
                    else None
                )
                href = element.get("href")

                if xdata and href:
                    # Clean and parse x-data
                    clean_xdata = (
                        xdata.replace("outclickHandler({", "{")
                        .replace("})", "}")
                        .replace("'", '"')
                    )
                    jsonData = json.loads(clean_xdata)

                    if jsonData.get("offerType") == "COUPON":
                        parsedUrl = urlparse(href)
                        queryParams = parse_qs(parsedUrl.query)

                        template = queryParams.get("template", [""])[0]
                        offerId = queryParams.get("offer_uuid", [""])[0]
                        merchantId = queryParams.get("merchant_uuid", [""])[0]

                        if offerId:
                            itemUrl = f"https://www.retailmenot.com/modals/outclick/{offerId}/?template={template}&trigger=entrance_modal&merchant={merchantId}"

                            html2 = getZenrowsResponse(itemUrl)
                            if html2:
                                codes = _extract_code_from_modal(html2)
                                coupons.extend(codes)
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"HTML elements method failed: {e}")
    return coupons


# Method 5: Extract from visible coupon code elements
def _try_visible_elements_method(soup):
    """
    Extract coupons from visible elements that might contain codes.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        code_elements = soup.find_all(
            ["div", "span", "button", "input", "code"],
            class_=re.compile(r"coupon|code|promo", re.IGNORECASE),
        )

        excluded_texts = {"CODE", "COUPON", "COPY", "SAVE", "GET", "SHOP"}

        for element in code_elements:
            text = element.get_text(strip=True)
            # Check if it matches coupon code pattern
            if text and re.match(r"^[A-Z0-9]{4,30}$", text):
                if text not in excluded_texts:
                    coupons.append(text.upper())

            # Also check data attributes
            for attr in ["data-code", "data-coupon", "data-coupon-code"]:
                if element.get(attr):
                    coupons.append(element[attr].upper())
    except Exception as e:
        logger.debug(f"Visible elements method failed: {e}")
    return coupons


def fetch_coupons_from_retailmenot(links):
    return _collect(scrape_retailmenot, links)


# Main function: fetch coupons from retailmenot
# def fetch_coupons_from_retailmenot(links):
#     """Fetch coupons from RetailMeNot using multiple extraction methods."""
#     try:
#         if not links:
#             return []

#         urls = [link.strip() for link in links]  # remove whitespace
#         coupons = []

#         for url in urls:
#             try:
#                 # Method 1: Try API endpoint
#                 api_coupons = _try_api_method(url)
#                 if api_coupons:
#                     coupons.extend(api_coupons)
#                     # If API method succeeds, skip HTML methods for this URL
#                     continue

#                 # Fetch HTML once for all HTML-based methods
#                 html = getZenrowsResponse(url)
#                 if not html:
#                     continue

#                 soup = BeautifulSoup(html, 'html.parser')

#                 # Method 2: Try __NEXT_DATA__
#                 next_data_coupons = _try_next_data_method(soup)
#                 if next_data_coupons:
#                     coupons.extend(next_data_coupons)
#                     # If Next.js method succeeds, skip remaining methods for this URL
#                     continue

#                 # Method 3: Try embedded JSON in scripts
#                 script_coupons = _try_script_json_method(soup)
#                 if script_coupons:
#                     coupons.extend(script_coupons)
#                     # If script method succeeds, skip remaining methods for this URL
#                     continue

#                 # Method 4: Try HTML elements
#                 html_coupons = _try_html_elements_method(soup, url)
#                 if html_coupons:
#                     coupons.extend(html_coupons)
#                     # If HTML elements method succeeds, skip remaining methods for this URL
#                     continue

#                 # Method 5: Try visible elements (fallback)
#                 visible_coupons = _try_visible_elements_method(soup)
#                 if visible_coupons:
#                     coupons.extend(visible_coupons)

#             except Exception as e:
#                 logger.error(f'Error retailmenot fetching {url}: {e}')
#                 continue

#         # Return unique coupons
#         return list(set(coupons))

#     except Exception as e:
#         logger.error(f'Error retailmenot fetching: {e}')
#         return []


# fetch coupons from joinhoney
def fetch_coupons_from_joinhoney(links):
    return _collect(scrape_joinhoney, links)


# fetch coupons from dealcatcher
def fetch_coupons_from_dealcatcher(links):
    """
    Fetch coupons from DealCatcher by selecting coupon code elements.
    Args: links (list) - List of DealCatcher page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")

            coupon_elements = soup.select(".coupon-box .coupon-code")
            for element in coupon_elements:
                coupon = element.text.strip()
                if coupon is not None:
                    coupons.append(coupon.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error dealcatcher fetching {url}: {e}")
        return []


# fetch coupons from dontpayfull
# def fetch_coupons_from_dontpayfull(links):
#     try:
#         if not links:
#             return []

#         # remove whitespace
#         urls = [link.strip() for link in links]
#         coupons = []
#         for url in urls:
#             html = getZenrowsResponse(url)

#             # BeautifulSoup parsing
#             soup = BeautifulSoup(html, 'html.parser')

#             coupon_elements = soup.select('.card.obox.code[data-otype="code"]')
#             for element in coupon_elements:

#                 # get idAttribute
#                 idAttribute = element.get_attribute_list('data-id')
#                 classAttribute = element.get_attribute_list('class')

#                 if len(idAttribute) == 0 or len(classAttribute) == 0:
#                     continue

#                 if 'sponsored' not in classAttribute[0]:
#                     try:
#                         html2 = getZenrowsResponse(f'https://www.dontpayfull.com/coupons/getcoupon?id={idAttribute[0]}')
#                         soup2 = BeautifulSoup(html2, 'html.parser')

#                         coupon_elements2 = soup2.select('.code-box.code h2')
#                         # break
#                         for element2 in coupon_elements2:
#                             code = element2.text.strip()
#                             if code is not None:
#                                 coupons.append(code.upper())
#                     except Exception as e:
#                         logger.error(f'Error scraping dontPayFull: {e}')
#                         continue


#         return coupons
#     except Exception as e:
#         logger.error(f'Error dontpayfull fetching {url}: {e}')
#         return []
async def fetch_coupons_from_dontpayfull(links):
    # The scraper blocks, so it runs in a worker thread to keep the event loop free
    return await asyncio.to_thread(_collect, scrape_dontpayfull, links)


# fetch coupons from vaouchercodes
def fetch_coupons_from_vouchercodes(url):
    """
    Fetch coupons from VoucherCodes using generic HTML extraction.
    Args: url (str) - VoucherCodes page URL
    Returns: list - List of uppercase coupon codes
    """
    try:
        html = scrape_with_requests(url, headers)
        return extract_coupons_from_html(html)
    except Exception as e:
        logger.error(f"Error vouchercodes fetching {url}: {e}")
        return []


# def fetch_coupons_from_couponbirds(links):
#     global cookies
#     coupons = []
#     try:
#         if not links:
#             return []

#         urls = [link.strip() for link in links]     # remove whitespace
#         for url in urls:
#             # html = getZenrowsResponse(url, isProxy=True)
#             html = getZenrowsWithCookiesResponse(url, isProxy=True, isCookies=True, isJsRender=True)
#             if not html:
#                 continue

#             soup = BeautifulSoup(html, 'html.parser')
#             coupon_elements = soup.select('.get-code a[data-url]')
#             for element in coupon_elements:
#                 attribute = element.get_attribute_list('data-url')[0]
#                 if attribute:
#                     html2 = getZenrowsWithCookiesResponse(attribute, isProxy=True, isCookies=True, isJsRender=True)
#                     soup2 = BeautifulSoup(html2, 'html.parser')


#                     coupon_elements2 = soup2.select('.modal-body .cp-code input#copy-recommend-code')
#                     for element2 in coupon_elements2:
#                         code = element2.get('value')
#                         if code is not None:
#                             coupons.append(code.upper())
#                     # print(f'Coupons: {coupons}')
#                     print(f'Coupons: {len(coupons)} found so far.')
#                     print(f'{coupons}')
#                     if len(coupons) >= 30:
#                         break
#         cookies = None
#         return coupons
#     except Exception as e:
#         cookies = None
#         logger.error(f'Error couponbirds fetching {links}: {e}')
#         return coupons
#     finally:
#         cookies = None
#         return coupons
async def fetch_coupons_from_couponbirds(links):
    """
    Fetch coupons from CouponBirds using async methods and modal extraction.
    Args: links (list) - List of CouponBirds page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    global cookies
    coupons = []
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        client = zenrowsClient()
        responses = await asyncio.gather(*[fetch_page(client, url) for url in urls])

        coupon_urls = []
        for response in responses:
            if response:
                html = response  # fetch_page already returns the text content
                soup = BeautifulSoup(html, "html.parser")
                coupon_elements = soup.select(".get-code a[data-url]")

                for element in coupon_elements:
                    attribute = element.get_attribute_list("data-url")[0]

                    if attribute:
                        coupon_urls.append(attribute)
        try:
            responses = await asyncio.gather(
                *[fetch_page(client, url) for url in coupon_urls]
            )

            for response in responses:
                html2 = response  # fetch_page already returns the text content
                soup2 = BeautifulSoup(html2, "html.parser")

                coupon_elements2 = soup2.select(
                    ".modal-body .cp-code input#copy-recommend-code"
                )
                for element2 in coupon_elements2:
                    code = element2.get("value")
                    if code is not None:
                        coupons.append(code.upper())
        except Exception as e:
            logger.error(f"Error couponbirds fetching {coupon_urls}: {e}")
            return []
        finally:
            cookies = None
            return list(set(coupons))
    except Exception as e:
        cookies = None
        logger.error(f"Error couponbirds fetching {links}: {e}")
        return coupons
    finally:
        cookies = None
        return coupons


async def fetch_coupons_from_offers(links):
    # The scraper blocks, so it runs in a worker thread to keep the event loop free
    return await asyncio.to_thread(_collect, scrape_offers, links)


# def fetch_coupons_from_offers(links):
#     try:
#         if not links:
#             return []

#         # remove whitespace
#         urls = [link.strip() for link in links]
#         coupons = []
#         for url in urls:
#             html = getZenrowsResponse(url)
#             if not html:
#                 continue

#             soup = BeautifulSoup(html, 'html.parser')

#             elements1 = soup.select('[data-offer-id] > [data-content-datastore="offer"]')
#             for element in elements1:
#                 # coupon = element['acsimpritem']
#                 attribute1 = element.get_attribute_list('x-data')
#                 attribute2 = element.get_attribute_list('@click.prevent')

#                 if len(attribute1) == 0 or len(attribute2) == 0:
#                     continue

#                 offerId = attribute1[0].split("'")[1]
#                 hasCode = 'hasCode: true' in attribute2[0]
#                 if hasCode:
#                     try:
#                         html2 = getZenrowsResponse(f'{url}?em={offerId}')
#                         soup2 = BeautifulSoup(html2, 'html.parser')

#                         elements2 = soup2.select('[x-data="commerceModal"] [x-data]')
#                         for element2 in elements2:
#                             codeAttribute = element2.get_attribute_list('x-data')[0]
#                             coupon = codeAttribute.split("'")[3]
#                             if coupon:
#                                 coupons.append(coupon.upper())
#                     except Exception as e:
#                         logger.error(f'Error fetching coupon from offers: {e}')
#                         continue

#         return coupons
#     except Exception as e:
#         logger.error(f'Error offers fetching {links}: {e}')
#         return []


def fetch_coupons_from_tobecoupon(url):
    """
    Fetch coupons from ToBeCoupon using generic HTML extraction.
    Args: url (str) - ToBeCoupon page URL
    Returns: list - List of uppercase coupon codes
    """
    try:
        html = scrape_with_requests(url, headers)
        return extract_coupons_from_html(html)
    except Exception as e:
        logger.error(f"Error tobecoupon fetching {url}: {e}")
        return []


def fetch_coupons_from_discountime(links):
    """
    Fetch coupons from Discountime using API POST request.
    Args: links (list) - List of Discountime page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        coupons = []
        urls = [link.strip() for link in links]  # remove whitespace
        for url in urls:
            payload = {
                "type": 1,
                "pageNo": 1,
                "pageSize": 500,
                "brandAliasName": url.split("/")[-1],
            }

            response, status = postZenrowsResponse(
                "https://www.discountime.com/system/coupon/queryByAliasName",
                data=json.dumps(payload),
            )
            if not response:
                continue

            jsondata = json.loads(response)
            if "data" in jsondata:
                if "result" in jsondata["data"]:
                    results = jsondata["data"]["result"]
                    for item in results:
                        if "code" in item:
                            coupons.append(item["code"].upper())

        return coupons
    except Exception as e:
        logger.error(f"Error discountime fetching {e}")
        return []


def fetch_coupons_from_couponcode(url):
    """
    Fetch coupons from CouponCode using generic HTML extraction.
    Args: url (str) - CouponCode page URL
    Returns: list - List of uppercase coupon codes
    """
    try:
        html = scrape_with_requests(url, headers)
        return extract_coupons_from_html(html)
    except Exception as e:
        logger.error(f"Error couponcode fetching {url}: {e}")
        return []


def fetch_coupons_from_coupons(url):
    return _collect(scrape_coupons, url)


def fetch_coupons_from_karma(links):
    """
    Fetch coupons from Karma by parsing JSON response.
    Args: links (list) - List of Karma API URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        logger.info(f"fetch_coupons_from_karma {links}")
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for url in urls:
            logger.info(f"URL {url}")
            response = getZenrowsResponse(url)
            if not response:
                continue

            jsondata = json.loads(response)
            if "data" in jsondata:
                for item in jsondata["data"]:
                    if "code" in item:
                        coupons.append(item["code"].upper())

        return coupons
    except Exception as e:
        logger.error(f"Error karma fetching {url}: {e}")
        return []


def fetch_coupons_from_simplycodes(links):
    return _collect(scrape_simplycodes, links)


def fetch_coupons_from_savings(links):
    return _collect(scrape_savings, links)


# def fetch_coupons_from_savings(links):
#     try:
#         if not links:
#             return []
#         urls = [link.strip() for link in links]     # remove whitespace
#         coupons = []

#         for url in urls:
#             html = getZenrowsResponse(url)
#             if not html:
#                 continue
#             soup = BeautifulSoup(html, 'html.parser')
#             coupon_elements = soup.select('.module-deal.showValue.codePeek[data-offer-id]')
#             for element in coupon_elements:
#                 attribute = element.get_attribute_list('id')
#                 if len(attribute) == 0:
#                     continue
#                 try:
#                     couponId = attribute[0].split('-')[-1]
#                     html2 = getZenrowsResponse(f'https://www.savings.com/popup/detail/coupon-{couponId}.html')

#                     soup2 = BeautifulSoup(html2, 'html.parser')
#                     coupon_elements2 = soup2.select('#code-wrapper input.code')
#                     for element2 in coupon_elements2:
#                         coupon = element2.get('value')
#                         if coupon:
#                             coupons.append(coupon)
#                 except Exception as e:
#                     logger.error(f'Error fetching coupon from savings: {e}')
#                     continue

#         return coupons
#     except Exception as e:
#         logger.error(f'Error savings fetching {e}')
#         return []


def fetch_coupons_from_dealspotr(links):
    """
    Fetch coupons from Dealspotr by selecting hidden input elements.
    Args: links (list) - List of Dealspotr page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            coupon_elements = soup.select("input.dnone")
            for element in coupon_elements:
                coupon = element.get("value")
                if coupon:
                    coupons.append(coupon.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error dealspotr fetching {e}")
        return []


def fetch_coupons_from_promocodes(links):
    return _collect(scrape_promocodes, links)


def fetch_coupons_from_couponchief(links):
    return _collect(scrape_couponchief, links)


def fetch_coupons_from_goodshop(links):
    return _collect(scrape_goodshop, links)


# Main function: fetch coupons from tenereteam
def fetch_coupons_from_tenereteam(links):
    return _collect(scrape_tenereteam, links)


def fetch_coupons_from_couponcause(links):
    return _collect(scrape_couponcause, links)


def fetch_coupons_from_goodsearch(links):
    return _collect(scrape_goodsearch, links)


def fetch_coupons_from_valuecom(links):
    return _collect(scrape_valuecom, links)


def fetch_coupons_from_lovecoupons(links):
    return _collect(scrape_lovecoupons, links)


def fetch_coupons_from_knoji(links):
    """
    Fetch coupons from Knoji by parsing JSON-LD scripts and regex.
    Args: links (list) - List of Knoji page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        coupons = []

        def find_codes_recursive(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "couponCode" and v:
                        coupons.append(str(v).strip())
                    else:
                        find_codes_recursive(v)
            elif isinstance(obj, list):
                for item in obj:
                    find_codes_recursive(item)

        urls = [link.strip() for link in links]  # remove whitespace
        for url in urls:
            logger.info(f"Fetching knoji URL: {url}")
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            scripts = soup.find_all("script", type="application/ld+json")

            for script in scripts:
                if script.string:
                    try:

                        data = json.loads(script.string.strip())

                        find_codes_recursive(data)
                    except (json.JSONDecodeError, Exception):
                        continue

            if not coupons:
                raw_matches = re.findall(r'"couponCode"\s*:\s*"([^"]+)"', html)
                coupons.extend(raw_matches)

        return coupons
    except Exception as e:
        logger.error(f"Error knoji fetching {e}")
        return []


def fetch_coupons_from_swagbucks(links):
    """
    Fetch coupons from Swagbucks by extracting data-clipboard-text.
    Args: links (list) - List of Swagbucks page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []
        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []

        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            coupon_elements = soup.select(
                '.sbCouponCodeWrapper[data-offer-type="coupon"]'
            )

            for element in coupon_elements:
                code = element.get_attribute_list("data-clipboard-text")
                if len(code) == 0:
                    continue
                coupons.append(code[0].strip().upper())

        return coupons
    except Exception as e:
        logger.error(f"Error swagbucks fetching {e}")
        return []


# Helper function: Extract slug from URL
def _extract_joinsmarty_slug(url):
    """
    Extract merchant slug from JoinSmarty URL.
    Args: url (str) - JoinSmarty page URL
    Returns: str or None - Merchant slug or None
    """
    try:
        slug = url.split("/")[-1].replace("-coupons", "")
        return slug if slug else None
    except Exception:
        return None


# Helper function: Extract coupon codes from API response
def _extract_joinsmarty_coupons_from_response(response_data):
    """
    Extract coupon codes from JoinSmarty API response.
    Args: response_data (dict) - API response dictionary
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        if "data" in response_data:
            items = response_data["data"]
            for item in items:
                if item.get("offer_type") == "coupon" and item.get("offer_code"):
                    code = item["offer_code"].upper().strip()
                    if code:
                        coupons.append(code)
    except Exception as e:
        logger.debug(f"Error extracting coupons from JoinSmarty response: {e}")
    return coupons


# Helper function: Fetch coupons from API for a single URL
def _fetch_joinsmarty_coupons_for_url(url):
    """
    Fetch coupons from JoinSmarty API for a single URL.
    Args: url (str) - JoinSmarty page URL
    Returns: list - List of uppercase coupon codes
    """
    try:
        slug = _extract_joinsmarty_slug(url)
        if not slug:
            logger.debug(f"Could not extract slug from URL: {url}")
            return []

        api_url = f"https://www.joinsmarty.com/api/merchant/{slug}/coupons?page=1"
        response = getZenrowsResponse(api_url)

        if not response:
            return []

        data = json.loads(response)
        return _extract_joinsmarty_coupons_from_response(data)

    except Exception as e:
        logger.debug(f"Error fetching JoinSmarty coupons for {url}: {e}")
        return []


# Main function: fetch coupons from joinsmarty
def fetch_coupons_from_joinsmarty(links):
    """
    Fetch coupons from JoinSmarty using API endpoint.
    Args: links (list) - List of JoinSmarty page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    if not links:
        return []

    coupons = []
    urls = [link.strip() for link in links]

    for url in urls:
        try:
            slug = url.rstrip("/").split("/")[-1].replace("-coupons", "")
            logger.info(f"Scraping JoinSmarty API for: {slug}")

            api_url = f"https://www.joinsmarty.com/api/merchant/{slug}/coupons?page=1"
            response = getZenrowsResponse(api_url)

            if not response:
                continue

            data = json.loads(response)
            items = data.get("data", [])

            for item in items:
                code = item.get("offer_code")
                if item.get("offer_type") == "coupon" and code:
                    coupons.append(code.strip().upper())

        except Exception as e:
            logger.error(f"Error scraping JoinSmarty for {url}: {e}")

    unique_coupons = list(set(coupons))

    if unique_coupons:
        logger.info(f"Found {len(unique_coupons)} unique coupons from JoinSmarty")

    return unique_coupons


def fetch_coupons_from_rebatesme(links):
    return _collect(scrape_rebatesme, links)


def fetch_coupons_from_refermate(links):
    return _collect(scrape_refermate, links)


def fetch_coupons_from_discountreactor(links):
    return _collect(scrape_discountreactor, links)


def fetch_coupons_from_couponbox(links):
    return _collect(scrape_couponbox, links)


# Main function: fetch coupons from dealdrop
def fetch_coupons_from_dealdrop(links):
    return _collect(scrape_dealdrop, links)


def fetch_coupons_from_revounts(links):
    """
    Fetch coupons from Revounts by selecting coupon button elements.
    Args: links (list) - List of Revounts page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for url in urls:
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            coupon_elements = soup.select('[id*="coupon-btn-code"]')

            for element in coupon_elements:
                code = element.string.strip()
                if code is not None:
                    coupons.append(code.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error revounts fetching {e}")
        return []


def fetch_coupons_from_dazzdeals(links):
    return _collect(scrape_dazzdeals, links)


def fetch_coupons_from_heydiscount(links):
    """
    Fetch coupons from HeyDiscount using modal extraction with coupon IDs.
    Args: links (list) - List of HeyDiscount page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            coupon_elements = soup.select(".elCouponCustom")

            for element in coupon_elements:
                couponId = element.get_attribute_list("data-value")
                if len(couponId) == 0:
                    continue

                if element.text.strip() != "Copy Code":
                    continue

                try:
                    html2 = getZenrowsResponse(f"?coupon-id={couponId[0]}")
                    soup2 = BeautifulSoup(html2, "html.parser")

                    coupon_elements2 = soup2.select(".elCouponCode")
                    for element2 in coupon_elements2:
                        code = element2.text.strip()
                        if code is not None:
                            coupons.append(code)

                except Exception as e:
                    logger.error(
                        f"[fetch_coupons_from_heydiscount]Error scraping Hydiscount: {e}"
                    )
                    continue

        return coupons
    except Exception as e:
        logger.error(f"Error heydiscount fetching {e}")
        return []


def fetch_coupons_from_couponbind(links):
    return _collect(scrape_couponbind, links)


def fetch_coupons_from_promopro(links):
    return _collect(scrape_promopro, links)


# Main function: fetch coupons from greenpromocode
def fetch_coupons_from_greenpromocode(links):
    return _collect(scrape_greenpromocode, links)


def fetch_coupons_from_deala(links):
    return _collect(scrape_deala, links)


def fetch_coupons_from_troupon(links):
    return _collect(scrape_troupon, links)


def fetch_coupons_from_worthepenny(links):
    return _collect(scrape_worthepenny, links)


def fetch_coupons_from_savingheist(links):
    return _collect(scrape_savingheist, links)


def fetch_coupons_from_coupongrouphy(links):
    return _collect(scrape_coupongrouphy, links)


def fetch_coupons_from_joincheckmate(links):
    return _collect(scrape_joincheckmate, links)


def fetch_coupons_from_faircoupons(links):
    coupons = []

    try:

        if not links:
            return []

        urls = [link.strip() for link in links]

        for url in urls:
            logger.info(f"Scraping FairCoupons URL: {url}")

            html = getZenrowsResponse(url, js_render=True)

            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            raw_scripts = re.findall(r'self\.__next_f\.push\(\[1,"(.+?)"\]\)', html)
            for item in raw_scripts:
                json_str = item.replace('\\"', '"').replace("\\\\", "\\")
                found_in_json = re.findall(r'"couponCode"\s*:\s*"([^"]*)"', json_str)
                for c in found_in_json:
                    if c.strip():
                        coupons.append(c.strip().upper())

            elements = soup.find_all("div", id="coupon_code")
            if not elements:
                elements = soup.select("div.modal_couponCode__1w92H")

            for el in elements:
                text = el.get_text(strip=True)
                if text:
                    coupons.append(text.upper())

    except Exception as e:
        logger.error(f"Error scraping FairCoupons: {e}")

    final_results = list(set([c.strip() for c in coupons if c and len(c.strip()) > 1]))
    logger.info(f"Total Found: {len(final_results)} coupons")
    return final_results

    
def fetch_coupons_from_dealam(links):
    return _collect(scrape_dealam, links)

def fetch_coupons_from_coponcodes(links):
    return _collect(scrape_coponcodes, links)
    
def fetch_coupons_from_zensavings(links):
   
    try:
        if not links:
            return []

        coupon_codes = []
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://www.google.com",
            "Connection": "keep-alive",
        })

        for url in links:
            response = session.get(url, timeout=20)
            html = response.text
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select("[data-code]")
            for el in elements:
                code = el.get("data-code", "").strip()
                if code:
                    coupon_codes.append(code.upper())

        unique = list(set(coupon_codes))
        logger.info(f"Found {len(unique)} unique coupons from ZenSaving")
        return unique

    except Exception as e:
        logger.error("Error:", e)
        return []
        

def fetch_coupons_from_kotag(links):
    return _collect(scrape_kotag, links)
    
def fetch_coupons_from_xyzcodes(links):
    return _collect(scrape_xyzcodes, links)
    
    
def fetch_coupons_from_couponblender(links):
    return _collect(scrape_couponblender, links)


def fetch_coupons_from_couponpromo(links):
    return _collect(scrape_couponpromo, links)
    
def fetch_coupons_from_shipthedeal(links):
    return _collect(scrape_shipthedeal, links)
    
    
def fetch_coupons_from_couponkiss(links):
    return _collect(scrape_couponskiss, links)
    
    
def fetch_coupons_from__couponterra(links):
    return _collect(scrape_couponterra, links)
    
def fetch_coupons_from_rediscount(links):
    return _collect(scrape_reediscount, links)
    
def fetch_coupons_from_getscoupon(links):
    return _collect(scrape_getscoupon, links)
    
    
def fetch_coupons_from_couponsgo(links):
    return _collect(scrape_couponsgo, links)
    
    
    
def fetch_coupons_from_promosreview(links):
    return _collect(scrape_promosreview, links)
    
    
def fetch_coupons_from_couponupto(links):
    return _collect(scrape_couponupto, links)
    
def fetch_coupons_from_trustdeals(links):
    return _collect(scrape_trustdeals, links)
    
def fetch_coupons_from_gocashback(links):
    return _collect(scrape_gocashback, links)
    
def fetch_coupons_from_latest_deals(links):
    return _collect(scrape_latestdeals, links)


def fetch_coupons_from_redeempromos(links):
    return _collect(scrape_redeempromos, links)

    