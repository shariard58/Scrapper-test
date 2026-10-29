import json
import re
from collections import Counter
from html import unescape
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from app.config.coupon_scraper import fetch_top_coupert
from app.lib.helpers import (fetch_once, get_rendered, get_response,
                             getZenrowsTwoResponse, plain_retry,
                             postZenrowsResponse, rendered_twice)
from app.lib.logger import get_logger

# set log level
logger = get_logger(__name__)


def isExists(extDomain, storeDomain, handles):
    if storeDomain in handles:
        if (
            extDomain in handles[storeDomain]
            and len(handles[storeDomain][extDomain]) > 0
        ):
            return True
    return False


def slugs(query):
    """Build both 'cozy-earth' and 'cozyearth' style slugs from names like 'Cozy Earth'."""
    all_names = [query["handle"]] + query["query"]
    result = []
    for name in all_names:
        name = name.strip().lower()
        result += [name.replace(" ", "-"), name.replace(" ", "").replace("-", "")]
    return list(dict.fromkeys(result))


def normalize(text):
    return text.lower().replace(" ", "").replace("-", "").replace("'", "").replace("’", "")


def title_mentions_store(html, query):
    # Check only the <title>: some sites echo the requested slug in links and data even for unknown stores
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    if not title:
        return False
    text = title.group(1).lower().replace(" ", "").replace("-", "").replace("'", "")
    return any(name.replace("-", "") in text for name in slugs(query))


def check_url(site_name, url, is_valid, recheck=False, fetch=get_response):
    """True if the page has codes, False if it doesn't (or is gone), None if it couldn't be fetched."""
    label = "Re-checking" if recheck else "Checking"
    try:
        logger.info(f"{label} {site_name}: {url}")
        response, status = fetch(url)
        # Some WordPress sites (savingheist, coupongrouphy) sometimes serve a JS challenge page first
        if status == 200 and "<title>One moment, please...</title>" in response:
            logger.warning(f"Challenge page, retrying with JS rendering: {url}")
            response, status = get_rendered(url)
        # Some sites (e.g. valuecom) occasionally return a cut-off page with only <head>; ask once more
        # JSON answers (site APIs) have no <body>, so only HTML pages are checked
        is_html = response.lstrip().startswith("<")
        if status == 200 and is_html and "<body" not in response.lower():
            logger.warning(f"Incomplete page, retrying: {url}")
            response, status = fetch(url)
            if status == 200 and "<body" not in response.lower():
                logger.warning(f"Incomplete page again, skipping: {url}")
                return None
    except Exception as e:
        logger.error(f"Error fetching {site_name} ({url}): {e}")
        return None

    # 422 = blocked, 429 = too many requests, 5xx = server trouble: none of these say the page is gone
    if status == 422 or status == 429 or status >= 500:
        logger.warning(f"Could not fetch: {url} (Status: {status})")
        return None
    if status != 200:
        logger.info(f"Not found: {url} (Status: {status})")
        return False
    # A page whose layout changed (e.g. broken JSON) must not take down the other sites on the same page
    try:
        has_codes = is_valid(response)
    except Exception as e:
        logger.error(f"Error checking {site_name} ({url}): {e}")
        return None
    if not has_codes:
        logger.info(f"{site_name}: page exists but has no codes for this store: {url}")
        return False
    logger.info(f"Valid store page with codes: {url}")
    return True


def check_site(handles, storeDomain, query, site_name, extDomain, make_url, is_valid, fetch=get_response):
    """Re-check saved URLs, otherwise try each slug and keep the first URL whose page passes is_valid."""
    extHandles = handles.copy()
    extHandles.setdefault(storeDomain, {}).setdefault(extDomain, [])

    # Saved URLs are re-checked so a page that dropped its codes doesn't stay "found".
    # A blocked or failed request (422, network error) keeps the URL, since that says nothing about the page.
    saved = extHandles[storeDomain][extDomain]
    dropped = set()
    if saved:
        kept = []
        for url in saved:
            result = check_url(site_name, url, is_valid, recheck=True, fetch=fetch)
            if result is False:
                dropped.add(url)
            else:
                kept.append(url)
        extHandles[storeDomain][extDomain] = kept
        if kept:
            return extHandles
        logger.info(f"{site_name}: saved link no longer has codes, searching again")

    # make_url may return one URL or a list of URLs to try for each slug
    candidates = [make_url(name) for name in slugs(query)]
    urls = list(dict.fromkeys(u for c in candidates for u in ([c] if isinstance(c, str) else c)))
    for url in urls:
        if url in dropped:
            continue
        if check_url(site_name, url, is_valid, fetch=fetch):
            extHandles[storeDomain][extDomain].append(url)
            break

    return extHandles


