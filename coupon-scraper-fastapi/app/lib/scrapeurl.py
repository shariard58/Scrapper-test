import json
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app.config.headers import headers
from app.lib.helpers import (fetch_once, get_rendered, get_response,
                             getZenrowsTwoResponse, postZenrowsResponse)
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
        if status == 200 and "<body" not in response.lower():
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
    if not is_valid(response):
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
    extDomain = "coupert"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}

    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = (
            f"https://www.coupert.com/api/v3/store/search_stores?keyword={storeDomain}"
        )
        try:
            response = requests.get(url, headers=headers)
            if not response or response.status_code != 200:
                logger.warning(
                    f"Invalid response from {url}: status {response.status_code if response else 'None'}"
                )
            else:
                # Check if response has content before parsing JSON
                if not response.text or not response.text.strip():
                    logger.warning(f"Empty response from {url}")
                else:
                    try:
                        jsondata = response.json()
                        if "data" in jsondata:
                            for item in jsondata["data"]:
                                if "Domain" in item:
                                    if item["Domain"] == storeDomain:
                                        handle = f'https://www.coupert.com/store/{item["Domain"]}'
                                        extHandles[storeDomain][extDomain].append(
                                            handle
                                        )
                    except ValueError as json_error:
                        logger.error(f"Invalid JSON response from {url}: {json_error}")
                        logger.debug(f"Response text: {response.text[:200]}")

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

        urls = [
            f"https://savings.coupert.com/api/browser/search?wd={storeDomain}",
            f"https://top.coupert.com/api/browser/search?wd={storeDomain}",
            f"https://ca.coupert.com/api/browser/search?wd={storeDomain}",
            f"https://fashion.coupert.com/api/browser/search?wd={storeDomain}",
        ]

        for i, url in enumerate(urls):
            try:
                # response = requests.get(url, headers=headers)
                # jsondata = response.json()
                response, status = postZenrowsResponse(url)
                if not response:
                    continue
                jsondata = json.loads(response)

                logger.info(f"Fetching {url}...")
                logger.info(jsondata)

                if "data" in jsondata:
                    for item in jsondata["data"]:
                        if "urlname" in item:
                            pathname = item["urlname"]
                            if pathname.startswith("/"):
                                pathname = pathname[1:]

                            parsed_url = urlparse(url)
                            host = (
                                parsed_url.netloc
                                if parsed_url.netloc
                                else parsed_url.path
                            )

                            handle = "https://" + host + "/" + pathname
                            extHandles[storeDomain][extDomain].append(handle)

            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                continue

    return extHandles


def discounttime(handles, storeDomain, query):
    extDomain = "discountime"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://www.discountime.com/system/brand/search"
        try:
            response, status = postZenrowsResponse(
                url,
                data=json.dumps({"keyWord": storeDomain, "pageNo": 1, "pageSize": 5}),
            )

            if not response:
                return extHandles

            jsondata = json.loads(response)

            if "data" in jsondata:
                if "result" in jsondata["data"]:
                    result = jsondata["data"]["result"]
                    # print(result)
                    for item in result:
                        logger.info(query["handle"])
                        logger.info(item["aliasName"])
                        if query["handle"] == item["aliasName"]:
                            handle = (
                                f'https://www.discountime.com/store/{item["aliasName"]}'
                            )
                            extHandles[storeDomain][extDomain].append(handle)
                            break

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def karmanow(handles, storeDomain, query):
    extDomain = "karma"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        return extHandles
        # url = f'https://www.karmanow.com/s?query={storeDomain}'
        # url = f'https://www.karmanow.com/ws/api/my/v1/items?filter[search]=cozyearth.com&page=1&per_page=20&sort_by=created_at&sort_order=desc&include_global=true&include_user=true'
        # print(f'Fetching {url}...')
        # try:
        #     response, status = getZenrowsTwoResponse(url,
        #         headers={
        #             'Content-Type': 'A'
        #             'Authorization': 'Bearer eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjo2Nzg1NTA5LCJleHAiOjE5MDMyNTcwNDZ9.8n5uxXuKknrguNkZU0oiGChumnZVTjF3_QDDRNCyJGY'
        #         }
        #     )
        #     print(response)
        #     return extHandles
        #     # print(response.text)
        #     jsondata = json.loads(response)

        #     # print(jsondata)

        #     if('data' in jsondata):
        #         if 'result' in jsondata['data']:
        #             result = jsondata['data']['result']
        #             # print(result)
        #             for  item in result:
        #                 print(query['handle'])
        #                 print(item['aliasName'])
        #                 if query['handle'] == item['aliasName']:
        #                     handle = f'https://www.discountime.com/store/{item["aliasName"]}'
        #                     extHandles[storeDomain][extDomain].append(handle)
        #                     break

        # except Exception as e:
        #     logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def rakuten(handles, storeDomain, query):
    extDomain = "rakuten"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    searchQuery = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        for i, search in enumerate(searchQuery):
            url = f"https://www.rakuten.ca/srch/suggest?q={search}&rows=10"
            try:
                isFound = False
                response, status = getZenrowsTwoResponse(url)
                if not response:
                    continue

                jsondata = json.loads(response)

                if "stores" in jsondata:
                    for item in jsondata["stores"]:
                        if "urlName" in item:
                            path = item["urlName"].replace("-", "")
                            if path == storeHandle:
                                handle = f'https://www.rakuten.com/{item["urlName"]}'
                                extHandles[storeDomain][extDomain].append(handle)
                                isFound = True
                                break
                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def hotdeals(handles, storeDomain, query):
    extDomain = "hotdeals"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://beta-api.hotdeals.com/search/get_term_list?keyword={storeDomain}&page_type=home&country_code=US"
        try:
            response, status = getZenrowsTwoResponse(url)
            if not response:
                return extHandles

            jsondata = json.loads(response)

            if "data" in jsondata:
                for item in jsondata["data"]:
                    if "DomainUrl" in item:
                        if item["DomainUrl"] == storeDomain:
                            extHandles[storeDomain][extDomain].append(item["reUrl"])
                            break

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def slickdeals(handles, storeDomain, query):
    extDomain = "slickdeals"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    searchQuery = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        for i, search in enumerate(searchQuery):
            url = f"https://coupons.slickdeals.net/api/search/a1cf309737e64efba2197ca0d5820b5f/{search}"
            try:
                response, status = getZenrowsTwoResponse(url)
                if not response:
                    continue

                jsondata = json.loads(response)

                for item in jsondata:
                    if "url" in item:
                        refineHandle = item["url"].replace("-", "").replace("/", "")
                        if refineHandle == storeHandle:
                            extHandles[storeDomain][extDomain].append(
                                f'https://coupons.slickdeals.net/{item["url"]}'
                            )
                            break
            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def capitalone(handles, storeDomain, query):
    extDomain = "capitalone"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://capitaloneshopping.com/s/{storeDomain}/coupon"
        try:
            response, status = getZenrowsTwoResponse(url)
            if not response:
                return extHandles

            soup = BeautifulSoup(response, "html.parser")

            elements = soup.select(".main-column .store-title h1.bold.charcoal")
            for element in elements:
                notFoundText = "undefined Coupon Codes, Promo Codes & Discounts"
                if element.text != notFoundText:
                    handle = f"https://capitaloneshopping.com/s/{storeDomain}/coupon"
                    extHandles[storeDomain][extDomain].append(handle)
                    break

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def retailmenot(handles, storeDomain, query):
    logger.info(f"Checking RetailMeNot for: {storeDomain}")
    extDomain = "retailmenot"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    if storeDomain.endswith(".co"):
        tld = ".co"
    else:
        tld = ".com"

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        if clean_name.endswith((".com", ".co")):
            final_slug = clean_name
        else:
            final_slug = f"{clean_name}{tld}"

        url = f"https://www.retailmenot.com/view/{final_slug}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking RetailMeNot: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True, isJsRender=True)

            if status == 200:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on RetailMeNot: {url}")
                break

            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching RetailMeNot ({url}): {e}")

    return extHandles


