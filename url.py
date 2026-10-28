import copy
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape

from bs4 import BeautifulSoup

from main import TIMEOUT, client, get_response, rebates_codes

G = "\033[92m"
R = "\033[91m"
E = "\033[0m"

HANDLES_FILE = "handles.json"


def readJson():
    if os.path.exists(HANDLES_FILE):
        with open(HANDLES_FILE) as f:
            return json.load(f)
    return {}


# Site functions save after each check when run one by one. runAll() turns this off and saves the
# merged result itself, so parallel sites don't overwrite each other's file writes.
AUTO_SAVE = True


def writeJson(handles, force=False):
    if not (AUTO_SAVE or force):
        return
    with open(HANDLES_FILE, "w") as f:
        json.dump(handles, f, indent=2)


def slugs(query):
    """Build both 'cozy-earth' and 'cozyearth' style slugs from names like 'Cozy Earth'."""
    all_names = [query["handle"]] + query["query"]
    result = []
    for name in all_names:
        name = name.strip().lower()
        result += [name.replace(" ", "-"), name.replace(" ", "").replace("-", "")]
    return list(dict.fromkeys(result))


def getRendered(url):
    """Fetch with JS rendering, for pages that only show their content after scripts run."""
    response = client.get(url, params={"js_render": "true", "premium_proxy": "true", "wait": "5000"}, timeout=TIMEOUT)
    return response.text, response.status_code


def plainRetry(url, tries=8, pause=3):
    """For sites that block most requests at random (valuecom): plain requests only, a few seconds apart.
    JS rendering does not get through there any better."""
    for attempt in range(tries):
        response = client.get(url, timeout=TIMEOUT)
        # A cut-off page with only <head> counts as another blocked try
        cutOff = response.status_code == 200 and "<body" not in response.text.lower()
        if response.status_code != 422 and not cutOff:
            break
        if attempt < tries - 1:
            time.sleep(pause)
    return response.text, response.status_code


def renderedTwice(url):
    """JS-rendered fetch that asks once more when the coupons did not load in time (gocashback)."""
    response, status = getRendered(url)
    if status == 200 and 'class="codetext">Code:</span>' not in response:
        response, status = getRendered(url)
    return response, status


def fetchOnce(url):
    """One plain request, no retries: for sites where an unknown store always comes back as 422."""
    response = client.get(url, timeout=TIMEOUT)
    return response.text, response.status_code


def checkSite(handles, storeDomain, query, siteName, extDomain, makeUrl, isValid, fetch=get_response):
    """Re-check saved URLs, otherwise try each slug and keep the first URL whose page passes isValid."""
    extHandles = handles.copy()
    extHandles.setdefault(storeDomain, {}).setdefault(extDomain, [])

    # Saved URLs are re-checked every run so a page that dropped its codes doesn't stay "found".
    # A blocked or failed request (422, network error) keeps the URL, since that says nothing about the page.
    saved = extHandles[storeDomain][extDomain]
    dropped = set()
    if saved:
        kept = []
        for url in saved:
            result = checkUrl(siteName, url, isValid, recheck=True, fetch=fetch)
            if result is False:
                dropped.add(url)
            else:
                kept.append(url)
        extHandles[storeDomain][extDomain] = kept
        if kept:
            writeJson(extHandles)
            return extHandles
        print(f"{R}{siteName}: saved link no longer has codes, searching again{E}")

    # makeUrl may return one URL or a list of URLs to try for each slug
    candidates = [makeUrl(name) for name in slugs(query)]
    urls = list(dict.fromkeys(u for c in candidates for u in ([c] if isinstance(c, str) else c)))
    for url in urls:
        if url in dropped:
            continue
        if checkUrl(siteName, url, isValid, fetch=fetch):
            extHandles[storeDomain][extDomain].append(url)
            break

    writeJson(extHandles)
    return extHandles


def checkUrl(siteName, url, isValid, recheck=False, fetch=get_response):
    """True if the page has codes, False if it doesn't (or is gone), None if it couldn't be fetched."""
    label = "Re-checking" if recheck else "Checking"
    try:
        print(f"{label} {siteName}: {url}")
        response, status = fetch(url)
        # Some WordPress sites (savingheist, coupongrouphy) sometimes serve a JS challenge page first
        if status == 200 and "<title>One moment, please...</title>" in response:
            print(f"{R}Challenge page, retrying with JS rendering: {url}{E}")
            response, status = getRendered(url)
        # Some sites (e.g. valuecom) occasionally return a cut-off page with only <head>; ask once more
        # JSON answers (site APIs) have no <body>, so only HTML pages are checked
        isHtml = response.lstrip().startswith("<")
        if status == 200 and isHtml and "<body" not in response.lower():
            print(f"{R}Incomplete page, retrying: {url}{E}")
            response, status = fetch(url)
            if status == 200 and "<body" not in response.lower():
                print(f"{R}Incomplete page again, skipping: {url}{E}")
                return None
    except Exception as e:
        print(f"{R}Error fetching {siteName} ({url}): {e}{E}")
        return None

    # 422 = blocked, 429 = too many requests, 5xx = server trouble: none of these say the page is gone
    if status == 422 or status == 429 or status >= 500:
        print(f"{R}Could not fetch: {url} (Status: {status}){E}")
        return None
    if status != 200:
        print(f"{R}Not found: {url} (Status: {status}){E}")
        return False
    if not isValid(response):
        print(f"{R}{siteName}: page exists but has no codes for this store: {url}{E}")
        return False
    print(f"{G}Success! Valid Store Page with codes: {url}{E}")
    return True


