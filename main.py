import json
import os
import random
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html import unescape

import requests
from bs4 import BeautifulSoup
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
    """Return (html, status). Retries when blocked (422) or when the request itself fails
    (timeout, network error); any other status, like a 404, returns immediately."""
    for attempt, params in enumerate(ATTEMPTS, 1):
        try:
            response = client.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            # The exception text contains the ZenRows URL with the API key, so only its type is shown
            if attempt == len(ATTEMPTS):
                raise RuntimeError(f"request failed: {type(e).__name__}") from None
            print(f"Retry ({type(e).__name__}) {url}")
            continue
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


def reveal_all(reveal, items, workers=5, pause=3):
    """Run reveal(*item) for every item in parallel and return the codes found, in item order.
    A reveal that raises (blocked or network error) gets one more try after a short pause;
    a reveal that returns None found no code and is not retried."""
    def attempt(item):
        try:
            return reveal(*item), False
        except Exception:
            return None, True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt, items))
        failed = [i for i, (_, blocked) in enumerate(results) if blocked]
        if failed:
            time.sleep(pause)
            for i, result in zip(failed, pool.map(attempt, [items[i] for i in failed])):
                results[i] = result
    still_failed = sum(blocked for _, blocked in results)
    if still_failed:
        print(f"{still_failed} of {len(items)} reveals failed twice")
    return list(dict.fromkeys(code for code, _ in results if code))


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


def fetch_top_coupert(url, tries=10, pause=3):
    """Return (html, status) for a top.coupert.com page. The site blocks (422) most requests at random,
    plain and JS-rendered alike, so it gets more tries, switching between the two."""
    response = None
    for attempt in range(tries):
        params = {"js_render": "true", "premium_proxy": "true"} if attempt % 2 else {}
        try:
            response = client.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"Retry ({type(e).__name__}) {url}")
            continue
        if response.status_code != 422:
            return response.text, response.status_code
        if attempt < tries - 1:
            time.sleep(pause)
    if response is None:
        raise RuntimeError("request failed")
    return response.text, response.status_code


def scrape_coupert(url):
    """Return the store's codes from a coupert /shop/{domain} or top.coupert.com /coupons/{name}-promo-code page."""
    if "top.coupert.com" in url:
        html, status = fetch_top_coupert(url)
        if status != 200:
            raise RuntimeError(f"status {status}")
        # Every card holds its code in full: <span class="hiddenCode">WELCOME15OFF</span>
        codes = re.findall(r'class="hiddenCode"[^>]*>([^<]+)<', html)
        return list(dict.fromkeys(unescape(code).strip() for code in codes if code.strip()))
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
            print(f"Retry ({type(e).__name__}) {url}")
        if attempt < tries:
            time.sleep(pause)
    raise RuntimeError(f"blocked {tries} times")


def scrape_valuecom(url):
    """Return the store's codes from a valuecom {store}.valuecom.com page."""
    # valuecom blocks most requests (422) at random, so it gets plain retries instead of get_response;
    # blocks come in streaks, so after a fully blocked round it waits a bit and tries one more round
    try:
        html = fetch_plain_retry(url)
    except RuntimeError as e:
        if not str(e).startswith("blocked"):
            raise
        time.sleep(10)
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
        print(f"Retry ({page.status_code}) {url}")
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
            print(f"Could not reveal codes ({response.status_code}) {url}")
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
            print(f"Only the first page of {key} is read: {url}")
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
    """Return the full code from retailmenot's post-click modal for one offer, or None. Raises when blocked."""
    body, status = get_response(f"https://www.retailmenot.com/modals/outclick/{uuid}/?template=merchant&trigger=entrance_modal")
    if status != 200:
        raise RuntimeError(f"status {status}")
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
    return reveal_all(retailmenot_reveal, [(uuid,) for uuid in uuids])


def offers_reveal(page, uuid):
    """Return the full code from offers.com's code modal (?em=<uuid>), or None. Raises when blocked."""
    html, status = get_response(f"{page}?em={uuid}")
    if status != 200:
        raise RuntimeError(f"status {status}")
    # The modal renders as x-data="{ copiedButtonClicked: false, code: 'first25' }"
    code = re.search(r"copiedButtonClicked: false, code: '([^']+)'", unescape(html))
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
    return reveal_all(offers_reveal, [(page, uuid) for uuid in uuids])


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
    """Return the full code from promopro's coupon detail page, or None. Raises when blocked."""
    # Plain requests to detail pages are always blocked (422) while JS rendering gets through, so go straight to it
    response = client.get(f"https://www.promopro.com/coupon-codes/detail/{coupon_id}.html",
                          params={"js_render": "true", "premium_proxy": "true"}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"status {response.status_code}")
    code = re.search(r'<div id="codeText">([^<]+)</div>', response.text)
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
    return reveal_all(promopro_reveal, offers)


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