def honey(handles, storeDomain, query):
    extDomain = "honey"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        isFound = False

        for i, search in enumerate(storeQueries):
            queryVars = json.dumps({"count": 10, "query": search})

            url = f"https://d.joinhoney.com/v3?operationName=web_autocomplete&variables={queryVars}"

            try:
                response, status = getZenrowsTwoResponse(url)
                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    data = jsondata["data"]
                    if "autocomplete" in data:
                        autocomplete = data["autocomplete"]
                        if "stores" in autocomplete:
                            results = autocomplete["stores"]
                            for item in results:
                                refineHandle1 = item["name"].replace(" ", "").lower()
                                refineHandle2 = item["label"].replace("-", "").lower()

                                if (
                                    storeHandle in refineHandle1
                                    or storeHandle in refineHandle2
                                ):
                                    handle = f'https://www.joinhoney.com/shop/{item["label"]}'
                                    extHandles[storeDomain][extDomain].append(handle)
                                    isFound = True
                                    break
                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def rebates(handles, storeDomain, query):
    extDomain = "rebates"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://rebates.com/search-stores"

        try:
            response, status = postZenrowsResponse(url, data={"keyword": storeDomain})
            if not response:
                return extHandles
            jsondata = json.loads(response)

            if storeDomain in jsondata:
                extHandles[storeDomain][extDomain].append(
                    f"https://rebates.com/coupons/{storeDomain}"
                )

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def coupons(handles, storeDomain, query):
    extDomain = "coupons"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        url = f"https://www.coupons.com/coupon-codes/{clean_name}/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already found, skipping: {url}")
            break

        try:
            logger.info(f"Checking Coupons.com: {url}")
            response, status = getZenrowsTwoResponse(url)
            logger.info("the status is", status)

            if status == 200:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Valid Store Page on Coupons.com: {url}")
                break
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching Coupons.com ({url}): {e}")

    return extHandles


def dealcatcher(handles, storeDomain, query):
    extDomain = "dealcatcher"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        return extHandles

        url = f"https://www.dealcatcher.com/coupons/{storeDomain}"

        try:
            response, status = getZenrowsTwoResponse(url)
            if status == 200:
                extHandles[storeDomain][extDomain].append(url)

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def dontpayfull(handles, storeDomain, query):
    extDomain = "dontpayfull"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        url = f"https://www.dontpayfull.com/at/{storeDomain}"

        try:
            response, status = getZenrowsTwoResponse(url)
            if not response:
                return extHandles
            if status == 200:
                extHandles[storeDomain][extDomain].append(url)

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def offers(handles, storeDomain, query, algolia):
    extDomain = "offers"
    extHandles = handles.copy()
    storeHandle = query["handle"]
    storeQueries = query["query"]
    hasAlready = isExists(extDomain, storeDomain, handles)
    try:
        app_id = algolia[extDomain]["app-id"]
        api_key = algolia[extDomain]["api-key"]

        if storeDomain not in extHandles:
            extHandles[storeDomain] = {}
        if not hasAlready:
            extHandles[storeDomain][extDomain] = []

            isFound = False
            url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Algolia%20for%20JavaScript%20(4.24.0)%3B%20Browser"

            for i, search in enumerate(storeQueries):
                try:
                    response, status = postZenrowsResponse(
                        url,
                        data=json.dumps(
                            {
                                "requests": [
                                    {
                                        "indexName": "production_companies",
                                        "query": search,
                                        "params": "hitsPerPage=3",
                                    }
                                ]
                            }
                        ),
                        headers={
                            "X-Algolia-API-Key": api_key,
                            "X-Algolia-Application-Id": app_id,
                            "Content-Type": "application/json",
                        },
                    )
                    if not response:
                        continue
                    jsondata = json.loads(response)

                    if "results" in jsondata:
                        results = jsondata["results"]
                        for result in results:
                            if "hits" in result:
                                hits = result["hits"]
                                for hit in hits:
                                    if "url" in hit and storeHandle in hit["url"]:
                                        handle = f'https://www.offers.com{hit["url"]}'
                                        extHandles[storeDomain][extDomain].append(
                                            handle
                                        )
                                        isFound = True
                                        break
                            if isFound:
                                break
                    if isFound:
                        break

                except Exception as e:
                    logger.error(f"Error  fetching {url}: {e}")
                    continue

        return extHandles
    except Exception as e:
        logger.error(f"Error {e}")
        return extHandles