def hotdeals(handles, storeDomain, query):
    # Each coupon card has data-merchant="cozyearth.com" ... data-code="GETCOZY50".
    # Deals have an empty data-code, and cards for other brands have a different merchant.
    codePattern = re.compile(rf'data-merchant="{re.escape(storeDomain)}"[^>]*data-code="[^"]+"')
    return checkSite(
        handles, storeDomain, query, "Hotdeals", "hotdeals.com",
        lambda name: f"https://www.hotdeals.com/coupons/{name}-promo-codes",
        lambda html: bool(codePattern.search(html)),
    )


def titleMentionsStore(html, query):
    # Check only the <title>: some sites echo the requested slug in links and data even for unknown stores
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    if not title:
        return False
    text = title.group(1).lower().replace(" ", "").replace("-", "").replace("'", "")
    return any(name.replace("-", "") in text for name in slugs(query))


def unsupported(handles, storeDomain, extDomain, reason):
    """Record an empty list for sites we can't get codes from, without spending requests."""
    extHandles = handles.copy()
    extHandles.setdefault(storeDomain, {}).setdefault(extDomain, [])
    print(f"{R}Skipping {extDomain}: {reason}{E}")
    writeJson(extHandles)
    return extHandles


def coupert(handles, storeDomain, query):
    # www.coupert.com/shop/{domain} loads without blocking (usa.coupert.com/coupons/... blocks most requests).
    # Unknown stores show the homepage. The store's own codes are in the Next.js data at
    # props.pageProps.storeInfo.coupons = [{"code": "NEWBIKE200OFF", ...}]; codes elsewhere on the page
    # belong to other featured stores.
    def hasCodes(html):
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not match:
            return False
        store = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("storeInfo") or {}
        return any(coupon.get("code") for coupon in store.get("coupons") or [])

    return checkSite(
        handles, storeDomain, query, "Coupert", "coupert.com",
        lambda name: f"https://www.coupert.com/shop/{storeDomain}",
        hasCodes,
    )


def rakuten(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "rakuten.com", "store pages show cash back only, no coupon codes")


def karmanow(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "karmanow.com", "no public store pages, coupons are only in the extension")


def discounttime(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "discounttime.com", "site is broken: invalid HTTPS certificate and an empty page over HTTP")


def slickdeals(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "slickdeals.net", "coupon pages were removed, every /coupons/ URL returns 410 Gone")


def capitalone(handles, storeDomain, query):
    # Each code offer has a button like aria-label="Copy code SHOP10 and open Kohl's in new tab"; deals say "No code required"
    return checkSite(
        handles, storeDomain, query, "Capital One Shopping", "capitaloneshopping.com",
        lambda name: f"https://capitaloneshopping.com/s/{storeDomain}/coupon",
        lambda html: 'aria-label="Copy code ' in html,
    )


def retailmenot(handles, storeDomain, query):
    # Unknown stores redirect to the homepage. Each offer link carries
    # x-data="outclickHandler({... 'offerType': 'COUPON', 'requestSlug': 'nike.com' ...})" and the
    # href has module=expired_offer for expired ones. The code itself is only revealed after a click.
    # Quotes are HTML-escaped (&#039;) in the raw page, so unescape before matching.
    codeOffer = re.compile(
        rf"module=(?!expired_offer)[^&\"]*[^>]*'offerType': 'COUPON', 'requestSlug': '{re.escape(storeDomain)}'"
    )
    return checkSite(
        handles, storeDomain, query, "RetailMeNot", "retailmenot.com",
        lambda name: f"https://www.retailmenot.com/view/{storeDomain}",
        lambda html: bool(codeOffer.search(unescape(html))),
    )


def honey(handles, storeDomain, query):
    # Store data is embedded as JSON: "publicCouponCount":3,"publicCoupons":[{"code":"UNLOCKCOZY",...}]
    def hasCodes(html):
        match = re.search(r'"publicCouponCount":(\d+)', html)
        return bool(match) and int(match.group(1)) > 0

    return checkSite(
        handles, storeDomain, query, "Honey", "joinhoney.com",
        lambda name: f"https://www.joinhoney.com/shop/{name}",
        hasCodes,
    )


def rebates(handles, storeDomain, query):
    # rebates.com (not rebatesme.com). Store pages use the domain; unknown or empty stores still return a page,
    # so a link counts only if the page holds a coupon code for this store (same parsing as main.scrape_rebates).
    return checkSite(
        handles, storeDomain, query, "Rebates", "rebates.com",
        lambda name: f"https://rebates.com/coupons/{storeDomain}",
        lambda html: bool(rebates_codes(html, storeDomain)),
    )


def rebatesme(handles, storeDomain, query):
    # Unknown stores return a generic stores page with status 200, so match urlNameInpage to our slug.
    # Store coupons are in `var couponListObj = [...]`; a code looks like "couponCodeList":[{"code":"EARLY",...}]
    def hasCodes(html):
        urlName = re.search(r'var urlNameInpage = "([^"]+)"', html)
        coupons = re.search(r"var couponListObj = (.*?);\s*\n", html)
        return (
            bool(urlName) and urlName.group(1) in slugs(query)
            and bool(coupons) and '"couponCodeList":[{"code":"' in coupons.group(1)
        )

    return checkSite(
        handles, storeDomain, query, "RebatesMe", "rebatesme.com",
        lambda name: f"https://www.rebatesme.com/en/stores/{name}",
        hasCodes,
    )


def normalize(text):
    return text.lower().replace(" ", "").replace("-", "").replace("'", "").replace("’", "")