def unsupported(handles, storeDomain, extDomain, reason):
    """Return an empty list for sites we can't get codes from, without spending requests."""
    extHandles = handles.copy()
    extHandles.setdefault(storeDomain, {}).setdefault(extDomain, [])
    logger.info(f"Skipping {extDomain}: {reason}")
    return extHandles


def coupert(handles, storeDomain, query):
    # www.coupert.com/shop/{domain} loads without blocking. Unknown stores show the homepage.
    # The store's own codes are in the Next.js data at props.pageProps.storeInfo.coupons;
    # codes elsewhere on the page belong to other featured stores.
    # Old /store/{domain} links already saved in the DB still work with fetch_coupons_from_coupert.
    # Some stores have codes only on top.coupert.com/coupons/{name}-promo-code (fuegodance), which shows them in full
    # as <span class="hiddenCode">WELCOME15OFF</span>; unknown stores there return 404. It blocks many requests at
    # random, so it is tried after www and gets extra retries.
    def has_codes(html):
        if 'class="hiddenCode"' in html:
            return bool(re.search(r'class="hiddenCode"[^>]*>[^<]+<', html))
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not match:
            return False
        store = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("storeInfo") or {}
        return any(coupon.get("code") for coupon in store.get("coupons") or [])

    def fetch(url):
        return fetch_top_coupert(url) if "top.coupert.com" in url else get_response(url)

    return check_site(
        handles, storeDomain, query, "Coupert", "coupert",
        lambda name: [
            f"https://www.coupert.com/shop/{storeDomain}",
            f"https://top.coupert.com/coupons/{name.replace('-', '')}-promo-code",
        ],
        has_codes,
        fetch=fetch,
    )


def discounttime(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "discountime", "discountime.com no longer exists (no DNS record)")


def karmanow(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "karma", "no public store pages, coupons are only in the extension")


def rakuten(handles, storeDomain, query):
    # The old search used rakuten.ca but built rakuten.com links, which were often 404,
    # and Rakuten store pages show cash back only, with no coupon codes to fetch.
    return unsupported(handles, storeDomain, "rakuten", "store pages show cash back only, no coupon codes")


def hotdeals_search(storeDomain):
    """Return the store page URL from hotdeals' search API, or None."""
    url = f"https://beta-api.hotdeals.com/search/get_term_list?keyword={storeDomain}&page_type=home&country_code=US"
    try:
        response, status = get_response(url)
        if status != 200:
            return None
        for item in json.loads(response).get("data") or []:
            if item.get("DomainUrl") == storeDomain and item.get("reUrl"):
                return item["reUrl"]
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
    return None


def hotdeals(handles, storeDomain, query):
    # The search API gives the exact page URL cheaply; slug URLs are only a fallback.
    # Each coupon card has data-merchant="cozyearth.com" ... data-code="GETCOZY50".
    # Deals have an empty data-code, and cards for other brands have a different merchant.
    code_pattern = re.compile(rf'data-merchant="{re.escape(storeDomain)}"[^>]*data-code="[^"]+"')
    search_url = hotdeals_search(storeDomain)
    return check_site(
        handles, storeDomain, query, "Hotdeals", "hotdeals",
        lambda name: [u for u in (search_url, f"https://www.hotdeals.com/coupons/{name}-promo-codes") if u],
        lambda html: bool(code_pattern.search(html)),
    )


def slickdeals(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "slickdeals", "coupon pages were removed, every /coupons/ URL returns 410 Gone")