def savings(handles, storeDomain, query):
    extDomain = "savings"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        url = f"https://www.savings.com/coupons/{storeDomain}"

        try:
            response, status = getZenrowsTwoResponse(url)
            if not response:
                return extHandles
            if status == 200:
                extHandles[storeDomain][extDomain].append(url)

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def dealspotr(handles, storeDomain, query):
    logger.info(f"Checking Dealspotr for: {storeDomain}")
    extDomain = "dealspotr"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    if storeDomain.endswith(".co"):
        tld = ".co"
    else:
        tld = ".com"

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()

        if clean_name.endswith((".com", ".co")):
            final_slug = f"{clean_name}{tld}"
        else:
            final_slug = f"{clean_name}"

        url = f"https://dealspotr.com/promo-codes/{final_slug}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking Dealspotr: {url}")
            response, status = postZenrowsResponse(url)
            logger.info("What is the status here:", status)

            if status == 200:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on Dealspotr: {url}")
                break

            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching Dealspotr ({url}): {e}")

    return extHandles


def promocodes(handles, storeDomain, query):
    extDomain = "promocodes"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        for i, search in enumerate(storeQueries):
            url = f"https://www.promocodes.com/api/search"
            try:
                response, status = postZenrowsResponse(
                    url,
                    data=json.dumps(
                        {"includeCategories": True, "onlyCashback": False, "q": search}
                    ),
                    headers={
                        "Content-Type": "application/json",
                        "Referer": "https://www.promocodes.com",
                    },
                )
                if not response:
                    continue
                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    data = jsondata["data"]
                    if "merchants" in data:
                        for item in data["merchants"]:
                            slug = item["slug"]
                            refineHandle = slug.replace("-", "").replace("/", "")
                            if storeHandle in refineHandle:
                                handle = f"https://www.promocodes.com/{slug}-coupons"
                                extHandles[storeDomain][extDomain].append(handle)
                                break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def couponchief(handles, storeDomain, query):
    extDomain = "couponchief"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://www.couponchief.com/ajaxsearch/store_search.php"

        try:
            response, status = postZenrowsResponse(
                url,
                data=f"tb_search={storeDomain}",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
                },
            )
            if not response:
                return extHandles
            jsondata = json.loads(response) if response else []

            # Ensure jsondata is a list
            if not isinstance(jsondata, list):
                return extHandles

            for item in jsondata:
                # Ensure item is a dictionary before accessing keys
                if not isinstance(item, dict):
                    continue

                if "path" not in item or "label" not in item:
                    continue

                path = item["path"]
                label = item["label"]

                if label:
                    soup = BeautifulSoup(label, "html.parser")
                    domain_element = soup.find("span", class_="storeSearchLabel")
                    if not domain_element or not domain_element.text:
                        continue
                    domain = domain_element.text

                    if domain.lower() == storeDomain.lower():
                        path = path.replace("\\", "")
                        handle = f"https://www.couponchief.com{path}"
                        extHandles[storeDomain][extDomain].append(handle)
                        break

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def goodshop(handles, storeDomain, query):
    extDomain = "goodshop"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://www.goodshop.com/shop/merchants?term={storeDomain}"

        try:
            response, status = getZenrowsTwoResponse(url)
            if not response:
                return extHandles
            jsondata = json.loads(response) if response else []

            for item in jsondata:
                if storeDomain in item["urlname"]:
                    handle = f"https://www.goodshop.com/coupons/{storeDomain}"
                    extHandles[storeDomain][extDomain].append(handle)
                    break

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def simplycodes(handles, storeDomain, query):
    extDomain = "simplycodes"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://api.simplycodes.com/v2/merchants/search?query={storeDomain}&isUs=true"

        try:
            response, status = getZenrowsTwoResponse(url)
            if not response:
                return extHandles
            jsondata = json.loads(response) if response else {}

            if "merchants" in jsondata:
                for item in jsondata["merchants"]:
                    if (
                        storeDomain in item["urlSlug"]
                        or storeDomain in item["displayUrl"]
                    ):
                        handle = f'https://simplycodes.com/store/{item["urlSlug"]}'
                        extHandles[storeDomain][extDomain].append(handle)
                        break

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def tenereteam(handles, storeDomain, query):
    extDomain = "tenereteam"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        isFound = False
        for i, search in enumerate(storeQueries):
            url = f"https://www.tenereteam.com/v2/store/search?q={search}"

            try:
                response, status = getZenrowsTwoResponse(url)
                if not response:
                    continue

                jsondata = json.loads(response) if response else []
                # Ensure jsondata is not None and is iterable (list or dict)
                if jsondata is None:
                    jsondata = []
                # If it's a string, try to parse it again or skip
                if isinstance(jsondata, str):
                    try:
                        jsondata = json.loads(jsondata)
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Invalid JSON format from {url}: got string instead of list/dict"
                        )
                        continue
                # Ensure it's a list or dict, convert dict to list of values if needed
                if not isinstance(jsondata, (list, dict)):
                    logger.warning(f"Unexpected JSON type from {url}: {type(jsondata)}")
                    continue
                # Convert dict to list if needed
                if isinstance(jsondata, dict):
                    jsondata = list(jsondata.values()) if jsondata else []

                for item in jsondata:
                    # Ensure item is a dict before accessing keys
                    if not isinstance(item, dict):
                        continue
                    if "alias" not in item or "name" not in item:
                        continue
                    refineHandle1 = (
                        item["alias"].replace("-", "").replace("\\", "").lower()
                    )
                    refineHandle2 = item["name"].replace(" ", "").lower()
                    if storeHandle in refineHandle1 or storeHandle in refineHandle2:
                        handle = f'https://{item["alias"]}.tenereteam.com/coupons'
                        extHandles[storeDomain][extDomain].append(handle)
                        isFound = True
                        break
                if isFound:
                    break
            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def couponcause(handles, storeDomain, query):
    extDomain = "couponcause"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False

        for i, search in enumerate(storeQueries):
            url = f"https://couponcause.com/ajax?merchant={search}"

            try:
                response, status = getZenrowsTwoResponse(url)
                if not response:
                    continue
                jsondata = json.loads(response) if response else {}
                if "merchants" in jsondata:
                    for item in jsondata["merchants"]:
                        refineHandle1 = item["url"].replace("-", "").lower()
                        refineHandle2 = item["name"].replace(" ", "").lower()
                        if storeHandle in refineHandle1 or storeHandle in refineHandle2:
                            handle = f'https://couponcause.com{item["url"]}'
                            extHandles[storeDomain][extDomain].append(handle)
                            isFound = True
                            break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def goodsearch(handles, storeDomain, query):
    extDomain = "goodsearch"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        url = f"https://www.goodsearch.com/coupons/{storeDomain}"

        try:
            response, status = getZenrowsTwoResponse(url)
            if not response:
                return extHandles
            if status == 200:
                extHandles[storeDomain][extDomain].append(url)

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def valuecom(handles, storeDomain, query):
    extDomain = "valuecom"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False

        for i, search in enumerate(storeQueries):
            url = f"https://www.valuecom.com/api/search?wd={search}"

            try:
                response, status = postZenrowsResponse(url)
                if not response:
                    continue
                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    for item in jsondata["data"]:
                        if storeDomain in item["domain"]:
                            handle = item["urlname"]
                            extHandles[storeDomain][extDomain].append(handle)
                            isFound = True
                            break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def lovecoupons(handles, storeDomain, query):
    extDomain = "lovecoupons"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False

        for i, search in enumerate(storeQueries):
            url = f"https://www.lovecoupons.com/search/term?q={search}"

            try:
                response, status = getZenrowsTwoResponse(
                    url, isProxy=True, isJsRender=True
                )
                if not response:
                    continue

                if response is not None:
                    soup = BeautifulSoup(response, "html.parser")
                    elements = soup.select("ul li a")
                    for element in elements:
                        href = element.get("href")
                        refineHandle = href.replace("-", "").replace("/", "")
                        if storeHandle in refineHandle:
                            handle = f"https://www.lovecoupons.com{href}"
                            extHandles[storeDomain][extDomain].append(handle)
                            isFound = True
                            break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def knoji(handles, storeDomain, query):
    extDomain = "knoji"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False

        for i, search in enumerate(storeQueries):
            url = f"https://knoji.com/lookup-jump/all/?term={search}"

            try:
                response, status = getZenrowsTwoResponse(
                    url, isProxy=True, isJsRender=True
                )
                if not response:
                    continue

                jsondata = json.loads(response) if response else []
                # Ensure jsondata is not None and is iterable
                if jsondata is None or not isinstance(jsondata, (list, dict)):
                    jsondata = []
                # Convert dict to list if needed
                if isinstance(jsondata, dict):
                    jsondata = list(jsondata.values()) if jsondata else []

                for item in jsondata:
                    if (
                        not isinstance(item, dict)
                        or "label" not in item
                        or "url" not in item
                    ):
                        continue
                    refineHandle1 = item["label"].replace("-", "").lower()
                    refineHandle2 = item["url"].replace("-", "").lower()
                    if storeHandle in refineHandle1 or storeHandle in refineHandle2:
                        extHandles[storeDomain][extDomain].append(item["url"])
                        isFound = True
                        break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def swagbucks(handles, storeDomain, query):
    extDomain = "swagbucks"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False

        for i, search in enumerate(storeQueries):
            url = f"https://www.swagbucks.com/?cmd=sh-search-autocomplete&term={search}"

            try:
                response, status = postZenrowsResponse(url)
                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    for item in jsondata["data"]:
                        if item["category"] == "Stores":
                            refineHandle1 = item["name"].replace(" ", "").lower()
                            refineHandle2 = item["url"].replace("-", "").lower()
                            if (
                                storeHandle in refineHandle1
                                or storeHandle in refineHandle2
                            ):
                                handle = item["url"].split("?")[0]
                                path_url = f"www.swagbucks.com{handle}"
                                extHandles[storeDomain][extDomain].append(path_url)
                                isFound = True
                                break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def joinsmarty(handles, storeDomain, query):
    extDomain = "joinsmarty"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False
        return extHandles

    return extHandles