def coupons(handles, storeDomain, query):
    # Vouchers are escaped JSON inside a script: \"code\":\"WELCOME10\",\"description\":...
    # Matching the following "description" key skips unrelated "code" keys such as icon names.
    codePattern = re.compile(r'\\"code\\":\\"[^\\"]+\\",\\"description')
    return checkSite(
        handles, storeDomain, query, "Coupons.com", "coupons.com",
        lambda name: f"https://www.coupons.com/coupon-codes/{name}",
        lambda html: bool(codePattern.search(html)),
    )


def dealcatcher(handles, storeDomain, query):
    # Offers live in the Nuxt payload (<script id="__NUXT_DATA__">), a flat JSON array where each
    # field holds the index of its value. Even big stores mostly have empty codes, so this is often empty.
    def hasCodes(html):
        match = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not match:
            return False
        data = json.loads(match.group(1))
        for item in data:
            if isinstance(item, dict) and "code" in item and "merchant_id" in item:
                code = data[item["code"]]
                if isinstance(code, str) and code.strip():
                    return True
        return False

    return checkSite(
        handles, storeDomain, query, "DealCatcher", "dealcatcher.com",
        lambda name: f"https://www.dealcatcher.com/coupons/{name}",
        hasCodes,
    )


def dontpayfull(handles, storeDomain, query):
    # Active offers sit in <div id="active-coupons">, expired ones in <div id="expired-coupons">.
    # Each offer is an <article class="card obox code"> or "card obox deal". Active codes stay hidden until
    # clicked (only expired ones show data-clipboard-text), so count active code cards instead.
    def hasCodes(html):
        active = BeautifulSoup(html, "html.parser").select_one("#active-coupons")
        return bool(active and active.select_one("article.code"))

    return checkSite(
        handles, storeDomain, query, "DontPayFull", "dontpayfull.com",
        lambda name: f"https://www.dontpayfull.com/at/{storeDomain}",
        hasCodes,
    )


def offers(handles, storeDomain, query):
    # Unknown stores redirect to the homepage, which also has code offers, so require a /stores/ canonical.
    # Code offers have "hasCode: true" in the link's click handler; the code is revealed only after a click.
    def hasCodes(html):
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        return bool(canonical) and "/stores/" in canonical.group(1) and "hasCode: true" in unescape(html)

    return checkSite(
        handles, storeDomain, query, "Offers.com", "offers.com",
        # Some stores carry a country suffix, e.g. /stores/macys-us/
        lambda name: [f"https://www.offers.com/stores/{name}/", f"https://www.offers.com/stores/{name}-us/"],
        hasCodes,
    )


def savings(handles, storeDomain, query):
    # Code offers are <div class="module-deal ... hasCode ..." data-provider-name="Kohl's">.
    # The page also shows code offers from other (sponsored) brands, so match the provider to the store.
    names = {normalize(name) for name in slugs(query)}

    def hasCodes(html):
        providers = re.findall(r'class="module-deal [^"]*\bhasCode\b[^"]*"[^>]*data-provider-name="([^"]+)"', html)
        return any(normalize(unescape(provider)) in names for provider in providers)

    return checkSite(
        handles, storeDomain, query, "Savings.com", "savings.com",
        lambda name: f"https://www.savings.com/coupons/{storeDomain}",
        hasCodes,
    )


def dealspotr(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "dealspotr.com", "every store page redirects to SimplyCodes, use simplycodes() instead")


def promocodes(handles, storeDomain, query):
    # Offers are embedded JSON: "couponCode":"GETCOZY"; deals have "couponCode":null
    return checkSite(
        handles, storeDomain, query, "PromoCodes.com", "promocodes.com",
        lambda name: f"https://www.promocodes.com/{name}-coupons",
        lambda html: bool(re.search(r'"couponCode":"[^"]+"', html)),
    )


def couponchief(handles, storeDomain, query):
    # Store coupons are <div id="coupon_7133304" data-coupon-id="7133304"> cards; a code card has a
    # <span class="coupon-code covered-coupon-code"> showing only the last character.
    # Sidebar cards for other stores have no data-coupon-id, so they are skipped.
    # Expired cards say "Expired over one year ago" and don't count.
    def hasCodes(html):
        soup = BeautifulSoup(html, "html.parser")
        return any(
            card.select_one(".covered-coupon-code") and "Expired" not in card.get_text()
            for card in soup.select("[data-coupon-id]")
        )

    return checkSite(
        handles, storeDomain, query, "CouponChief", "couponchief.com",
        lambda name: f"https://www.couponchief.com/{name}",
        hasCodes,
    )