def capitalone(handles, storeDomain, query):
    # Each code offer has a button like aria-label="Copy code SHOP10 and open Kohl's in new tab"; deals say "No code required"
    return check_site(
        handles, storeDomain, query, "Capital One Shopping", "capitalone",
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
    return check_site(
        handles, storeDomain, query, "RetailMeNot", "retailmenot",
        lambda name: f"https://www.retailmenot.com/view/{storeDomain}",
        lambda html: bool(codeOffer.search(unescape(html))),
    )


def honey(handles, storeDomain, query):
    # Store data is embedded as JSON: "publicCouponCount":3,"publicCoupons":[{"code":"UNLOCKCOZY",...}]
    def hasCodes(html):
        match = re.search(r'"publicCouponCount":(\d+)', html)
        return bool(match) and int(match.group(1)) > 0

    return check_site(
        handles, storeDomain, query, "Honey", "honey",
        lambda name: f"https://www.joinhoney.com/shop/{name}",
        hasCodes,
    )


def rebates_codes(html, domain):
    """Codes of one store from a rebates.com page (Next.js payload)."""
    # Offers look like {"title":...,"store":"macys.com",...,"code":"FRIEND","type":"coupon",...}; offers of other
    # stores are mixed in. Links inside an offer contain braces ("sid={rebates-shopperid}"), so each code is matched
    # to the nearest "store" before it, and the two keys come in either order.
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    payload = "".join(json.loads(chunk) for chunk in chunks)
    codes = []
    for match in re.finditer(r'"code":"([^"]*)","type":"coupon"|"type":"coupon","code":"([^"]*)"', payload):
        stores = re.findall(r'"store":"([^"]+)"', payload[max(0, match.start() - 3000):match.start()])
        if stores and stores[-1] == domain:
            codes.append((match.group(1) or match.group(2) or "").strip())
    return list(dict.fromkeys(code for code in codes if code and " " not in code))


def rebates(handles, storeDomain, query):
    # Store pages are /coupons/{domain}. Every store gets a page, even with no offers, so the link counts only if
    # the page holds a coupon code for this store (the old search API returned stores without codes too).
    return check_site(
        handles, storeDomain, query, "Rebates", "rebates",
        lambda name: f"https://rebates.com/coupons/{storeDomain}",
        lambda html: bool(rebates_codes(html, storeDomain)),
    )


def coupons(handles, storeDomain, query):
    # Vouchers are escaped JSON inside a script: \"code\":\"WELCOME10\",\"description\":...
    # Matching the following "description" key skips unrelated "code" keys such as icon names.
    codePattern = re.compile(r'\\"code\\":\\"[^\\"]+\\",\\"description')
    return check_site(
        handles, storeDomain, query, "Coupons.com", "coupons",
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

    return check_site(
        handles, storeDomain, query, "DealCatcher", "dealcatcher",
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

    return check_site(
        handles, storeDomain, query, "DontPayFull", "dontpayfull",
        lambda name: f"https://www.dontpayfull.com/at/{storeDomain}",
        hasCodes,
    )


def offers(handles, storeDomain, query, algolia=None):
    # Unknown stores redirect to the homepage, which also has code offers, so require a /stores/ canonical.
    # Code offers have "hasCode: true" in the link's click handler; the code is revealed only after a click.
    def hasCodes(html):
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        return bool(canonical) and "/stores/" in canonical.group(1) and "hasCode: true" in unescape(html)

    return check_site(
        handles, storeDomain, query, "Offers.com", "offers",
        # Some stores carry a country suffix, e.g. /stores/macys-us/
        lambda name: [f"https://www.offers.com/stores/{name}/", f"https://www.offers.com/stores/{name}-us/"],
        hasCodes,
    )


def savings(handles, storeDomain, query):
    # Code offers are <div class="module-deal ... hasCode ..." data-provider-name="Kohl's">.
    # The page also shows a few offers from other (sponsored) brands. The URL is built from the domain and
    # unknown domains return 404, so the store is the most common provider, even when its name differs from
    # the query (byltbasics.com is "BYLT").
    def hasCodes(html):
        offers = re.findall(r'class="module-deal ([^"]*)"[^>]*data-provider-name="([^"]+)"', html)
        if not offers:
            return False
        store = Counter(provider for _, provider in offers).most_common(1)[0][0]
        return any(provider == store and re.search(r"\bhasCode\b", classes) for classes, provider in offers)

    return check_site(
        handles, storeDomain, query, "Savings.com", "savings",
        lambda name: f"https://www.savings.com/coupons/{storeDomain}",
        hasCodes,
    )


def dealspotr(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "dealspotr", "every store page redirects to SimplyCodes, use simplycodes() instead")


def promocodes(handles, storeDomain, query):
    # Offers are embedded JSON: "couponCode":"GETCOZY"; deals have "couponCode":null
    return check_site(
        handles, storeDomain, query, "PromoCodes.com", "promocodes",
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

    return check_site(
        handles, storeDomain, query, "CouponChief", "couponchief",
        lambda name: f"https://www.couponchief.com/{name}",
        hasCodes,
    )


def goodshop(handles, storeDomain, query):
    # JSON-LD lists each offer as {"@id":"https://www.goodshop.com/coupons/cozyearth.com#72866609", ...,
    # "discountCode":"CE-DISCOVERELLE"}. Most codes are masked ("••••••AN") but still mean a code exists.
    codePattern = re.compile(rf'coupons/{re.escape(storeDomain)}#\d+"[^{{}}]*"discountCode":"[^"]+"')
    return check_site(
        handles, storeDomain, query, "Goodshop", "goodshop",
        lambda name: f"https://www.goodshop.com/coupons/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def simplycodes(handles, storeDomain, query):
    # Code buttons look like data-code="COZY15" ... data-merchant-url="https://cozyearth.com/".
    # Buttons for related stores use data-merchant-url="/store/other.com", so match the store's own URL.
    codePattern = re.compile(rf'data-code="[^"]+"[^>]*data-merchant-url="https?://(www\.)?{re.escape(storeDomain)}')
    return check_site(
        handles, storeDomain, query, "SimplyCodes", "simplycodes",
        lambda name: f"https://simplycodes.com/store/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def tenereteam(handles, storeDomain, query):
    # Each store has its own subdomain. Offers are escaped JSON inside the Next.js payload:
    # \"coupon_code\":\"WELCOME10\"
    codePattern = re.compile(r'\\"coupon_code\\":\\"[^\\"]+\\"')
    return check_site(
        handles, storeDomain, query, "TenereTeam", "tenereteam",
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

    return check_site(
        handles, storeDomain, query, "CouponCause", "couponcause",
        lambda name: f"https://couponcause.com/stores/{name}/",
        hasCodes,
    )


def goodsearch(handles, storeDomain, query):
    # Same company and page layout as Goodshop: JSON-LD offers with "discountCode", mostly masked
    codePattern = re.compile(rf'coupons/{re.escape(storeDomain)}#\d+"[^{{}}]*"discountCode":"[^"]+"')
    return check_site(
        handles, storeDomain, query, "Goodsearch", "goodsearch",
        lambda name: f"https://www.goodsearch.com/coupons/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def valuecom(handles, storeDomain, query):
    # Stores live on subdomains like cozyearth.valuecom.com (no hyphens); a few use store{name}.valuecom.com.
    # Offers are JSON-LD: {"@type":"Offer", ..., "couponCode":"SAMANTHA55", ...}
    return check_site(
        handles, storeDomain, query, "Valuecom", "valuecom",
        lambda name: [
            f"https://{name.replace('-', '')}.valuecom.com/",
            f"https://store{name.replace('-', '')}.valuecom.com/",
        ],
        lambda html: bool(re.search(r'"couponCode":"[^"]+"', html)),
        fetch=plain_retry,
    )


def lovecoupons(handles, storeDomain, query):
    # Offers are <article class="Offer ..." data-type="2" ... data-brand-slug="/cozy-earth">.
    # data-type 2 is a coupon (code), 3 is a sale and 9 is shipping, so only type 2 counts.
    names = set(slugs(query))

    def hasCodes(html):
        offers = re.findall(r'<article class="Offer[^"]*"[^>]*data-type="2"[^>]*data-brand-slug="/([^"]+)"', html)
        return any(slug in names for slug in offers)

    return check_site(
        handles, storeDomain, query, "Love Coupons", "lovecoupons",
        lambda name: f"https://www.lovecoupons.com/{name}",
        hasCodes,
    )


def knoji(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "knoji", "every store URL returns 410 Gone and the homepage redirects to SimplyCodes, use simplycodes() instead")


def swagbucks(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "swagbucks", "store pages show cash back only, no coupon codes")


def joinsmarty(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "joinsmarty", "no public store pages, store lists only show after signing up")


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

    return check_site(
        handles, storeDomain, query, "RebatesMe", "rebatesme",
        lambda name: f"https://www.rebatesme.com/en/stores/{name}",
        hasCodes,
    )


def refermate(handles, storeDomain, query):
    # Codes are escaped JSON in the Next.js payload: \"couponCode\":\"HONEY10\"; deals say "No Code Needed"
    codePattern = re.compile(r'\\"couponCode\\":\\"(?!No Code Needed)[^\\"]+\\"')
    return check_site(
        handles, storeDomain, query, "Refermate", "refermate",
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

    return check_site(
        handles, storeDomain, query, "DiscountReactor", "discountreactor",
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

    return check_site(
        handles, storeDomain, query, "Couponbox", "couponbox",
        lambda name: f"https://www.couponbox.com/coupons/{name}",
        hasCodes,
    )


def dealdrop(handles, storeDomain, query):
    # Each code is an <article id="code-<uuid>"> with a "Show code" button that shows the last characters
    codePattern = re.compile(r'<article id="code-[^"]+"(?:(?!</article>).)*Show code', re.S)
    return check_site(
        handles, storeDomain, query, "DealDrop", "dealdrop",
        lambda name: f"https://www.dealdrop.com/{name}",
        lambda html: bool(codePattern.search(html)),
    )


def revounts(handles, storeDomain, query):
    extDomain = "revounts"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeQueries = query["query"]
    storeHandle = query["handle"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False
        url = f"https://www.revounts.com.au/ajax/search_call.php"
        for i, search in enumerate(storeQueries):
            try:
                response, status = postZenrowsResponse(
                    url,
                    data=f"searchvalue={search}",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
                    },
                )
                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "stores" in jsondata:
                    data = jsondata["stores"]
                    if data is not False:
                        for item in data:
                            refineHandle1 = item["href"].replace("-", "").lower()
                            refineHandle2 = item["name"].replace(" ", "").lower()
                            if (
                                storeHandle in refineHandle1
                                or storeHandle in refineHandle2
                            ):
                                extHandles[storeDomain][extDomain].append(item["href"])
                                isFound = True
                                break

                if isFound:
                    break
            except Exception as e:
                logger.error(f" *** revounts *** Error  fetching {url}: {e}")
                continue

    return extHandles


def dazzdeals(handles, storeDomain, query):
    # Code offers have a hidden <a class="couponBtn hidden showCouponCode showcode">PNIK</a> holding the code's tail
    return check_site(
        handles, storeDomain, query, "DazzDeals", "dazzdeals",
        lambda name: f"https://www.dazzdeals.com/store/{name}/",
        lambda html: 'class="couponBtn hidden showCouponCode' in html,
    )


def greenpromocodes(handles, storeDomain, query):
    # The site is greenpromocode.com (greenpromocodes.com does not resolve).
    # Code offers have view-href="/coupons/cozy-earth/?view=11609534" followed by a "Show Code" button.
    names = set(slugs(query))

    def hasCodes(html):
        offers = re.findall(r'view-href="/coupons/([^/]+)/\?view=\d+"[^>]*><div class="show_code">', html)
        return any(slug in names for slug in offers)

    return check_site(
        handles, storeDomain, query, "GreenPromoCode", "greenpromocode",
        lambda name: f"https://www.greenpromocode.com/coupons/{name}/",
        hasCodes,
    )


def couponbind(handles, storeDomain, query):
    # Each code offer holds the full code in <span class="hiddenCode">CE-DISCOVERELLE</span>
    return check_site(
        handles, storeDomain, query, "CouponBind", "couponbind",
        lambda name: f"https://www.couponbind.com/coupons/{storeDomain}",
        lambda html: bool(re.search(r'<span class="hiddenCode">[^<]+</span>', html)),
    )


def deala(handles, storeDomain, query):
    # The store's own offers are <app-card-coupon-without-store> cards; code offers have a "Get code" button
    codePattern = re.compile(r"<app-card-coupon-without-store(?:(?!</app-card-coupon-without-store>).)*btn-get-code", re.S)
    return check_site(
        handles, storeDomain, query, "DealA", "deala",
        lambda name: f"https://deala.com/{name}",
        lambda html: bool(codePattern.search(html)),
    )


def promopro(handles, storeDomain, query):
    # Unknown stores return 410 Gone. Code offers are <article class="offer_card ... detail_filter_code">.
    return check_site(
        handles, storeDomain, query, "PromoPro", "promopro",
        lambda name: f"https://www.promopro.com/coupon-codes/{name}",
        lambda html: bool(re.search(r'<article class="offer_card[^"]*\bdetail_filter_code\b', html)),
    )


def couponbirds(handles, storeDomain, query):
    extDomain = "couponbirds"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = (
            f"https://www.couponbirds.com/search/autocomplete?searchCode={storeDomain}"
        )

        try:
            response, status = getZenrowsTwoResponse(url, isProxy=True, isJsRender=True)
            if not response:
                return extHandles

            jsondata = json.loads(response)
            if "items" in jsondata and len(jsondata["items"]) > 0:
                items = jsondata["items"]

                for item in items:
                    if "website" in item and storeDomain == item["website"]:
                        handle = f'https://www.couponbirds.com/codes/{item["website"]}'
                        extHandles[storeDomain][extDomain].append(handle)
                        break
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")

    return extHandles


def troupon(handles, storeDomain, query):
    # Each store has its own subdomain. Codes look like data-coupon-code="HOMELIFE" data-coupon-url="https://cozyearth.com"
    codePattern = re.compile(rf'data-coupon-code="[^"]+" data-coupon-url="https?://(www\.)?{re.escape(storeDomain)}')
    return check_site(
        handles, storeDomain, query, "Troupon", "troupon",
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

    return check_site(
        handles, storeDomain, query, "WorthEPenny", "worthepenny",
        lambda name: f"https://{name.replace('-', '')}.worthepenny.com/coupon/",
        hasCodes,
    )


def savingheist(handles, storeDomain, query):
    # Store pages are /store/{slug}/ or /store/{slug}-coupons/. Code buttons look like
    # <button ... data-coupon-id="205324" data-code="BULKRATE">Get Code</button>
    return check_site(
        handles, storeDomain, query, "SavingHeist", "savingheist",
        lambda name: [f"https://savingheist.com/store/{name}/", f"https://savingheist.com/store/{name}-coupons/"],
        lambda html: bool(re.search(r'data-coupon-id="\d+" data-code="[^"]+"', html)),
    )


def coupongrouphy(handles, storeDomain, query):
    # WordPress (Rehub theme) store pages: /dealstore/{slug}/, and many slugs end in "thuy" (dreamlandbabycothuy).
    # Code offers are <span class="... masked_coupon" data-clipboard-text="JULIANASPICOLUK0" ...>.
    return check_site(
        handles, storeDomain, query, "CouponGrouphy", "coupongrouphy",
        lambda name: [f"https://coupongrouphy.com/dealstore/{name}/", f"https://coupongrouphy.com/dealstore/{name}thuy/"],
        lambda html: bool(re.search(r'masked_coupon[^"]*" data-clipboard-text="[^"]+"', html)),
    )


def joincheckmate(handles, storeDomain, query):
    # Store pages use the domain. Codes are masked like <span class="label-regular tracking-widest ...">INS#############</span>
    return check_site(
        handles, storeDomain, query, "Checkmate", "joincheckmate",
        lambda name: f"https://joincheckmate.com/merchants/{storeDomain}",
        lambda html: bool(re.search(r'<span class="label-regular tracking-widest[^"]*">[^<#]*#+</span>', html)),
    )


def faircoupons(handles, storeDomain, query):
    return unsupported(handles, storeDomain, "faircoupons", "site is down: the server does not respond")

# newer

def gocashback(handles, storeDomain, query):
    # Store pages are /stores/{slug} without hyphens. Coupons only appear after JS rendering, where each code
    # sits under <span class="codetext">Code:</span>.
    return check_site(
        handles, storeDomain, query, "GoCashBack", "gocashback",
        lambda name: f"https://www.gocashback.com/stores/{name.replace('-', '')}",
        lambda html: 'class="codetext">Code:</span>' in html,
        fetch=rendered_twice,
    )


def trustdeals(handles, storeDomain, query):
    # Store pages use the domain. Code offers are <article class="coupon nw|common ..."> with <span class="code">.
    # "coupon common" articles ("most common codewords at similar shops", e.g. SAVE10) count as codes,
    # matching the live project; only "coupon expired" ones are skipped.
    codePattern = re.compile(r'<article[^>]*class="coupon (?!expired)[^"]*"(?:(?!</article>).)*<span class="code">[^<]+</span>', re.S)
    return check_site(
        handles, storeDomain, query, "TrustDeals", "trustdeals",
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

    return check_site(
        handles, storeDomain, query, "Couponupto", "couponupto",
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
    return check_site(
        handles, storeDomain, query, "PromosReview", "promosreview",
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

    return check_site(
        handles, storeDomain, query, "Couponsgo", "couponsgo",
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
    return check_site(
        handles, storeDomain, query, "GetsCoupon", "getscoupon",
        lambda name: f"https://www.getscoupon.com/store/{storeDomain}",
        lambda html: bool(codePattern.search(html)),
    )


def rediscount(handles, storeDomain, query):
    # rediscount.com is a parked domain; the real site is reediscount.com (double e).
    # Store pages are /{slug}/ or /{domain}/. Code buttons look like <a rel="nofollow" data-code="NEW10" data-store_info="...">
    return check_site(
        handles, storeDomain, query, "ReeDiscount", "rediscount",
        lambda name: [f"https://www.reediscount.com/{name}/", f"https://www.reediscount.com/{storeDomain}/"],
        lambda html: bool(re.search(r'<a rel="nofollow" data-code="[^"]+" data-store_info=', html)),
    )


def couponterra(handles, storeDomain, query):
    # Mostly prop trading firms, but some retail stores too (cabelas.com). Store pages use the domain.
    # Unknown stores always come back as 422, so fetch once instead of going through the retries.
    # Codes are in full: <div class="get-code getCodeBtn" data-coupon-id="1235" data-code="VORTEX15" ...>
    return check_site(
        handles, storeDomain, query, "CouponTerra", "couponterra",
        lambda name: f"https://www.couponterra.com/coupons/{storeDomain}",
        lambda html: bool(re.search(r'data-coupon-id="\d+"\s+data-code="[^"]+"', html)),
        fetch=fetch_once,
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

    return check_site(
        handles, storeDomain, query, "CouponsKiss", "couponkiss",
        lambda name: f"https://{name}.couponskiss.com/",
        hasCodes,
    )


def shipthedeal(handles, storeDomain, query):
    # The page header states the count: <span class="... font-bold">3</span> Coupon Code(s)
    def hasCodes(html):
        match = re.search(r'font-bold">(\d+)</span>\s*Coupon Code\(s\)', html)
        return bool(match) and int(match.group(1)) > 0

    return check_site(
        handles, storeDomain, query, "ShipTheDeal", "shipthedeal",
        lambda name: f"https://shipthedeal.com/store/{name}-coupon",
        hasCodes,
    )


def couponpromo(handles, storeDomain, query):
    # The site is couponpromo.net (couponpromo.com is for sale). Store page URLs are irregular
    # (/target/target-promo-code.htm), so the site search is used: /search?query={name}.
    # Results carry the full code and the store: data-domain="nobullproject.com" ... data-clipboard-text="GOVX20"
    codePattern = re.compile(rf'data-domain="{re.escape(storeDomain)}"[\s\S]*?data-clipboard-text="[^"]+"')
    names = list(dict.fromkeys(name.replace("-", " ") for name in slugs(query)))
    return check_site(
        handles, storeDomain, query, "CouponPromo", "couponpromo",
        lambda name: [f"https://couponpromo.net/search?query={n.replace(' ', '+')}" for n in names],
        lambda html: bool(codePattern.search(html)),
    )


def couponblender(handles, storeDomain, query):
    # Unknown stores show the homepage (canonical "https://www.couponblender.com//"), so require a store canonical.
    # Each offer is labelled <div class="type">COUPON CODE</div> or <div class="type">DEAL</div>.
    def hasCodes(html):
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        return (
            bool(canonical) and not canonical.group(1).rstrip("/").endswith("couponblender")
            and '<div class="type">COUPON CODE</div>' in html
        )

    return check_site(
        handles, storeDomain, query, "Coupon Blender", "couponblender",
        lambda name: f"https://www.couponblender.com/{name}/",
        hasCodes,
    )


def xyzcodes(handles, storeDomain, query):
    # WordPress "WP Coupons and Deals" plugin; pages often need the JS-challenge retry.
    # Code offers are <a data-type="code" data-coupon-id="3474" ...>; deals use data-type="deal".
    return check_site(
        handles, storeDomain, query, "XYZCodes", "xyzcodes",
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

    return check_site(
        handles, storeDomain, query, "Kotag", "kotag",
        lambda name: f"https://kotag.co/us/marques/codes-promo-{name}/",
        hasCodes,
    )


def zensavings(handles, storeDomain, query):
    # The site is zensaving.com (zensavings.com is parked). Store pages are /store/{slug}, but every
    # store checked (16) lists only deals, no codes.
    return unsupported(handles, storeDomain, "zensavings", "store pages list only deals, no coupon codes")


def coponcodes(handles, storeDomain, query):
    # Store slugs vary (aosom-uk, bioptimizers-com, avidlove-promo-codes), so a few forms are tried.
    # Unknown stores take about 30s and return 422, so each URL is fetched once without retries.
    # Each code is hidden in <span id="code-2505" hidden>BFCM2025</span>
    return check_site(
        handles, storeDomain, query, "Coponcodes", "coponcodes",
        lambda name: [
            f"https://coponcodes.com/store/{name}/",
            f"https://coponcodes.com/store/{storeDomain.replace('.', '-')}/",
            f"https://coponcodes.com/store/{name}-promo-codes/",
            f"https://coponcodes.com/store/{name}-coupon-code/",
        ],
        lambda html: bool(re.search(r'<span id="code-\d+" hidden>[^<]+</span>', html)),
        fetch=fetch_once,
    )


def dealam(handles, storeDomain, query):
    # Code offers are <div class="dx_code_coupons">; cards with "new-class-ads" are ads for other offers.
    codePattern = re.compile(r'class="dx_code_coupons(?![^"]*new-class-ads)[^"]*"')
    return check_site(
        handles, storeDomain, query, "DealAM", "dealam",
        lambda name: f"https://www.dealam.com/coupons/{name}/",
        lambda html: bool(codePattern.search(html)),
    )


def latestdeals(handles, storeDomain, query):
    # UK site whose store pages sit behind an AWS WAF challenge (JS rendering, often incomplete). The page loads its
    # offers from the site's own JSON API, which answers plain requests, so the link is checked through the API:
    # /api/posts?merchantSlugDomain=<domain>&type=voucher&type=offer -> {"entities": {...: {"code": "SPARKS30", ...}}}
    def fetch_api(page_url):
        domain = page_url.rstrip("/").split("/")[-1]
        return get_response(
            f"https://www.latestdeals.co.uk/api/posts?merchantSlugDomain={domain}&type=voucher&type=offer&noExcerpts=true"
        )

    def has_codes(body):
        try:
            posts = (json.loads(body).get("entities") or {}).values()
        except (ValueError, AttributeError):
            return False
        return any(isinstance(post, dict) and post.get("code") for post in posts)

    return check_site(
        handles, storeDomain, query, "LatestDeals", "latestdeals",
        lambda name: f"https://www.latestdeals.co.uk/retailers/{storeDomain}",
        has_codes,
        fetch=fetch_api,
    )


def redeempromos_search(storeDomain, query):
    """Return store page URLs from redeempromos' site search whose name is exactly the store's name."""
    # Store slugs are irregular (/ridge-coupon-code, /pelsbarn-coupn-code, /tsb-living), so the URL can't be guessed.
    # Search only works with a single word: "tsb" finds "TSB Living", "tsb living" finds nothing.
    # Results are cards in the "Matching Stores" section: <a href="https://redeempromos.com/tsb-living"><img alt="TSB Living">
    def key(text):
        return re.sub(r"[^a-z0-9]", "", text.lower())

    def strip_tld(name):
        name = name.strip().lower()
        for tld in (".co.uk", ".com", ".co", ".net", ".org"):
            if name.endswith(tld):
                return name[: -len(tld)]
        return name

    names = [strip_tld(name) for name in [query["handle"]] + query["query"]]
    keys = {key(name) for name in names + [strip_tld(storeDomain)] if len(key(name)) >= 2}
    words = list(dict.fromkeys(name.replace("-", " ").split()[0] for name in names if name.split()))

    urls = []
    for word in words:
        url = f"https://redeempromos.com/search/{quote(word)}"
        try:
            response, status = get_response(url)
            if status != 200:
                continue
            soup = BeautifulSoup(response, "html.parser")
            heading = soup.find(string=lambda text: text and "Matching Stores" in text)
            section = heading.find_parent(class_="section") if heading else None
            if not section:
                continue
            for card in section.find_all("a", href=True):
                img = card.find("img")
                store_name = img.get("alt", "") if img else card.get_text(" ", strip=True)
                if key(store_name) in keys:
                    urls.append(card["href"].split("?")[0])
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
    return list(dict.fromkeys(urls))


def redeempromos(handles, storeDomain, query):
    # The store page is found through the site search (see redeempromos_search).
    # Code offers are <a class="btn-reveal-wrap" href=".../tsb-living?offer=22845"><span class="btn-label">Get Code</span>;
    # deals say "Get Deal". The code itself comes from POST /welcome/getcode (cid=22845), and some "Get Code"
    # offers only hold a placeholder like "OFFER ACTIVATED", so a page counts only if one offer returns a real code.
    def has_codes(html):
        soup = BeautifulSoup(html, "html.parser")
        cids = []
        for button in soup.select("a.btn-reveal-wrap[href]"):
            label = button.select_one("span.btn-label")
            match = re.search(r"[?&]offer=(\d+)", button["href"])
            if label and label.get_text(strip=True).lower() == "get code" and match and match.group(1) not in cids:
                cids.append(match.group(1))

        for cid in cids:
            try:
                # The API isn't protected, so a plain request is enough (no ZenRows credits)
                response = requests.post(
                    "https://redeempromos.com/welcome/getcode",
                    data={"cid": cid, "grabit": 0},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"},
                    timeout=20,
                )
                data = json.loads(response.text)
            except Exception as e:
                logger.error(f"Error fetching RedeemPromos code {cid}: {e}")
                continue
            # Unknown ids return 0 instead of a list
            code = (data[0].get("code") or "").strip() if isinstance(data, list) and data else ""
            if code and " " not in code:
                return True
        return False

    store_urls = redeempromos_search(storeDomain, query)
    return check_site(
        handles, storeDomain, query, "RedeemPromos", "redeempromos",
        lambda name: store_urls,
        has_codes,
    )