def rebatesme(handles, storeDomain, query):
    extDomain = "rebatesme"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False
        url = f"https://www.rebatesme.com/guest-api/rest/search-keywords"
        for i, search in enumerate(storeQueries):
            try:
                response, status = postZenrowsResponse(
                    url,
                    data=json.dumps(
                        {
                            "pageNo": 1,
                            "pageSize": 5,
                            "keywords": search,
                            "searchType": "all",
                        }
                    ),
                    headers={
                        "Content-Type": "application/json",
                        "Referer": "https://www.rebatesme.com",
                    },
                )
                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    data = jsondata["data"]
                    if "resultMap" in data:
                        resultMap = data["resultMap"]
                        if "store" in resultMap:
                            result = resultMap["store"]
                            if "list" in result:
                                for item in result["list"]:
                                    refineHandle1 = (
                                        item["displayName"].replace(" ", "").lower()
                                    )
                                    refineHandle2 = item["url"].replace("-", "").lower()
                                    if (
                                        storeHandle in refineHandle1
                                        or storeHandle in refineHandle2
                                    ):
                                        extHandles[storeDomain][extDomain].append(
                                            item["url"]
                                        )
                                        isFound = True
                                        break
                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def refermate(handles, storeDomain, query):
    extDomain = "refermate"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False

        for i, search in enumerate(storeQueries):
            url = f"https://www.refermate.com/api/search?keyword={search}"

            try:
                response, status = getZenrowsTwoResponse(url)
                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    for item in jsondata["data"]:
                        refineHandle1 = item["name"].replace(" ", "").lower()
                        refineHandle2 = item["url"].replace("-", "").lower()
                        if storeHandle in refineHandle1 or storeHandle in refineHandle2:
                            handle = f'https://www.refermate.com{item["url"]}'
                            extHandles[storeDomain][extDomain].append(handle)
                            isFound = True
                            break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def discountreactor(handles, storeDomain, query):
    extDomain = "discountreactor"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False

        url = f"https://www.discountreactor.com/api/search/results"

        for i, search in enumerate(storeQueries):
            try:
                response, status = postZenrowsResponse(
                    url,
                    data=f"term={search}",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": "https://www.discountreactor.com",
                    },
                )
                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    for item in jsondata["data"]:
                        if storeDomain in item["domain"]:
                            handle = item["url"]
                            extHandles[storeDomain][extDomain].append(handle)
                            isFound = True
                            break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"** discountreactor** Error  fetching {url}: {e}")
                continue

    return extHandles