def goodshop(handles, storeDomain, query):
    # JSON-LD lists each offer as {"@id":"https://www.goodshop.com/coupons/cozyearth.com#72866609", ...,
    # "discountCode":"CE-DISCOVERELLE"}. Most codes are masked ("••••••AN") but still mean a code exists.
    codePattern = re.compile(rf'coupons/{re.escape(storeDomain)}#\d+"[^{{}}]*"discountCode":"[^"]+"')
    return checkSite(
        handles, storeDomain, query, "Goodshop", "goodshop.com",
        lambda name: f"https://www.goodshop.com/coupons/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def simplycodes(handles, storeDomain, query):
    # Code buttons look like data-code="COZY15" ... data-merchant-url="https://cozyearth.com/".
    # Buttons for related stores use data-merchant-url="/store/other.com", so match the store's own URL.
    codePattern = re.compile(rf'data-code="[^"]+"[^>]*data-merchant-url="https?://(www\.)?{re.escape(storeDomain)}')
    return checkSite(
        handles, storeDomain, query, "SimplyCodes", "simplycodes.com",
        lambda name: f"https://simplycodes.com/store/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def tenereteam(handles, storeDomain, query):
    # Each store has its own subdomain. Offers are escaped JSON inside the Next.js payload:
    # \"coupon_code\":\"WELCOME10\"
    codePattern = re.compile(r'\\"coupon_code\\":\\"[^\\"]+\\"')
    return checkSite(
        handles, storeDomain, query, "TenereTeam", "tenereteam.com",
        lambda name: f"https://{name}.tenereteam.com/coupons",
        lambda html: bool(codePattern.search(html)),
    )


def couponcause(handles, storeDomain, query):
    # Unknown stores return status 200 with an "Oops Page Not Found!" page whose canonical is /404/.
    # Code offers link to data-redirect-url="/stores/cozy-earth/?_c=20099"; deals have no ?_c=.
    def hasCodes(html):
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        return (
            bool(canonical) and "/stores/" in canonical.group(1)
            and bool(re.search(r'data-redirect-url="/stores/[^"]+\?_c=\d+"', html))
        )

    return checkSite(
        handles, storeDomain, query, "CouponCause", "couponcause.com",
        lambda name: f"https://couponcause.com/stores/{name}/",
        hasCodes,
    )


def goodsearch(handles, storeDomain, query):
    # Same company and page layout as Goodshop: JSON-LD offers with "discountCode", mostly masked
    codePattern = re.compile(rf'coupons/{re.escape(storeDomain)}#\d+"[^{{}}]*"discountCode":"[^"]+"')
    return checkSite(
        handles, storeDomain, query, "Goodsearch", "goodsearch.com",
        lambda name: f"https://www.goodsearch.com/coupons/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def valuecom(handles, storeDomain, query):
    # Stores live on subdomains like cozyearth.valuecom.com (no hyphens); a few use store{name}.valuecom.com.
    # Offers are JSON-LD: {"@type":"Offer", ..., "couponCode":"SAMANTHA55", ...}
    # The site blocks most requests (422) at random, so it gets plain retries instead of get_response.
    return checkSite(
        handles, storeDomain, query, "Valuecom", "valuecom.com",
        lambda name: [
            f"https://{name.replace('-', '')}.valuecom.com/",
            f"https://store{name.replace('-', '')}.valuecom.com/",
        ],
        lambda html: bool(re.search(r'"couponCode":"[^"]+"', html)),
        fetch=plainRetry,
    )


def lovecoupons(handles, storeDomain, query):
    # Offers are <article class="Offer ..." data-type="2" ... data-brand-slug="/cozy-earth">.
    # data-type 2 is a coupon (code), 3 is a sale and 9 is shipping, so only type 2 counts.
    names = set(slugs(query))

    def hasCodes(html):
        offers = re.findall(r'<article class="Offer[^"]*"[^>]*data-type="2"[^>]*data-brand-slug="/([^"]+)"', html)
        return any(slug in names for slug in offers)

    return checkSite(
        handles, storeDomain, query, "Love Coupons", "lovecoupons.com",
        lambda name: f"https://www.lovecoupons.com/{name}",
        hasCodes,
    )


def knoji(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "knoji.com", "every store URL returns 410 Gone and the homepage redirects to SimplyCodes, use simplycodes() instead")


def swagbucks(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "swagbucks.com", "store pages show cash back only, no coupon codes")


def joinsmarty(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "joinsmarty.com", "no public store pages, store lists only show after signing up")


def refermate(handles, storeDomain, query):
    # Codes are escaped JSON in the Next.js payload: \"couponCode\":\"HONEY10\"; deals say "No Code Needed"
    codePattern = re.compile(r'\\"couponCode\\":\\"(?!No Code Needed)[^\\"]+\\"')
    return checkSite(
        handles, storeDomain, query, "Refermate", "refermate.com",
        lambda name: f"https://refermate.com/store/{name}/promo-codes",
        lambda html: bool(codePattern.search(html)),
    )


def discountreactor(handles, storeDomain, query):
    # Store pages use the domain (/coupons/cozyearth.com) or a plain name (/coupons/lindseyleighjewelry).
    # Each code offer links with data-shop-alias="cozyearth.com" and wraps a masked data-code="••••••OZY".
    aliases = {storeDomain} | set(slugs(query))

    def hasCodes(html):
        offers = re.findall(
            r'data-shop-alias="([^"]+)"[^>]*>\s*<div class="offer-list-item-button_hidden-code"[^>]*data-code="[^"]+"', html
        )
        return any(alias in aliases for alias in offers)

    return checkSite(
        handles, storeDomain, query, "DiscountReactor", "discountreactor.com",
        lambda name: [
            f"https://www.discountreactor.com/coupons/{storeDomain}",
            f"https://www.discountreactor.com/coupons/{name.replace('-', '')}",
        ],
        hasCodes,
    )


def couponbox(handles, storeDomain, query):
    # The store's own offers sit between "activeVouchersGrouped" and "expiredVouchers" in the Next.js payload;
    # code offers there have \"codeType\":\"copy_paste\". Offers further down belong to other (cross-sell) stores.
    def hasCodes(html):
        start = html.find("activeVouchersGrouped")
        end = html.find("expiredVouchers", start)
        return start >= 0 and end > start and "copy_paste" in html[start:end]

    return checkSite(
        handles, storeDomain, query, "Couponbox", "couponbox.com",
        lambda name: f"https://www.couponbox.com/coupons/{name}",
        hasCodes,
    )


def dealdrop(handles, storeDomain, query):
    # Each code is an <article id="code-<uuid>"> with a "Show code" button that shows the last characters
    codePattern = re.compile(r'<article id="code-[^"]+"(?:(?!</article>).)*Show code', re.S)
    return checkSite(
        handles, storeDomain, query, "DealDrop", "dealdrop.com",
        lambda name: f"https://www.dealdrop.com/{name}",
        lambda html: bool(codePattern.search(html)),
    )


def dazzdeals(handles, storeDomain, query):
    # Code offers have a hidden <a class="couponBtn hidden showCouponCode showcode">PNIK</a> holding the code's tail
    return checkSite(
        handles, storeDomain, query, "DazzDeals", "dazzdeals.com",
        lambda name: f"https://www.dazzdeals.com/store/{name}/",
        lambda html: 'class="couponBtn hidden showCouponCode' in html,
    )


def greenpromocode(handles, storeDomain, query):
    # The site is greenpromocode.com (greenpromocodes.com does not resolve).
    # Code offers have view-href="/coupons/cozy-earth/?view=11609534" followed by a "Show Code" button.
    names = set(slugs(query))

    def hasCodes(html):
        offers = re.findall(r'view-href="/coupons/([^/]+)/\?view=\d+"[^>]*><div class="show_code">', html)
        return any(slug in names for slug in offers)

    return checkSite(
        handles, storeDomain, query, "GreenPromoCode", "greenpromocode.com",
        lambda name: f"https://www.greenpromocode.com/coupons/{name}/",
        hasCodes,
    )


def couponbind(handles, storeDomain, query):
    # Each code offer holds the full code in <span class="hiddenCode">CE-DISCOVERELLE</span>
    return checkSite(
        handles, storeDomain, query, "CouponBind", "couponbind.com",
        lambda name: f"https://www.couponbind.com/coupons/{storeDomain}",
        lambda html: bool(re.search(r'<span class="hiddenCode">[^<]+</span>', html)),
    )


def deala(handles, storeDomain, query):
    # The store's own offers are <app-card-coupon-without-store> cards; code offers have a "Get code" button
    codePattern = re.compile(r"<app-card-coupon-without-store(?:(?!</app-card-coupon-without-store>).)*btn-get-code", re.S)
    return checkSite(
        handles, storeDomain, query, "DealA", "deala.com",
        lambda name: f"https://deala.com/{name}",
        lambda html: bool(codePattern.search(html)),
    )


def promopro(handles, storeDomain, query):
    # Unknown stores return 410 Gone. Code offers are <article class="offer_card ... detail_filter_code">.
    return checkSite(
        handles, storeDomain, query, "PromoPro", "promopro.com",
        lambda name: f"https://www.promopro.com/coupon-codes/{name}",
        lambda html: bool(re.search(r'<article class="offer_card[^"]*\bdetail_filter_code\b', html)),
    )


def troupon(handles, storeDomain, query):
    # Each store has its own subdomain. Codes look like data-coupon-code="HOMELIFE" data-coupon-url="https://cozyearth.com"
    codePattern = re.compile(rf'data-coupon-code="[^"]+" data-coupon-url="https?://(www\.)?{re.escape(storeDomain)}')
    return checkSite(
        handles, storeDomain, query, "Troupon", "troupon.com",
        lambda name: f"https://{name}.troupon.com/",
        lambda html: bool(codePattern.search(html)),
    )


def worthepenny(handles, storeDomain, query):
    # Each store has its own subdomain without hyphens (cozyearth.worthepenny.com).
    # Code cards look like data-code="CE-CADE" ... data-coupon_type="code" data-name="Cozy Earth"; deals have data-code="".
    names = {normalize(name) for name in slugs(query)}

    def hasCodes(html):
        offers = re.findall(r'data-code="[^"]+"[^>]*data-coupon_type="code"[^>]*data-name="([^"]+)"', html)
        return any(normalize(unescape(name)) in names for name in offers)

    return checkSite(
        handles, storeDomain, query, "WorthEPenny", "worthepenny.com",
        lambda name: f"https://{name.replace('-', '')}.worthepenny.com/coupon/",
        hasCodes,
    )


def savingheist(handles, storeDomain, query):
    # Store pages are /store/{slug}/ or /store/{slug}-coupons/. Code buttons look like
    # <button ... data-coupon-id="205324" data-code="BULKRATE">Get Code</button>
    return checkSite(
        handles, storeDomain, query, "SavingHeist", "savingheist.com",
        lambda name: [f"https://savingheist.com/store/{name}/", f"https://savingheist.com/store/{name}-coupons/"],
        lambda html: bool(re.search(r'data-coupon-id="\d+" data-code="[^"]+"', html)),
    )


def coupongrouphy(handles, storeDomain, query):
    # WordPress (Rehub theme) store pages: /dealstore/{slug}/, and many slugs end in "thuy" (dreamlandbabycothuy).
    # Code offers are <span class="... masked_coupon" data-clipboard-text="JULIANASPICOLUK0" ...>.
    return checkSite(
        handles, storeDomain, query, "CouponGrouphy", "coupongrouphy.com",
        lambda name: [f"https://coupongrouphy.com/dealstore/{name}/", f"https://coupongrouphy.com/dealstore/{name}thuy/"],
        lambda html: bool(re.search(r'masked_coupon[^"]*" data-clipboard-text="[^"]+"', html)),
    )


def joincheckmate(handles, storeDomain, query):
    # Store pages use the domain. Codes are masked like <span class="label-regular tracking-widest ...">INS#############</span>
    return checkSite(
        handles, storeDomain, query, "Checkmate", "joincheckmate.com",
        lambda name: f"https://joincheckmate.com/merchants/{storeDomain}",
        lambda html: bool(re.search(r'<span class="label-regular tracking-widest[^"]*">[^<#]*#+</span>', html)),
    )


def faircoupons(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "faircoupons.com", "site is down: the server does not respond")


def gocashback(handles, storeDomain, query):
    # Store pages are /stores/{slug} without hyphens. Coupons only appear after JS rendering, where each code
    # sits under <span class="codetext">Code:</span>.
    return checkSite(
        handles, storeDomain, query, "GoCashBack", "gocashback.com",
        lambda name: f"https://www.gocashback.com/stores/{name.replace('-', '')}",
        lambda html: 'class="codetext">Code:</span>' in html,
        fetch=renderedTwice,
    )


def trustdeals(handles, storeDomain, query):
    # Store pages use the domain. Code offers are <article class="coupon nw|common ..."> with <span class="code">.
    # "coupon common" articles ("most common codewords at similar shops", e.g. SAVE10) count as codes,
    # matching the live project; only "coupon expired" ones are skipped.
    codePattern = re.compile(r'<article[^>]*class="coupon (?!expired)[^"]*"(?:(?!</article>).)*<span class="code">[^<]+</span>', re.S)
    return checkSite(
        handles, storeDomain, query, "TrustDeals", "trustdeals.com",
        lambda name: f"https://www.trustdeals.com/view/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def couponupto(handles, storeDomain, query):
    # Unknown stores redirect to the homepage (which has its own codes), so require a /coupons/ canonical.
    # Code offers show the full code as <p id="code-64ba23ddc05cf77fa779d770">SPECIALDEAL2023</p> <span>Show Code</span>
    def hasCodes(html):
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        return (
            bool(canonical) and "/coupons/" in canonical.group(1)
            and bool(re.search(r'<p id="code-[0-9a-f]+">[^<]+</p>\s*<span>Show Code', html))
        )

    return checkSite(
        handles, storeDomain, query, "Couponupto", "couponupto.com",
        lambda name: f"https://www.couponupto.com/coupons/{name}",
        hasCodes,
    )


def promosreview(handles, storeDomain, query):
    # Store pages use the name without spaces in its original case: /store/CozyEarth-coupon-codes.
    # Unknown stores get a small generic page. Codes look like data-code="PJ-BOGO" data-url="https://cozyearth.com".
    names = list(dict.fromkeys(
        "".join(word[:1].upper() + word[1:] for word in name.split())
        for name in [query["handle"].replace("-", " ")] + query["query"]
    ))
    codePattern = re.compile(rf'data-code="[^"]+" data-url="https?://(www\.)?{re.escape(storeDomain)}')
    return checkSite(
        handles, storeDomain, query, "PromosReview", "promosreview.com",
        lambda name: [f"https://www.promosreview.com/store/{n}-coupon-codes" for n in names],
        lambda html: bool(codePattern.search(html)),
    )


def couponsgo(handles, storeDomain, query):
    # The site is couponsgo.org (couponsgo.com and .net are parked). Store pages are /{slug} or /{domain};
    # many stores have no page of their own but still show up in /search?query={name}.
    # Offers are <div class="coupon-item ..." data-domain="shopbop.com">; code offers show the code's tail
    # in <a class="get-deal-btn coupon_button"><p>CKUP</p>, deals say "Get Deal".
    def hasCodes(html):
        soup = BeautifulSoup(html, "html.parser")
        for item in soup.select(f'.coupon-item[data-domain="{storeDomain}"]'):
            button = item.select_one("a.coupon_button p")
            if button and button.get_text(strip=True) and button.get_text(strip=True) != "Get Deal":
                return True
        return False

    return checkSite(
        handles, storeDomain, query, "Couponsgo", "couponsgo.org",
        lambda name: [
            f"https://www.couponsgo.org/{name}",
            f"https://www.couponsgo.org/{storeDomain}",
            f"https://www.couponsgo.org/search?query={name.replace('-', '+')}",
        ],
        hasCodes,
    )


def getscoupon(handles, storeDomain, query):
    # Same layout as PromosReview. Unknown stores get a small generic page.
    # Codes look like data-code="PJ-BOGO" data-url="https://cozyearth.com".
    codePattern = re.compile(rf'data-code="[^"]+" data-url="https?://(www\.)?{re.escape(storeDomain)}')
    return checkSite(
        handles, storeDomain, query, "GetsCoupon", "getscoupon.com",
        lambda name: f"https://www.getscoupon.com/store/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def rediscount(handles, storeDomain, query):
    # rediscount.com is a parked domain; the real site is reediscount.com (double e).
    # Store pages are /{slug}/ or /{domain}/. Code buttons look like <a rel="nofollow" data-code="NEW10" data-store_info="...">
    return checkSite(
        handles, storeDomain, query, "ReeDiscount", "reediscount.com",
        lambda name: [f"https://www.reediscount.com/{name}/", f"https://www.reediscount.com/{storeDomain}/"],
        lambda html: bool(re.search(r'<a rel="nofollow" data-code="[^"]+" data-store_info=', html)),
    )


def couponterra(handles, storeDomain, query):
    # Mostly prop trading firms, but some retail stores too (cabelas.com). Store pages use the domain.
    # Unknown stores always come back as 422, so fetch once instead of going through the retries.
    # Codes are in full: <div class="get-code getCodeBtn" data-coupon-id="1235" data-code="VORTEX15" ...>
    return checkSite(
        handles, storeDomain, query, "CouponTerra", "couponterra.com",
        lambda name: f"https://www.couponterra.com/coupons/{storeDomain}",
        lambda html: bool(re.search(r'data-coupon-id="\d+"\s+data-code="[^"]+"', html)),
        fetch=fetchOnce,
    )


def couponkiss(handles, storeDomain, query):
    # The site is couponskiss.com (couponkiss.com is a domain for sale). Each store has its own subdomain,
    # e.g. gform.couponskiss.com; unknown stores show the homepage (canonical www.couponskiss.com).
    # Codes are in data-clipboard-text="WELCOMEBACK"; deals say "Click to Redeem".
    def hasCodes(html):
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        if not canonical or "www.couponskiss.com" in canonical.group(1):
            return False
        codes = re.findall(r'data-clipboard-text="([^"]+)"', html)
        return any(code != "Click to Redeem" for code in codes)

    return checkSite(
        handles, storeDomain, query, "CouponsKiss", "couponskiss.com",
        lambda name: f"https://{name}.couponskiss.com/",
        hasCodes,
    )


def shipthedeal(handles, storeDomain, query):
    # The page header states the count: <span class="... font-bold">3</span> Coupon Code(s)
    def hasCodes(html):
        match = re.search(r'font-bold">(\d+)</span>\s*Coupon Code\(s\)', html)
        return bool(match) and int(match.group(1)) > 0

    return checkSite(
        handles, storeDomain, query, "ShipTheDeal", "shipthedeal.com",
        lambda name: f"https://shipthedeal.com/store/{name}-coupon",
        hasCodes,
    )


def couponpromo(handles, storeDomain, query):
    # The site is couponpromo.net (couponpromo.com is for sale). Store page URLs are irregular
    # (/target/target-promo-code.htm), so the site search is used: /search?query={name}.
    # Results carry the full code and the store: data-domain="nobullproject.com" ... data-clipboard-text="GOVX20"
    codePattern = re.compile(rf'data-domain="{re.escape(storeDomain)}"[\s\S]*?data-clipboard-text="[^"]+"')
    names = list(dict.fromkeys(name.replace("-", " ") for name in slugs(query)))
    return checkSite(
        handles, storeDomain, query, "CouponPromo", "couponpromo.net",
        lambda name: [f"https://couponpromo.net/search?query={n.replace(' ', '+')}" for n in names],
        lambda html: bool(codePattern.search(html)),
    )


def couponblender(handles, storeDomain, query):
    # Unknown stores show the homepage (canonical "https://www.couponblender.com//"), so require a store canonical.
    # Each offer is labelled <div class="type">COUPON CODE</div> or <div class="type">DEAL</div>.
    def hasCodes(html):
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        return (
            bool(canonical) and not canonical.group(1).rstrip("/").endswith("couponblender.com")
            and '<div class="type">COUPON CODE</div>' in html
        )

    return checkSite(
        handles, storeDomain, query, "Coupon Blender", "couponblender.com",
        lambda name: f"https://www.couponblender.com/{name}/",
        hasCodes,
    )


def xyzcodes(handles, storeDomain, query):
    # WordPress "WP Coupons and Deals" plugin; pages often need the JS-challenge retry.
    # Code offers are <a data-type="code" data-coupon-id="3474" ...>; deals use data-type="deal".
    return checkSite(
        handles, storeDomain, query, "XYZCodes", "xyzcodes.com",
        lambda name: [f"https://www.xyzcodes.com/store/{name}/", f"https://www.xyzcodes.com/store/{storeDomain.replace('.', '-')}/"],
        lambda html: bool(re.search(r'<a data-type="code" data-coupon-id="\d+"', html)),
    )


def kotag(handles, storeDomain, query):
    # The site is kotag.co (kotag.com is a domain for sale): influencer promo codes.
    # Unknown stores show the brand list. The store's codes are in the Next.js data at
    # props.pageProps.coupons = [{"code": "CHAMPAGNE45", "expired": false, ...}]
    def hasCodes(html):
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not match:
            return False
        coupons = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("coupons") or []
        return any(coupon.get("code") and not coupon.get("expired") for coupon in coupons if isinstance(coupon, dict))

    return checkSite(
        handles, storeDomain, query, "Kotag", "kotag.co",
        lambda name: f"https://kotag.co/us/marques/codes-promo-{name}/",
        hasCodes,
    )


def zensaving(handles, storeDomain, query):
    # The site is zensaving.com (zensavings.com is parked). Store pages are /store/{slug}, but every
    # store checked (16) lists only deals, no codes.
    return unsupported(handles, storeDomain, "zensaving.com", "store pages list only deals, no coupon codes")


def coponcodes(handles, storeDomain, query):
    # Store slugs vary (aosom-uk, bioptimizers-com, avidlove-promo-codes), so a few forms are tried.
    # Unknown stores take about 30s and return 422, so each URL is fetched once without retries.
    # Each code is hidden in <span id="code-2505" hidden>BFCM2025</span>
    return checkSite(
        handles, storeDomain, query, "Coponcodes", "coponcodes.com",
        lambda name: [
            f"https://coponcodes.com/store/{name}/",
            f"https://coponcodes.com/store/{storeDomain.replace('.', '-')}/",
            f"https://coponcodes.com/store/{name}-promo-codes/",
            f"https://coponcodes.com/store/{name}-coupon-code/",
        ],
        lambda html: bool(re.search(r'<span id="code-\d+" hidden>[^<]+</span>', html)),
        fetch=fetchOnce,
    )


def dealam(handles, storeDomain, query):
    # Code offers are <div class="dx_code_coupons">; cards with "new-class-ads" are ads for other offers.
    codePattern = re.compile(r'class="dx_code_coupons(?![^"]*new-class-ads)[^"]*"')
    return checkSite(
        handles, storeDomain, query, "DealAM", "dealam.com",
        lambda name: f"https://www.dealam.com/coupons/{name}/",
        lambda html: bool(codePattern.search(html)),
    )


def latestdeals(handles, storeDomain, query):
    # UK site whose store pages sit behind an AWS WAF challenge (JS rendering, often incomplete). The page loads its
    # offers from the site's own JSON API, which answers plain requests, so the link is checked through the API:
    # /api/posts?merchantSlugDomain=<domain>&type=voucher&type=offer -> {"entities": {...: {"code": "SPARKS30", ...}}}
    def fetchApi(pageUrl):
        domain = pageUrl.rstrip("/").split("/")[-1]
        return get_response(
            f"https://www.latestdeals.co.uk/api/posts?merchantSlugDomain={domain}&type=voucher&type=offer&noExcerpts=true"
        )

    def hasCodes(body):
        try:
            posts = (json.loads(body).get("entities") or {}).values()
        except (ValueError, AttributeError):
            return False
        return any(isinstance(post, dict) and post.get("code") for post in posts)

    return checkSite(
        handles, storeDomain, query, "LatestDeals", "latestdeals.co.uk",
        lambda name: f"https://www.latestdeals.co.uk/retailers/{storeDomain}",
        hasCodes,
        fetch=fetchApi,
    )


def runAll(handles, storeDomain, query, sites, workers=10):
    """Run site functions in parallel. Each works on its own copy; changed entries are merged and saved here."""
    global AUTO_SAVE
    AUTO_SAVE = False
    original = copy.deepcopy(handles.get(storeDomain, {}))
    merged = copy.deepcopy(handles)
    merged.setdefault(storeDomain, {})
    results, timings = {}, {}

    def job(site):
        siteStart = time.time()
        result = site(copy.deepcopy(handles), storeDomain, query).get(storeDomain, {})
        return site.__name__, result, time.time() - siteStart

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(job, site) for site in sites]
            for future in as_completed(futures):
                try:
                    name, result, seconds = future.result()
                except Exception as e:
                    print(f"{R}Site check failed: {e}{E}")
                    continue
                timings[name] = seconds
                for extDomain, urls in result.items():
                    if extDomain not in original or urls != original[extDomain]:
                        results[extDomain] = urls
                merged[storeDomain].update(results)
                writeJson(merged, force=True)
    finally:
        AUTO_SAVE = True

    slowest = sorted(timings.items(), key=lambda item: item[1], reverse=True)[:5]
    print("\nSlowest sites: " + ", ".join(f"{name} {seconds:.0f}s" for name, seconds in slowest))
    return merged


def printSummary(handles, storeDomain):
    """Print sites with links first, then empty ones, then the totals."""
    sites = handles.get(storeDomain, {})
    found = {site: urls for site, urls in sites.items() if urls}
    empty = [site for site, urls in sites.items() if not urls]

    print(f"\nSummary for {storeDomain}")
    print(f"{G}Found ({len(found)}):{E}")
    for site, urls in found.items():
        for url in urls:
            print(f"  {site:<24} {url}")
    print(f"{R}Not found ({len(empty)}):{E}")
    for site in empty:
        print(f"  {site}")
    print(f"Total sites checked: {len(sites)} | Found: {len(found)} | Not found: {len(empty)}")


if __name__ == "__main__":
    handles = readJson()

    # storeDomain = "cozyearth.com"
    # storeQuery = {"handle": "cozy-earth", "query": ["Cozy Earth"]}

    # storeDomain = "tommyjohn.com"
    # storeQuery = {"handle": "tommy-john", "query": ["Tommy John"]}

    storeDomain = "softminkyblankets.com"
    storeQuery = {"handle": "softminkyblankets", "query": ["softminkyblankets"]}

    # storeDomain = "nobullproject.com"
    # storeQuery = {"handle": "nobull", "query": ["NOBULL", "NOBULL Project"]}

    # storeDomain = "imathyna.com"
    # storeQuery = {"handle": "imathyna", "query": ["Im Athyna", "I M Athyna"]}

    # Comment out a site here to skip it
    SITES = [
        coupert,
        discounttime,
        karmanow,
        rakuten,
        hotdeals,
        slickdeals,
        capitalone,
        retailmenot,
        honey,
        rebates,
        rebatesme,
        coupons,
        dealcatcher,
        dontpayfull,
        offers,
        savings,
        dealspotr,
        promocodes,
        couponchief,
        goodshop,
        simplycodes,
        tenereteam,
        couponcause,
        goodsearch,
        valuecom,
        lovecoupons,
        knoji,
        swagbucks,
        joinsmarty,
        refermate,
        discountreactor,
        couponbox,
        dealdrop,
        dazzdeals,
        greenpromocode,
        couponbind,
        deala,
        promopro,
        troupon,
        worthepenny,
        savingheist,
        coupongrouphy,
        joincheckmate,
        faircoupons,
        gocashback,
        trustdeals,
        couponupto,
        promosreview,
        couponsgo,
        getscoupon,
        rediscount,
        couponterra,
        couponkiss,
        shipthedeal,
        couponpromo,
        couponblender,
        xyzcodes,
        kotag,
        zensaving,
        coponcodes,
        dealam,
        latestdeals,
    ]

    start = time.time()
    handles = runAll(handles, storeDomain, storeQuery, SITES)

    printSummary(handles, storeDomain)
    elapsed = time.time() - start
    print(f"Time taken: {int(elapsed // 60)}m {elapsed % 60:.1f}s")