def run(scraper, urls):
    """Scrape each URL with one site's function and print its codes and time."""
    for url in urls:
        start = time.time()
        try:
            codes = scraper(url)
            print(f"{url}\n  Codes ({len(codes)}): {codes}")
        except Exception as e:
            print(f"{url}\n  Failed: {e}")
        print(f"  Response time: {time.time() - start:.2f} seconds\n")


if __name__ == "__main__":
    from test_links import TEST_LINKS

    # Run one site at a time; comment out the others
    # run(scrape_hotdeals, TEST_LINKS["hotdeals"])
    # run(scrape_coupert, TEST_LINKS["coupert"])
    # run(scrape_joinhoney, TEST_LINKS["joinhoney"])
    #run(scrape_dontpayfull, TEST_LINKS["dontpayfull"])
    # run(scrape_promocodes, TEST_LINKS["promocodes"])
    # run(scrape_couponchief, TEST_LINKS["couponchief"])
    # run(scrape_simplycodes, TEST_LINKS["simplycodes"])
    # run(scrape_tenereteam, TEST_LINKS["tenereteam"])
    # run(scrape_valuecom, TEST_LINKS["valuecom"])
    #run(scrape_refermate, TEST_LINKS["refermate"])
    run(scrape_discountreactor, TEST_LINKS["discountreactor"])
    # run(scrape_dealdrop, TEST_LINKS["dealdrop"])
    # run(scrape_greenpromocode, TEST_LINKS["greenpromocode"])
    #run(scrape_couponbind, TEST_LINKS["couponbind"])
    # run(scrape_deala, TEST_LINKS["deala"])
    # run(scrape_worthepenny, TEST_LINKS["worthepenny"])
    #run(scrape_promosreview, TEST_LINKS["promosreview"])
    # run(scrape_getscoupon, TEST_LINKS["getscoupon"])
    # run(scrape_dealam, TEST_LINKS["dealam"])
    # run(scrape_coupons, TEST_LINKS["coupons"])
    #run(scrape_goodshop, TEST_LINKS["goodshop"])
    #run(scrape_goodsearch, TEST_LINKS["goodsearch"])
    # run(scrape_couponcause, TEST_LINKS["couponcause"])
    # run(scrape_dazzdeals, TEST_LINKS["dazzdeals"])
    # run(scrape_joincheckmate, TEST_LINKS["joincheckmate"])
    # run(scrape_couponsgo, TEST_LINKS["couponsgo"])
    #run(scrape_couponpromo, TEST_LINKS["couponpromo"])
    # run(scrape_kotag, TEST_LINKS["kotag"])
    #run(scrape_retailmenot, TEST_LINKS["retailmenot"])
    # run(scrape_offers, TEST_LINKS["offers"])
    # run(scrape_trustdeals, TEST_LINKS["trustdeals"])
    # run(scrape_latestdeals, TEST_LINKS["latestdeals"])
    #run(scrape_troupon, TEST_LINKS["troupon"])
    # run(scrape_couponupto, TEST_LINKS["couponupto"])
    #run(scrape_capitaloneshopping, TEST_LINKS["capitaloneshopping"])
    #run(scrape_shipthedeal, TEST_LINKS["shipthedeal"])
    # run(scrape_lovecoupons, TEST_LINKS["lovecoupons"])
    #run(scrape_couponbox, TEST_LINKS["couponbox"])
    # run(scrape_promopro, TEST_LINKS["promopro"])

    # Others 
    # run(scrape_rebatesme, TEST_LINKS["rebatesme"])
    # run(scrape_savings, TEST_LINKS["savings"])
    #run(scrape_savingheist, TEST_LINKS["savingheist"])
    # run(scrape_coupongrouphy, TEST_LINKS["coupongrouphy"])
    # run(scrape_gocashback, TEST_LINKS["gocashback"])
    #run(scrape_reediscount, TEST_LINKS["reediscount"])
    #run(scrape_couponterra, TEST_LINKS["couponterra"])
    # run(scrape_couponskiss, TEST_LINKS["couponskiss"])
    #run(scrape_couponblender, TEST_LINKS["couponblender"])
    #run(scrape_xyzcodes, TEST_LINKS["xyzcodes"])
    # run(scrape_coponcodes, TEST_LINKS["coponcodes"])
    # run(scrape_redeempromos, TEST_LINKS["redeempromos"])
    #run(scrape_rebates, TEST_LINKS["rebates"])