def couponbox(handles, storeDomain, query):
    extDomain = "couponbox"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeQueries = query["query"]
    storeHandle = query["handle"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        isFound = False
        for i, search in enumerate(storeQueries):
            url = f"https://www.couponbox.com/api/search?query={search}&page=1&shopLimit=5"

            try:
                response, status = getZenrowsTwoResponse(url)
                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "shops" in jsondata:
                    for item in jsondata["shops"]:
                        refineHandle1 = item["name"].replace(" ", "").lower()
                        refineHandle2 = item["resource"].replace("-", "").lower()
                        if storeHandle in refineHandle1 or storeHandle in refineHandle2:
                            handle = f'https://www.couponbox.com{item["resource"]}'
                            extHandles[storeDomain][extDomain].append(handle)
                            isFound = True
                            break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"Error  fetching {url}: {e}")
                continue

    return extHandles


def dealdrop(handles, storeDomain, query):
    extDomain = "dealdrop"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeHandle = query["handle"]
    storeQueries = query["query"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        isFound = False
        url = f"https://www.dealdrop.com/api/deals"

        for i, search in enumerate(storeQueries):
            try:
                response, status = postZenrowsResponse(
                    url,
                    data=json.dumps({"domain": storeDomain}),
                    headers={
                        "Content-Type": "application/json",
                        "Referer": "https://www.dealdrop.com",
                    },
                )

                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "merchant" in jsondata:
                    for item in jsondata["merchant"]:
                        logger.info(item)
                        if storeDomain in item["domain"]:
                            handle = f'https://www.dealdrop.com/{item["slug"]}'
                            extHandles[storeDomain][extDomain].append(handle)
                            isFound = True
                            break

                if isFound:
                    break

            except Exception as e:
                logger.error(f"*** dealdrop *** Error  fetching {url}: {e}")
                continue

    return extHandles


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
    extDomain = "dazzdeals"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    storeQueries = query["query"]
    storeHandle = query["handle"]

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []
        isFound = False
        url = f"https://www.dazzdeals.com/searchsuggest/"
        for i, search in enumerate(storeQueries):
            try:
                response, status = postZenrowsResponse(
                    url,
                    data=f"q={search}",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
                    },
                )
                if not response:
                    continue

                jsondata = json.loads(response) if response else {}

                if "data" in jsondata:
                    data = jsondata["data"]
                    if "store" in data:
                        for item in data["store"]:
                            if item["catetype"] == "store":
                                refineHandle1 = item["slug"].replace("-", "").lower()
                                refineHandle2 = item["title"].replace(" ", "").lower()
                                if (
                                    storeHandle in refineHandle1
                                    or storeHandle in refineHandle2
                                ):
                                    handle = f'https://www.dazzdeals.com/store/{item["slug"]}'
                                    extHandles[storeDomain][extDomain].append(handle)
                                    isFound = True
                                    break

                if isFound:
                    break
            except Exception as e:
                logger.error(f"*** dazzdeals *** Error  fetching {url}: {e}")
                continue

    return extHandles


def greenpromocodes(handles, storeDomain, query):
    extDomain = "greenpromocode"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]

    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        url = f"https://www.greenpromocode.com/coupons/{clean_name}/"
        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already found in handles: {url}")
            break

        try:
            logger.info(f"Checking GreenPromoCode: {url}")
            response, status = postZenrowsResponse(url)

            logger.info("status is", status)

            if status == 200:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on GreenPromoCode: {url}")
                break
            else:
                logger.info(f"Not Found (Status {status}): {url}")

        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")

    return extHandles


def couponbind(handles, storeDomain, query):
    extDomain = "couponbind"
    extHandles = handles.copy()
    hasAlready = isExists(extDomain, storeDomain, handles)

    # storeHandle = query['handle']
    # storeQueries = query['query']

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if not hasAlready:
        extHandles[storeDomain][extDomain] = []

        url = f"https://www.couponbind.com/suggest/"

        try:
            response, status = postZenrowsResponse(
                url,
                data=f"tp=se&kw={storeDomain}",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Referer": "https://www.couponbind.com",
                },
            )
            if not response:
                return extHandles

            jsondata = json.loads(response) if response else []

            for item in jsondata:
                if storeDomain in item["Domain"]:
                    if "UrlName" in item:
                        handle = f'https://www.couponbind.com{item["UrlName"]}'
                        extHandles[storeDomain][extDomain].append(handle)
                        break

        except Exception as e:
            logger.error(f"Error  fetching {url}: {e}")

    return extHandles


def deala(handles, storeDomain, query):
    extDomain = "deala"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        url = f"https://www.deala.com/{clean_name}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking Deala: {url}")

            response, status = postZenrowsResponse(url)

            if status == 200:

                if (
                    "Page Not Found" not in response
                    and 'id="__next_error__"' not in response
                ):
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Valid Deala Store Page: {url}")
                    break
                else:
                    logger.info(f"Deala: 404/Invalid Page detected for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching Deala ({url}): {e}")

    return extHandles


def promopro(handles, storeDomain, query):
    logger.info("Starting promopro scrape...")
    extDomain = "promopro"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    queries = query["query"]
    if isinstance(queries, str):
        queries = [queries]

    all_names = [query["handle"]] + queries
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        url = f"https://www.promopro.com/coupon-codes/{clean_name}"

        if url in extHandles[storeDomain][extDomain]:
            continue

        try:
            logger.info(f"Checking PromoPro: {url}")
            response, status = postZenrowsResponse(url)

            if status == 200 and 'id="__next_error__"' not in response:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Valid Link Added: {url}")

        except Exception as e:
            logger.error(f"Error fetching PromoPro ({url}): {e}")

    return extHandles


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
    extDomain = "troupon"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]

    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        url = f"https://{clean_name}.troupon.com/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already found: {url}")
            break

        try:
            logger.info(f"Checking Troupon: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on Troupon: {url}")

                break
            else:
                logger.info(f"Not Found: {url}")

        except Exception as e:
            logger.error(f"Error: {e}")

    return extHandles


def worthepenny(handles, storeDomain, query):
    extDomain = "worthepenny"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]

    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:

        clean_name = name.replace(" ", "-").lower()
        url = f"https://{clean_name}.worthepenny.com/coupon/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping search: {url}")
            break

        try:
            logger.info(f"Checking Worthepenny: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200:
                verify_name = clean_name.replace("-", "")
                if verify_name in response.lower().replace("-", ""):
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on Worthepenny: {url}")
                    break
                else:
                    logger.info(f"Worthepenny: Name mismatch on page.")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching Worthepenny ({url}): {e}")

    return extHandles


def savingheist(handles, storeDomain, query):
    extDomain = "savingheist"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:

        clean_name = name.replace(" ", "-").lower()
        url = f"https://www.savingheist.com/store/{clean_name}/"
        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking Savingheist: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True, isJsRender=True)

            if status != 200:
                logger.info(f"Not found: {url} (Status: {status})")
                continue
            if isinstance(response, dict) and "code" in response:
                logger.info(f"SavingHeist: Page returned error code {response['code']}")
                continue

            extHandles[storeDomain][extDomain].append(url)
            logger.info(f"Success! Found on SavingHeist: {url}")
            break

        except Exception as e:
            logger.error(f"Error fetching Savingheist ({url}): {e}")

    return extHandles


def coupongrouphy(handles, storeDomain, query):
    extDomain = "coupongrouphy"
    extHandles = handles.copy()
    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        url = f"https://coupongrouphy.com/dealstore/{clean_name}/"
        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping search: {url}")
            break

        try:
            logger.info(f"Checking Coupongrouphy: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200:
                match_name = clean_name.split("-")[0].split("_")[0]

                if match_name in response.lower():
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on Coupongrouphy: {url}")
                    break
                else:
                    logger.info(f"Coupongrouphy: Content mismatch for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching Coupongrouphy ({url}): {e}")

    return extHandles


def joincheckmate(handles, storeDomain, query):
    extDomain = "joincheckmate"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    if storeDomain.endswith(".co"):
        tld = ".co"
    else:
        tld = ".com"

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        if clean_name.endswith((".com", ".co")):
            final_slug = clean_name
        else:
            final_slug = f"{clean_name}{tld}"

        url = f"https://joincheckmate.com/merchants/{final_slug}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping search: {url}")
            break

        try:
            logger.info(f"Checking JoinCheckmate: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200:
                match_name = final_slug.split(".")[0].split("-")[0]

                if match_name in response.lower():
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on JoinCheckmate: {url}")
                    break
                else:
                    logger.info(f"JoinCheckmate: Content mismatch for {final_slug}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching JoinCheckmate ({url}): {e}")

    return extHandles


def faircoupons(handles, storeDomain, query):
    extDomain = "faircoupons"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:

        clean_name = name.replace(" ", "-").lower()
        url = f"https://www.faircoupons.com/stores/{clean_name}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking Faircoupons: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200:
                if 'id="__next_error__"' not in response:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Valid Store Page: {url}")
                    break
                else:
                    logger.info(
                        f"Faircoupons: 404/Error Page detected for {clean_name}"
                    )
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching Faircoupons ({url}): {e}")

    return extHandles

# newer

def gocashback(handles, storeDomain, query):
    extDomain = "gocashback"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://www.gocashback.com/store/{clean_name}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking GoCashBack: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200:
                match_name = clean_name.replace("-", "")

                if match_name in response.lower().replace("-", ""):
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on GoCashBack: {url}")
                    break
                else:
                    logger.info(f"GoCashBack: Content mismatch for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching GoCashBack ({url}): {e}")

    return extHandles


def trustdeals(handles, storeDomain, query):
    extDomain = "trustdeals"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    url = f"https://www.trustdeals.com/view/{storeDomain}"

    try:
        logger.info(f"Checking TrustDeals: {url}")
        response, status = getZenrowsTwoResponse(url, isProxy=True)

        if status == 200 and response:
            soup = BeautifulSoup(response, "html.parser")
            code_spans = soup.select("span.code")
            if code_spans:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on TrustDeals: {url}")
            else:
                logger.info(f"TrustDeals: No coupon codes found for {storeDomain}")
        else:
            logger.info(f"Not found: {url} (Status: {status})")

    except Exception as e:
        logger.error(f"Error fetching TrustDeals ({url}): {e}")

    return extHandles


def couponupto(handles, storeDomain, query):
    extDomain = "couponupto"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://www.couponupto.com/coupons/{clean_name}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking CouponUpto: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Check for coupon code elements (a.a_code with p[id^='code-'])
                code_tags = soup.select("a.a_code p[id^='code-']")
                if code_tags:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on CouponUpto: {url}")
                    break
                else:
                    logger.info(f"CouponUpto: No coupon codes found for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching CouponUpto ({url}): {e}")

    return extHandles


def promosreview(handles, storeDomain, query):
    extDomain = "promosreview"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "")
        for tld in (".com", ".co", ".net", ".org"):
            if clean_name.lower().endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        variants = list(dict.fromkeys([
            clean_name,
            clean_name.capitalize(),
            clean_name.upper(),
            clean_name.lower(),
        ]))

        found = False
        for variant in variants:
            url = f"https://www.promosreview.com/store/{variant}-coupon-codes"

            if url in extHandles[storeDomain][extDomain]:
                logger.info(f"Already exists, skipping: {url}")
                found = True
                break

            try:
                logger.info(f"Checking PromosReview: {url}")
                response, status = getZenrowsTwoResponse(url, isProxy=True)

                if status == 200 and response:
                    soup = BeautifulSoup(response, "html.parser")
                    # Check for coupon code elements with data-code attribute
                    code_elements = soup.select("[data-code]")
                    if code_elements:
                        extHandles[storeDomain][extDomain].append(url)
                        logger.info(f"Success! Found on PromosReview: {url}")
                        found = True
                        break
                    else:
                        logger.info(f"PromosReview: No coupon codes found for {variant}")
                else:
                    logger.info(f"Not found: {url} (Status: {status})")

            except Exception as e:
                logger.error(f"Error fetching PromosReview ({url}): {e}")

        if found:
            break

    return extHandles


def couponsgo(handles, storeDomain, query):
    extDomain = "couponsgo"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://www.couponsgo.org/search?query={clean_name}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking CouponsGo: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Accept only pages that contain revealable coupon codes (not deal-only pages).
                coupon_divs = soup.select("div.coupon-item.c-r-item.couponsgoorg")
                show_code_links = soup.select('a[onclick*="click_show_code"]')

                if coupon_divs and show_code_links:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on CouponsGo: {url}")
                    break
                elif coupon_divs:
                    logger.info(
                        f"CouponsGo: Deal-only page (no show code) for {clean_name}"
                    )
                else:
                    logger.info(f"CouponsGo: No coupons found for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching CouponsGo ({url}): {e}")

    return extHandles


def getscoupon(handles, storeDomain, query):
    extDomain = "getscoupon"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    url = f"https://www.getscoupon.com/store/{storeDomain}"

    try:
        logger.info(f"Checking GetsCoupon: {url}")
        response, status = getZenrowsTwoResponse(url, isProxy=True)

        if status == 200 and response:
            soup = BeautifulSoup(response, "html.parser")
            # Check for coupon elements with data-code attribute
            code_divs = soup.find_all("div", class_="getlistr")
            has_codes = any(div.get("data-code") for div in code_divs)
            if has_codes:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on GetsCoupon: {url}")
            else:
                logger.info(f"GetsCoupon: No coupon codes found for {storeDomain}")
        else:
            logger.info(f"Not found: {url} (Status: {status})")

    except Exception as e:
        logger.error(f"Error fetching GetsCoupon ({url}): {e}")

    return extHandles


def rediscount(handles, storeDomain, query):
    extDomain = "rediscount"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://www.reediscount.com/{clean_name}/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking ReeDiscount: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Check for coupon code buttons with data-code attribute
                buttons = soup.select(".coupons_buttons a.coupon_btn")
                has_codes = any(btn.get("data-code") for btn in buttons)
                if has_codes:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on ReeDiscount: {url}")
                    break
                else:
                    logger.info(f"ReeDiscount: No coupon codes found for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching ReeDiscount ({url}): {e}")

    return extHandles


def couponterra(handles, storeDomain, query):
    extDomain = "couponterra"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    url = f"https://www.couponterra.com/coupons/{storeDomain}"

    try:
        logger.info(f"Checking CouponTerra: {url}")
        response, status = getZenrowsTwoResponse(url, isProxy=True)

        if status == 200 and response:
            soup = BeautifulSoup(response, "html.parser")
            # Check for coupon elements with data-code attribute
            code_elements = soup.select("[data-code]")
            has_codes = any(el.get("data-code") for el in code_elements)
            if has_codes:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on CouponTerra: {url}")
            else:
                logger.info(f"CouponTerra: No coupon codes found for {storeDomain}")
        else:
            logger.info(f"Not found: {url} (Status: {status})")

    except Exception as e:
        logger.error(f"Error fetching CouponTerra ({url}): {e}")

    return extHandles


def couponkiss(handles, storeDomain, query):
    extDomain = "couponkiss"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://{clean_name}.couponskiss.com"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking CouponKiss: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Only accept pages that have a revealable "Show Coupon Code" button.
                show_code_spans = [
                    el for el in soup.find_all("span")
                    if el.get_text(strip=True).lower() == "show coupon code"
                ]
                if show_code_spans:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on CouponKiss: {url}")
                    break
                else:
                    logger.info(f"CouponKiss: No revealable coupon codes for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching CouponKiss ({url}): {e}")

    return extHandles


def shipthedeal(handles, storeDomain, query):
    extDomain = "shipthedeal"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://shipthedeal.com/store/{clean_name}-coupon"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking ShipTheDeal: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Only accept pages that expose an explicit Show coupon code trigger.
                show_code_elements = [
                    el
                    for el in soup.find_all(["div", "span", "a", "button"])
                    if el.get_text(strip=True).lower() == "show coupon code"
                ]

                if show_code_elements:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on ShipTheDeal: {url}")
                    break
                else:
                    logger.info(
                        f"ShipTheDeal: No revealable coupon codes for {clean_name}"
                    )
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching ShipTheDeal ({url}): {e}")

    return extHandles


def couponpromo(handles, storeDomain, query):
    extDomain = "couponpromo"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://couponpromo.net/search?query={clean_name}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking CouponPromo: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Only accept pages that have a revealable "Reveal Code" trigger.
                reveal_code_elements = [
                    el
                    for el in soup.find_all(["div", "span", "a", "button"])
                    if el.get_text(strip=True).lower() == "reveal code"
                ]

                if reveal_code_elements:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on CouponPromo: {url}")
                    break
                else:
                    logger.info(f"CouponPromo: No revealable coupon codes for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching CouponPromo ({url}): {e}")

    return extHandles


def couponblender(handles, storeDomain, query):
    extDomain = "couponblender"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://www.couponblender.com/{clean_name}/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking CouponBlender: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Only accept pages that expose an explicit Show Coupon Code trigger.
                show_code_elements = [
                    el
                    for el in soup.find_all(["div", "span", "a", "button"])
                    if el.get_text(strip=True).lower() == "show coupon code"
                ]

                if show_code_elements:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on CouponBlender: {url}")
                    break
                else:
                    logger.info(
                        f"CouponBlender: No revealable coupon codes for {clean_name}"
                    )
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching CouponBlender ({url}): {e}")

    return extHandles


def xyzcodes(handles, storeDomain, query):
    extDomain = "xyzcodes"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://xyzcodes.com/store/{clean_name}/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking XYZCodes: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Only accept pages that have an explicit "Show Code" span element.
                show_code_spans = [
                    el
                    for el in soup.find_all("span")
                    if el.get_text(strip=True).lower() == "show code"
                ]

                if show_code_spans:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on XYZCodes: {url}")
                    break
                else:
                    logger.info(f"XYZCodes: No revealable coupon codes for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching XYZCodes ({url}): {e}")

    return extHandles


def kotag(handles, storeDomain, query):
    extDomain = "kotag"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://kotag.co/us/marques/codes-promo-{clean_name}/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking Kotag: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Only accept pages that have an explicit "Show coupon code" button.
                show_code_buttons = [
                    el
                    for el in soup.find_all("button")
                    if el.get_text(strip=True).lower() == "show coupon code"
                ]

                if show_code_buttons:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on Kotag: {url}")
                    break
                else:
                    logger.info(f"Kotag: No revealable coupon codes for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching Kotag ({url}): {e}")

    return extHandles


def zensavings(handles, storeDomain, query):
    extDomain = "zensavings"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://zensaving.com/store/{clean_name}/"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking ZenSaving: {url}")
            response, status = getZenrowsTwoResponse(
                url, isProxy=True, isJsRender=True
            )

            if status == 200 and response:
                soup = BeautifulSoup(response, "html.parser")
                # Only accept pages that expose a real code trigger span.
                get_code_spans = [
                    el
                    for el in soup.select("span.get-code")
                    if " ".join(el.get_text(" ", strip=True).split()).lower()
                    == "get code"
                ]

                if get_code_spans:
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on ZenSaving: {url}")
                    break
                else:
                    logger.info(f"ZenSaving: No revealable coupon codes for {clean_name}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching ZenSaving ({url}): {e}")

    return extHandles


def coponcodes(handles, storeDomain, query):
    extDomain = "coponcodes"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    suffixes = ["-coupon-code", "-promo-codes", "-coupon-codes", "-coupons", "-voucher-codes"]

    for name in unique_names:
        clean_name = str(name).strip().replace(" ", "-").lower()
        for tld in (".com", ".co", ".net", ".org", ".co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        # Guard against noisy/invalid search terms that can lead to generic pages.
        clean_name = clean_name.strip("-/")
        if len(clean_name) < 3:
            continue

        for suffix in suffixes:
            url = f"https://coponcodes.com/store/{clean_name}{suffix}/"

            if url in extHandles[storeDomain][extDomain]:
                logger.info(f"Already exists, skipping: {url}")
                return extHandles

            try:
                logger.info(f"Checking CoponCodes: {url}")
                response, status = getZenrowsTwoResponse(
                    url, isProxy=True, isJsRender=True
                )

                if status == 200 and response:
                    soup = BeautifulSoup(response, "html.parser")

                    # Accept only pages that expose real coupon-code triggers.
                    has_get_code = any(
                        el.get_text(strip=True) == "Get Code"
                        for el in soup.find_all("span")
                    )

                    # Prevent unrelated store pages by requiring current store slug marker.
                    response_lower = response.lower()
                    has_store_marker = (
                        f"/store/{clean_name}" in response_lower
                        or clean_name in response_lower
                    )

                    if has_get_code and has_store_marker:
                        extHandles[storeDomain][extDomain].append(url)
                        logger.info(f"Success! Found on CoponCodes: {url}")
                        return extHandles

                    logger.info(
                        f"CoponCodes: Skip mismatch/deal-only page for {clean_name}"
                    )
                else:
                    logger.info(f"Not found: {url} (Status: {status})")

            except Exception as e:
                logger.error(f"Error fetching CoponCodes ({url}): {e}")

    return extHandles


def dealam(handles, storeDomain, query):
    extDomain = "dealam"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        for tld in (".com","co.uk"):
            if clean_name.endswith(tld):
                clean_name = clean_name[: -len(tld)]
                break

        url = f"https://www.dealam.com/stores/{clean_name}.html"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping: {url}")
            break

        try:
            logger.info(f"Checking DealAM: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                extHandles[storeDomain][extDomain].append(url)
                logger.info(f"Success! Found on DealAM: {url}")
                break
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching DealAM ({url}): {e}")

    return extHandles


def latestdeals(handles, storeDomain, query):
    extDomain = "latestdeals"
    extHandles = handles.copy()

    if storeDomain not in extHandles:
        extHandles[storeDomain] = {}
    if extDomain not in extHandles[storeDomain]:
        extHandles[storeDomain][extDomain] = []

    all_names = [query["handle"]] + query["query"]
    unique_names = list(dict.fromkeys(all_names))

    if storeDomain.endswith(".co"):
        tld = ".co"
    else:
        tld = ".com"

    for name in unique_names:
        clean_name = name.replace(" ", "-").lower()
        if clean_name.endswith((".com", ".co")):
            final_slug = clean_name
        else:
            final_slug = f"{clean_name}{tld}"

        url = f"https://www.latestdeals.co.uk/retailers/{final_slug}"

        if url in extHandles[storeDomain][extDomain]:
            logger.info(f"Already exists, skipping search: {url}")
            break

        try:
            logger.info(f"Checking LatestDeals: {url}")
            response, status = getZenrowsTwoResponse(url, isProxy=True)

            if status == 200 and response:
                match_name = final_slug.split(".")[0].split("-")[0]

                if match_name in response.lower():
                    extHandles[storeDomain][extDomain].append(url)
                    logger.info(f"Success! Found on LatestDeals: {url}")
                    break
                else:
                    logger.info(f"LatestDeals: Content mismatch for {final_slug}")
            else:
                logger.info(f"Not found: {url} (Status: {status})")

        except Exception as e:
            logger.error(f"Error fetching LatestDeals ({url}): {e}")

    return extHandles
