import asyncio
import base64
import json
import re
from html import unescape
from urllib.parse import parse_qs, urlparse

import cloudscraper
import requests  # type: ignore
from bs4 import BeautifulSoup

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


# Helper function: Extract coupon code from coupon object
def _extract_coupon_code(coupon):
    """
    Extract coupon code from a coupon dictionary.
    Args: coupon (dict) - Dictionary with 'code' key
    Returns: str or None - Uppercase coupon code or None
    """
    if isinstance(coupon, dict) and "code" in coupon and coupon["code"]:
        return coupon["code"].upper()
    return None


# Method 1: Try API endpoint
def _try_coupert_api_method(url):
    """
    Fetch coupons from Coupert API endpoint.
    Args: url (str) - Coupert store page URL
    Returns: list - List of uppercase coupon codes
    """
    try:
        store_domain = url.split("/")[-1]
        if not store_domain:
            return []

        api_url = f"https://www.coupert.com/api/v3/store/{store_domain}"

        response = fetchRequest(api_url)
        if response and response.status_code == 200:
            data = response.json()

            # Try different JSON structures
            coupon_data = None
            if "coupons" in data:
                coupon_data = data["coupons"]
            elif "data" in data and isinstance(data["data"], dict):
                if "coupons" in data["data"]:
                    coupon_data = data["data"]["coupons"]

            # Extract coupon codes
            coupons = []
            if coupon_data:
                for coupon in coupon_data:
                    code = _extract_coupon_code(coupon)
                    if code:
                        coupons.append(code)
            return coupons
    except Exception as e:
        logger.debug(f"Coupert API method failed for {url}: {e}")
    return []


# Method 2: Extract from __NEXT_DATA__ (Next.js structure)
def _try_coupert_next_data_method(soup):
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
        if "props" in json_data:
            props = json_data["props"]
            if "pageProps" in props:
                pageProps = props["pageProps"]
                if "storeInfo" in pageProps:
                    storeInfo = pageProps["storeInfo"]
                    if "data" in storeInfo and "coupons" in storeInfo["data"]:
                        for item in storeInfo["data"]["coupons"]:
                            code = _extract_coupon_code(item)
                            if code:
                                coupons.append(code)
    except Exception as e:
        logger.debug(f"Coupert __NEXT_DATA__ parsing failed: {e}")
    return coupons


# Method 3: Extract from __NUXT_DATA__ (Nuxt.js structure)
def _try_coupert_nuxt_data_method(soup):
    """
    Extract coupons from Nuxt.js __NUXT_DATA__ script tag.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        nuxt_data = soup.select_one("script#__NUXT_DATA__")
        if not nuxt_data:
            return coupons

        json_data = json.loads(nuxt_data.string)
        if isinstance(json_data, list) and len(json_data) > 0:
            for item in json_data:
                if isinstance(item, str):
                    try:
                        dataText = base64.b64decode(item).decode("utf-8")
                        dataJson = json.loads(dataText)

                        if "merchant_coupons" in dataJson:
                            couponsItem = dataJson["merchant_coupons"]
                            for itm in couponsItem:
                                code = _extract_coupon_code(itm)
                                if code:
                                    coupons.append(code)
                    except Exception:
                        continue
    except Exception as e:
        logger.debug(f"Coupert __NUXT_DATA__ parsing failed: {e}")
    return coupons


# Method 4: Extract from script tags using regex
def _try_coupert_script_regex_method(soup):
    """
    Extract coupons from script tags using regex patterns.
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
                # Look for JSON containing coupon codes
                if "code" in script.string.lower() or "coupon" in script.string.lower():
                    # Try to find JSON objects with code field
                    matches = re.findall(
                        r'"code"\s*:\s*"([A-Z0-9]{4,30})"', script.string, re.IGNORECASE
                    )
                    for code in matches:
                        if 4 <= len(code) <= 30:
                            coupons.append(code.upper())
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Coupert script regex method failed: {e}")
    return coupons


# Method 5: Extract from visible HTML elements
def _try_coupert_html_elements_method(soup):
    """
    Extract coupons from visible HTML elements.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        excluded_texts = {
            "CODE",
            "COPY",
            "SAVE",
            "DEAL",
            "FREE",
            "SHOP",
            "GETCODE",
            "GET",
            "NOW",
        }

        code_elements = soup.find_all(
            ["span", "div", "button", "input"],
            class_=re.compile(r"code|coupon", re.IGNORECASE),
        )
        for element in code_elements:
            text = element.get_text(strip=True)
            # Filter for valid coupon code patterns
            if text and re.match(r"^[A-Z0-9]{4,30}$", text):
                # Exclude common false positives
                if text not in excluded_texts:
                    coupons.append(text.upper())
    except Exception as e:
        logger.debug(f"Coupert HTML elements method failed: {e}")
    return coupons


# Main function: fetch coupons from coupert
def fetch_coupons_from_coupert(links):
    """
    Fetch coupons from Coupert using multiple extraction methods.
    Args: links (list) - List of Coupert store page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []

        for url in urls:
            try:
                # Method 1: Try API endpoint (fastest)
                api_coupons = _try_coupert_api_method(url)
                if api_coupons:
                    coupons.extend(api_coupons)
                    # If API method succeeds, skip HTML methods for this URL
                    continue

                # Fetch HTML once for all HTML-based methods
                html = getZenrowsResponse(url)
                if not html:
                    continue

                soup = BeautifulSoup(html, "html.parser")

                # Method 2: Try __NEXT_DATA__
                next_data_coupons = _try_coupert_next_data_method(soup)
                if next_data_coupons:
                    coupons.extend(next_data_coupons)
                    # If Next.js method succeeds, skip remaining methods for this URL
                    continue

                # Method 3: Try __NUXT_DATA__
                nuxt_coupons = _try_coupert_nuxt_data_method(soup)
                if nuxt_coupons:
                    coupons.extend(nuxt_coupons)
                    # If Nuxt.js method succeeds, skip remaining methods for this URL
                    continue

                # Method 4: Try script regex parsing
                script_coupons = _try_coupert_script_regex_method(soup)
                if script_coupons:
                    coupons.extend(script_coupons)
                    # If script method succeeds, skip remaining methods for this URL
                    continue

                # Method 5: Try HTML elements (fallback)
                html_coupons = _try_coupert_html_elements_method(soup)
                if html_coupons:
                    coupons.extend(html_coupons)

            except Exception as e:
                logger.error(f"Error scraping coupert for {url}: {e}")
                continue

        # Return unique coupons after processing all URLs
        unique_coupons = list(set(coupons))
        if unique_coupons:
            logger.info(f"Found {len(unique_coupons)} unique coupons from Coupert")
        return unique_coupons

    except Exception as e:
        logger.error(f"Error coupert fetching: {e}")
        return []


# Helper function: Get bad words list for hotdeals
def _get_hotdeals_bad_words():
    """
    Get list of excluded terms for hotdeals coupon extraction.
    Returns: list - List of strings to exclude from coupon codes
    """
    return [
        "HTML",
        "UTF8",
        "TRUE",
        "FALSE",
        "WIDTH",
        "HEIGHT",
        "DEALS",
        "PROMO",
        "DECEMBER",
    ]


# Method 1: Extract coupons from code:"..." pattern in HTML
def _extract_hotdeals_nuxt_codes(html):
    """
    Extract coupon codes from code:"..." pattern in HTML.
    Args: html (str) - Raw HTML content
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        nuxt_codes = re.findall(r'code:"([^"]+)"', html)
        for code in nuxt_codes:
            if code and code.lower() != "none":
                coupons.append(code.upper())
    except Exception as e:
        logger.debug(f"Hotdeals nuxt codes extraction failed: {e}")
    return coupons


# Method 2: Extract coupons from meta description
def _extract_hotdeals_meta_description(soup):
    """
    Extract coupon codes from meta description tag.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        meta_desc = soup.find("meta", {"name": "description"})
        if meta_desc:
            content = meta_desc.get("content", "")
            matches = re.findall(r'"([^"]*)"', content)
            for match in matches:
                if 4 <= len(match) <= 15:
                    coupons.append(match.upper())
    except Exception as e:
        logger.debug(f"Hotdeals meta description extraction failed: {e}")
    return coupons


# Method 3: Extract coupons using regex pattern matching
def _extract_hotdeals_regex_pattern(html):
    """
    Extract coupon codes using regex pattern matching.
    Args: html (str) - Raw HTML content
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        pattern = r"\b[A-Z]{2,}[A-Z0-9]{2,}\b"
        raw_matches = re.findall(pattern, html)
        bad_words = _get_hotdeals_bad_words()

        for code in raw_matches:
            # Must not be all digits, have reasonable length, not in bad words, and have at least 2 letters
            if not code.isdigit() and len(code) < 15 and code not in bad_words:
                if sum(char.isalpha() for char in code) >= 2:
                    coupons.append(code)
    except Exception as e:
        logger.debug(f"Hotdeals regex pattern extraction failed: {e}")
    return coupons


# Main function: fetch coupons from hotdeals
def fetch_coupons_from_hotdeals(links):
    """
    Fetch coupons from HotDeals using multiple extraction methods.
    Args: links (list) - List of HotDeals page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []

        for url in urls:
            try:
                html = getZenrowsResponse(url)
                if not html:
                    continue

                # Method 1: Extract from code:"..." pattern
                nuxt_coupons = _extract_hotdeals_nuxt_codes(html)
                coupons.extend(nuxt_coupons)

                # Method 2: Extract from meta description
                soup = BeautifulSoup(html, "html.parser")
                meta_coupons = _extract_hotdeals_meta_description(soup)
                coupons.extend(meta_coupons)

                # Method 3: Extract using regex pattern matching
                regex_coupons = _extract_hotdeals_regex_pattern(html)
                coupons.extend(regex_coupons)

            except Exception as e:
                logger.error(f"Error scraping HotDeals for {url}: {e}")
                continue

        # Return unique coupons after processing all URLs
        unique_coupons = list(set(coupons))
        if unique_coupons:
            logger.info(f"Found {len(unique_coupons)} unique coupons from HotDeals")
        return unique_coupons

    except Exception as e:
        logger.error(f"Error hotdeals fetching: {e}")
        return []


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

    if not links or not isinstance(links, list):
        logger.info("No links provided or invalid format.")
        return []

    all_coupons = []
    unique_urls = list(dict.fromkeys([url.strip() for url in links if url]))

    for url in unique_urls:
        try:
            logger.info(f" Processing: {url}")
            html = getZenrowsResponse(url)
            if not html:
                logger.info(f" Empty response from ZenRows for: {url}")
                continue

            soup = BeautifulSoup(html, "html.parser")

            tag = soup.find(
                "script", string=lambda t: t and "window.__remixContext" in t
            )
            if not tag:
                logger.info(f" RemixContext tag not found for: {url}")
                continue

            script_txt = tag.string.strip() if tag.string else tag.text.strip()

            match = re.search(r"({.*})", script_txt, re.DOTALL)
            if not match:
                logger.info(f" Regex failed for: {url}")
                continue

            data_dict = json.loads(match.group(1))
            loader_data = data_dict.get("state", {}).get("loaderData", {})

            store_route = loader_data.get("routes/__app/s.$store.coupon", {})

            coupons_list = (
                store_route.get("StorePage", {})
                .get("site", {})
                .get("couponsInfo", {})
                .get("coupons", [])
            )

            current_site_coupons = 0
            for item in coupons_list:
                code = item.get("code")
                if code:
                    all_coupons.append(code.strip().upper())
                    current_site_coupons += 1

            logger.info(f" {current_site_coupons} coupons from this link.")

        except json.JSONDecodeError:
            logger.info(f" JSON Parse Error for: {url}")
        except Exception as e:
            logger.info(f" Unexpected Error for {url}: {str(e)}")

    final_result = sorted(list(set(all_coupons)))
    logger.info(f" Total Unique Coupons Found: {len(final_result)}")
    return final_result


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
    """
    Fetch coupons from Rebates by extracting data-code attributes.
    Args: links (list) - List of Rebates page URLs
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
            pattern = r'coupon[\\"]*\s*,\s*[\\"]*code[\\"]*\s*:\s*[\\"]*([^\\"]+)'
            found_codes = re.findall(pattern, html)

            for code in found_codes:
                clean_code = code.strip()
                if clean_code and clean_code.lower() not in [
                    "null",
                    "none",
                    "undefined",
                ]:
                    if len(clean_code) < 30:
                        coupons.append(clean_code.upper())

        final_results = list(set(coupons))
        return final_results
    except Exception as e:
        logger.error(f"Error rebates fetching {url}: {e}")
        return []


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
    coupons = []

    if not links:
        return []

    for url in links:
        logger.info(f"Scraping RetailMeNot: {url}")

        html = getZenResponseAlter(url, js_render=True, isProxy=True)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        coupon_elements = soup.select('a[data-content-datastore="offer"]')

        dynamic_ids = list(
            set(
                el.get("data-content-uuid")
                for el in coupon_elements
                if el.get("data-content-uuid")
            )
        )

        eventReferenceId = "ee2f6e04-cbc5-4b53-b93c-fb0eb132fbf3"

        for el in dynamic_ids:
            page_url = (
                f"{url}?u={el}&outclicked=true&eventReferenceId={eventReferenceId}"
            )
            logger.info("page url is", page_url)

            html2 = getZenResponseAlter(page_url, js_render=True, isProxy=True)

            if not html2:
                continue

            soup2 = BeautifulSoup(html2, "html.parser")
            code_divs = soup2.select(
                "div.relative.mb-2.flex.h-12.w-full.items-center.justify-center.overflow-hidden.rounded-3xl.bg-gray-100.text-base.font-bold.leading-none.tracking-wider.text-purple-700"
            )

            if code_divs:
                for div in code_divs:
                    code = div.get_text(strip=True)
                    if code:
                        coupons.append(code)
                break

    return list(set(code.upper() for code in coupons if code))


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
    """
    Fetch coupons from JoinHoney by selecting aria-label elements.
    Args: links (list) - List of JoinHoney page URLs
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

            coupon_elements = soup.select('[aria-label="Coupon Code"]')
            for element in coupon_elements:
                coupon = element.text.strip()
                if coupon:
                    coupons.append(coupon.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error joinhoney fetching {url}: {e}")
        return []


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
    """
    Fetch coupons from DontPayFull using async methods and modal extraction.
    Args: links (list) - List of DontPayFull page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    coupons = []
    try:
        if not links:
            return []

        # remove whitespace
        urls = [link.strip() for link in links]
        client = zenrowsClient()
        responses = await asyncio.gather(*[fetch_page(client, url) for url in urls])

        coupon_elements = []
        coupon_urls = []
        for response in responses:
            if response:
                html = response  # fetch_page already returns the text content
                soup = BeautifulSoup(html, "html.parser")
                coupon_elements = soup.select('.card.obox.code[data-otype="code"]')
                for element in coupon_elements:
                    idAttribute = element.get_attribute_list("data-id")[0]
                    classAttribute = element.get_attribute_list("class")[0]
                    if "sponsored" not in classAttribute:
                        coupon_urls.append(
                            f"https://www.dontpayfull.com/coupons/getcoupon?id={idAttribute}"
                        )
        try:
            responses = await asyncio.gather(
                *[fetch_page(client, url) for url in coupon_urls]
            )

            for response in responses:
                html2 = response  # fetch_page already returns the text content
                soup2 = BeautifulSoup(html2, "html.parser")

                coupon_elements2 = soup2.select(".code-box.code h2")
                for element2 in coupon_elements2:
                    code = element2.text.strip()
                    if code is not None:
                        coupons.append(code.upper())
        except Exception as e:
            logger.error(f"Error dontpayfull fetching {coupon_urls}: {e}")
            return []
        return list(set(coupons))
    except Exception as e:
        logger.error(f"Error dontpayfull fetching {links}: {e}")
        return []


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
    """
    Fetch coupons from Offers.com using async methods and modal extraction.
    Args: links (list) - List of Offers.com page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    coupons = []
    try:
        if not links:
            return []

        # remove whitespace
        urls = [link.strip() for link in links]
        coupon_urls = []
        client = zenrowsClient()
        responses = await asyncio.gather(*[fetch_page(client, url) for url in urls])

        for url in urls:
            for response in responses:
                if response:
                    html = response  # fetch_page already returns the text content
                    soup = BeautifulSoup(html, "html.parser")
                    elements1 = soup.select(
                        '[data-offer-id] > [data-content-datastore="offer"]'
                    )

            for element in elements1:
                attribute1 = element.get_attribute_list("x-data")[0]
                attribute2 = element.get_attribute_list("@click.prevent")[0]

                if attribute1:
                    offerId = attribute1.split("'")[1]
                    hasCode = "hasCode: true" in attribute2
                    if hasCode:
                        coupon_urls.append(f"{url}?em={offerId}")

            try:
                logger.info(f"coupon_urls: {coupon_urls}")
                responses = await asyncio.gather(
                    *[fetch_page(client, url) for url in coupon_urls]
                )
                # print(f'responses: {responses}')

                for response in responses:
                    html2 = response  # fetch_page already returns the text content
                    soup2 = BeautifulSoup(html2, "html.parser")
                    elements2 = soup2.select('[x-data="commerceModal"] [x-data]')
                    for element2 in elements2:
                        codeAttribute = element2.get_attribute_list("x-data")[0]
                        coupon = codeAttribute.split("'")[3]
                        if coupon is not None:
                            coupons.append(coupon.upper())
            except Exception as e:
                logger.error(f"Error fetching coupon from offers: {e}")
                continue

        return list(set(coupons))

    except Exception as e:
        logger.error(f"Error offers fetching {links}: {e}")
        return []


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
    """
    Fetch coupons from Coupons.com using API calls with client ID extraction.
    Args: url (list) - List of Coupons.com page URLs
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
            styleElements = soup.select('[type="text/css"]')
            clientId = None
            for styleElement in styleElements:
                href = styleElement.get("href")
                if (
                    href
                    and "https://www.coupons.com/assets/pico" in href
                    and "style-guide.css" in href
                ):
                    clientId = href.split("pico/")[1].split("/")[0]
                    break

            if clientId:
                coupon_elements = soup.select('[data-attribute="code"]')
                for element in coupon_elements:
                    couponId = element.get_attribute_list("data-id")

                    if len(couponId) == 0:
                        continue
                    try:
                        text = getZenrowsResponse(
                            f"https://www.coupons.com/api/voucher/country/us/client/{clientId}/id/{couponId[0]}"
                        )
                        if text:
                            data = json.loads(text)
                            if "type" in data and data["type"] == "code":
                                code = data["code"]
                                if code:
                                    coupons.append(code.upper())

                    except Exception as e:
                        logger.error(
                            f"[fetch_coupons_from_coupons] Error scraping coupons: {e}"
                        )
                        continue
        return coupons
    except Exception as e:
        logger.error(f"Error coupons fetching {url}: {e}")
        return []


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
    """
    Fetch coupons from SimplyCodes using API endpoint.
    Args: links (list) - List of SimplyCodes page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for link in urls:
            url = f'https://simplycodes.com/api/promotion/mdp/codes?slug={link.split("/")[-1]}&filter=all&showFallback=true&extras=detailsCombined'
            response = getZenrowsResponse(url, js_render=True)
            jsondata = json.loads(response)

            if "promotions" in jsondata:
                promotions = jsondata["promotions"]
                for promo in promotions:

                    if promo.get("isCode") == True and "code" in promo:
                        code = promo["code"].upper().strip()
                        if code:
                            coupons.append(code)

        return coupons

    except Exception as e:
        logger.error(f"Error simplycodes fetching {urls}: {e}")
        return []


def fetch_coupons_from_savings(links):
    """
    Fetch coupons from Savings.com using async methods and popup extraction.
    Args: links (list) - List of Savings.com page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    coupons = []
    try:
        if not links:
            return []
        urls = [link.strip() for link in links]  # remove whitespace

        for url in urls:
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            coupon_elements = soup.select(
                ".module-deal.showValue.codePeek[data-offer-id]"
            )
            for element in coupon_elements:
                attribute = element.get("id")
                if not attribute:
                    continue
                couponId = attribute.split("-")[-1]
                html2 = getZenrowsResponse(
                    f"https://www.savings.com/popup/detail/coupon-{couponId}.html",
                    js_render=True,
                )

                soup2 = BeautifulSoup(html2, "html.parser")
                coupon_elements2 = soup2.select_one("input.code.code-border[value]")
                if coupon_elements2:
                    code = coupon_elements2.get("value")
                    coupons.append(code)

        return list(set(coupons))
    except Exception as e:
        logger.error(f"Error savings fetching {links}: {e}")
        return []


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
    """
    Fetch coupons from PromoCodes by parsing __NEXT_DATA__ and regex.
    Args: links (list) - List of PromoCodes page URLs
    Returns: list - List of filtered uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []

        for url in urls:
            try:
                html = getZenrowsResponse(url, js_render=True)
                if not html:
                    continue

                soup = BeautifulSoup(html, "html.parser")
                next_data_script = soup.select_one("script#__NEXT_DATA__")

                if next_data_script:
                    json_data = json.loads(next_data_script.string)

                    def find_coupons(obj):
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k == "couponCode" and v:
                                    coupons.append(v.upper())
                                else:
                                    find_coupons(v)
                        elif isinstance(obj, list):
                            for item in obj:
                                find_coupons(item)

                    find_coupons(json_data)

                regex_codes = re.findall(r'"couponCode"\s*:\s*"([^"]+)"', html)
                coupons.extend([c.upper() for c in regex_codes if c])
            except Exception as e:
                logger.error(f"Error")

        final_coupons = list(set(coupons))

        garbage = ["NULL", "NONE", "VERIFIED", "TRUE", "FALSE"]
        final_coupons = [c for c in final_coupons if c not in garbage and len(c) > 2]
        return final_coupons
    except Exception as e:
        logger.error(f"Error promocodes fetching {e}")
        return []


def fetch_coupons_from_couponchief(links):
    """
    Fetch coupons from CouponChief using overlay extraction.
    Args: links (list) - List of CouponChief page URLs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]
        coupons = []

        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            coupon_elements = soup.select(".coupon-code-container.store-code")

            for element in coupon_elements:
                attribute = element.get_attribute_list("id")
                if len(attribute) == 0:
                    continue

                try:
                    couponId = attribute[0].split("-")[-1]
                    html2 = getZenrowsResponse(
                        f"https://www.couponchief.com/pages/coupon_overlay/{couponId}"
                    )
                    soup2 = BeautifulSoup(html2, "html.parser")
                    coupon_elements2 = soup2.select("input#coupon-code")

                    for element2 in coupon_elements2:
                        coupon = element2.get("value")
                        if coupon:
                            coupons.append(coupon.upper())

                except Exception as e:
                    logger.error(f"Error scraping Couponchief overlay: {e}")
                    continue

        unique_coupons = list(set(coupons))
        logger.info(f"Found {len(unique_coupons)} unique coupons from CouponChief")
        return unique_coupons

    except Exception as e:
        logger.error(f"Error couponchief fetching: {e}")
        return []


def fetch_coupons_from_goodshop(links):
    """
    Fetch coupons from GoodShop by extracting data-clipboard-text attributes.
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]
        coupons = []

        for url in urls:
            html = getZenrowsResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            coupon_elements = soup.select("[data-clipboard-text]")

            for element in coupon_elements:
                code = element.get_attribute_list("data-clipboard-text")[0]
                if code:
                    coupons.append(code)

        return list(set(coupons))

    except Exception as e:
        logger.error(f"Error goodshop fetching {e}")
        return []


# Helper function: Validate coupon code format
def _is_valid_tenereteam_code(code):
    """
    Check if code matches Tenereteam coupon format.
    Args: code (str) - Code to validate
    Returns: bool - True if valid format, False otherwise
    """
    if not code:
        return False
    return code.isupper() and 3 <= len(code) <= 15 and " " not in code


# Method 1: Extract coupons from strong tags
def _extract_tenereteam_strong_tags(soup):
    """
    Extract coupon codes from strong tags.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        for strong_tag in soup.find_all("strong"):
            code = strong_tag.get_text(strip=True)
            if _is_valid_tenereteam_code(code):
                coupons.append(code)
    except Exception as e:
        logger.debug(f"Tenereteam strong tags extraction failed: {e}")
    return coupons


# Method 2: Extract coupons from input elements
def _extract_tenereteam_input_elements(soup):
    """
    Extract coupon codes from input elements with values.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        for box in soup.find_all("input", value=True):
            val = box["value"].strip()
            if _is_valid_tenereteam_code(val):
                coupons.append(val)
    except Exception as e:
        logger.debug(f"Tenereteam input elements extraction failed: {e}")
    return coupons


# Method 3: Extract coupons from button/div elements with code/coupon classes
def _extract_tenereteam_button_div_elements(soup):
    """
    Extract coupon codes from button/div elements with code/coupon classes.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        for btn in soup.find_all(
            ["button", "div"], class_=re.compile(r"code|coupon", re.I)
        ):
            btn_text = btn.get_text(strip=True)
            if _is_valid_tenereteam_code(btn_text):
                coupons.append(btn_text)
    except Exception as e:
        logger.debug(f"Tenereteam button/div elements extraction failed: {e}")
    return coupons


# Main function: fetch coupons from tenereteam
def fetch_coupons_from_tenereteam(links):
    """
    Fetch coupons from Tenereteam using multiple extraction methods.
    Args: links (list) - List of Tenereteam page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []

        for url in urls:
            try:
                html = getZenrowsResponse(url)
                if not html:
                    continue
                soup = BeautifulSoup(html, "html.parser")
                scripts = soup.find_all("script")
                for s in scripts:
                    txt = s.get_text(strip=True)
                    if "__next_f.push" not in txt:
                        continue

                    matches = re.findall(r'\\"coupon_code\\"\s*:\s*\\"([^"]+)\\"', txt)

                    for code in matches:
                        coupons.append(code)

            except Exception as e:
                logger.error(f"Error scraping tenereteam for {url}: {e}")
                continue

        # Return unique coupons after processing all URLs
        unique_coupons = list(set(coupons))
        if unique_coupons:
            logger.info(f"Found {len(unique_coupons)} unique coupons from Tenereteam")
        return unique_coupons

    except Exception as e:
        logger.error(f"Error tenereteam fetching: {e}")
        return []


def fetch_coupons_from_couponcause(links):
    """
    Fetch coupons from CouponCause by selecting div elements.
    Args: links (list) - List of CouponCause page URLs
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
            selector = "div.bg-grey-lighter.border.border-grey-lighter.text-grey-dark.text-right"
            coupon_elements = soup.select(selector)
            for element in coupon_elements:
                code = element.get_text(strip=True)
                code = code.replace("Show Code", "").strip()
                if code and code != "Get Offer" and len(code) > 0:
                    coupons.append(code)

        final_coupons = list(set(coupons))
        return final_coupons
    except Exception as e:
        logger.error(f"Error couponcause fetching {e}")
        return []


def fetch_coupons_from_goodsearch(links):
    """
    Fetch coupons from GoodSearch by parsing __NEXT_DATA__.
    Args: links (list) - List of GoodSearch page URLs
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
            next_data = soup.select_one("script#__NEXT_DATA__")
            if next_data:
                json_data = json.loads(next_data.string)
                if "props" in json_data:
                    props = json_data["props"]
                    if "pageProps" in props:
                        pageProps = props["pageProps"]
                        if "merchant" in pageProps:
                            deals = pageProps["merchant"]
                            if "activeDeals" in deals:
                                for item in deals["activeDeals"]:
                                    coupon = item["code"]
                                    if coupon:
                                        coupons.append(coupon.upper())
        return coupons
    except Exception as e:
        logger.error(f"Error goodsearch fetching {e}")
        return []


def fetch_coupons_from_valuecom(links):
    """
    Fetch coupons from Value.com by selecting button code elements.
    Args: links (list) - List of Value.com page URLs
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
            coupon_elements = soup.select("button.btn-code .code-text")
            for element in coupon_elements:
                code = element.text.strip()
                # need to check the code is not made up with special characters only like "****" or "$$$$$"
                if code and not re.match(r"^[\*\s\.\-]+$", code):
                    coupons.append(code.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error valuecom fetching {e}")
        return []


def fetch_coupons_from_lovecoupons(links):
    """
    Fetch coupons from LoveCoupons using modal extraction.
    Args: links (list) - List of LoveCoupons page URLs
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
            coupon_elements = soup.select('article.Offer[data-type="2"]')
            for element in coupon_elements:
                attribute = element.get_attribute_list("data-id")
                if len(attribute) == 0:
                    continue
                try:
                    html2 = getZenrowsResponse(
                        f"https://www.lovecoupons.com/go/3/{attribute[0]}"
                    )
                    soup2 = BeautifulSoup(html2, "html.parser")
                    coupon_elements2 = soup2.select(".RevealCoupon input.block")

                    for element2 in coupon_elements2:
                        coupon = element2.get("value")
                        if coupon:
                            coupons.append(coupon.upper())

                except Exception as e:
                    logger.error(f"Error scraping lovecoupons: {e}")
                    continue

        return coupons
    except Exception as e:
        logger.error(f"Error lovecoupons fetching {e}")
        return []


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
    """
    Fetch coupons from RebatesMe using API POST request.
    Args: links (list) - List of RebatesMe API URLs with merchant IDs
    Returns: list - List of uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace

        coupons = []
        for url in urls:
            apiUrl = url.split("?")[0]
            merchantId = url.split("=")[1]

            data = {
                "pageNo": 1,
                "pageSize": 1000,
                "merchantId": merchantId,
            }

            text, status = postZenrowsResponse(apiUrl, data=json.dumps(data))
            if text:
                entity = json.loads(text)
                if "data" in entity:
                    data = entity["data"]
                    if "coupons" in data:
                        items = data["coupons"]
                        for item in items:
                            if "couponCodeList" in item:
                                codes = item["couponCodeList"]
                                for code in codes:
                                    if "code" in code:
                                        coupon = code["code"]
                                        if coupon:
                                            coupons.append(coupon.upper())
        return coupons
    except Exception as e:
        logger.error(f"Error rebatesme fetching {e}")
        return []


def fetch_coupons_from_refermate(links):
    """
    Fetch coupons from ReferMate by extracting data-clipboard-text.
    Args: links (list) - List of ReferMate page URLs
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
            matches = re.findall(r'\\"couponCode\\":\\"([^"]+)\\"', html)
            coupons.extend(matches)

        return list(set(coupons))
    except Exception as e:
        logger.error(f"Error refermate fetching {e}")
        return []


def fetch_coupons_from_discountreactor(links):
    """
    Fetch coupons from DiscountReactor by extracting data-code attributes.
    Args: links (list) - List of DiscountReactor page URLs
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
            coupon_elements = soup.select(".offer-list-item-button_hidden-code")
            for element in coupon_elements:
                code = element.get_attribute_list("data-code")
                if len(code) == 0:
                    continue
                coupons.append(code[0].strip().upper())

        return coupons
    except Exception as e:
        logger.error(f"Error discountreactor fetching {e}")
        return []


def is_valid_coupon(code: str) -> bool:
    code = code.strip()

    if len(code) < 4 or len(code) > 20:
        return False

    if " " in code:
        return False

    blacklist = {
        "sign",
        "receive",
        "discount",
        "shop",
        "email",
        "activate",
        "deal",
        "save",
    }

    lowered = code.lower()
    return not any(word in lowered for word in blacklist)


def fetch_coupons_from_couponbox(links):
    """
    Fetch coupons from CouponBox using voucher API.
    Args:
        links (list): List of CouponBox page URLs
    Returns:
        list: List of valid uppercase coupon codes
    """

    if not links:
        return []

    coupons = []
    urls = [link.strip() for link in links]

    for url in urls:
        try:
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            voucher_elements = soup.select(
                '[data-testid="VouchersListItem"][class*="VouchersListItem_root_type-voucher"]'
            )

            for element in voucher_elements:
                voucher_ids = element.get_attribute_list("data-voucherid")
                if not voucher_ids:
                    continue

                api_url = f"https://www.couponbox.com/api/voucher/{voucher_ids[0]}"
                response = getZenrowsResponse(api_url)
                if not response:
                    continue

                data = json.loads(response)
                code = data.get("code")

                if (
                    data.get("codeType") == "copy_paste"
                    and code
                    and is_valid_coupon(code)
                ):
                    coupons.append(code.strip().upper())

        except Exception as e:
            logger.error(f"Error fetching CouponBox data from {url}: {e}")

    return list(set(coupons))


# Helper function: Get exclude list for dealdrop
def _get_dealdrop_exclude_list():
    """
    Get list of excluded terms for dealdrop coupon extraction.
    Returns: list - List of strings to exclude from coupon codes
    """
    return [
        "FFFFFF",
        "ABOUT",
        "CONTACT",
        "DEAL",
        "DROP",
        "HTML",
        "SVELTE",
        "TRUE",
        "FALSE",
        "NULL",
        "WIDTH",
        "HEIGHT",
        "LOGO",
        "IMAGE",
        "UTF8",
        "DECEMBER",
        "VIEWPORT",
    ]


# Method 1: Extract coupons using regex pattern matching
def _extract_dealdrop_regex_coupons(html_content):
    """
    Extract coupons from HTML content using regex pattern matching.
    Args: html_content (str) - Raw HTML content
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        pattern = r"\b[A-Z]{3,10}[0-9]{0,4}\b"
        raw_matches = re.findall(pattern, html_content)
        exclude_list = _get_dealdrop_exclude_list()

        for code in raw_matches:
            if code not in exclude_list and len(code) >= 4:
                # Must have digits or be at least 5 characters
                if any(char.isdigit() for char in code) or len(code) >= 5:
                    if code.upper() not in coupons:
                        coupons.append(code.upper())
    except Exception as e:
        logger.debug(f"Dealdrop regex extraction failed: {e}")
    return coupons


# Method 2: Extract coupons from script tags
def _extract_dealdrop_script_coupons(soup):
    """
    Extract coupons from script tags containing specific keywords.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        exclude_list = _get_dealdrop_exclude_list()
        scripts = soup.find_all("script")

        for script in scripts:
            if script.string and (
                "HALLMARK" in script.string or "WANDER" in script.string
            ):
                matches = re.findall(r'"([A-Z0-9]{4,15})"', script.string)
                for match in matches:
                    if match not in exclude_list and match not in coupons:
                        coupons.append(match)
    except Exception as e:
        logger.debug(f"Dealdrop script extraction failed: {e}")
    return coupons


# Method 3: Extract expected coupon codes
def _extract_dealdrop_expected_coupons(html_content):
    """
    Extract known expected coupon codes from HTML content.
    Args: html_content (str) - Raw HTML content
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        expected_codes = [
            "HALLMARK20",
            "CARLOSANDHEATH20",
            "WANDER25",
            "NUD10",
            "CARE25",
            "MATT20",
        ]

        for expected in expected_codes:
            if expected in html_content and expected not in coupons:
                coupons.append(expected)
    except Exception as e:
        logger.debug(f"Dealdrop expected codes extraction failed: {e}")
    return coupons


# Main function: fetch coupons from dealdrop
def fetch_coupons_from_dealdrop(links):
    """
    Fetch coupons from DealDrop using multiple extraction methods.
    Args: links (list) - List of DealDrop page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons_list = []

        for url in urls:
            try:
                html_content = getZenrowsResponse(url)
                if not html_content:
                    continue

                pattern = r"\b[A-Z]{3,10}[0-9]{0,4}\b"
                raw_matches = re.findall(pattern, html_content)
                exclude_list = [
                    "FFFFFF",
                    "ABOUT",
                    "CONTACT" "DEAL",
                    "DROP",
                    "HTML",
                    "SVELTE",
                    "TRUE",
                    "FALSE",
                    "NULL",
                    "WIDTH",
                    "HEIGHT",
                    "LOGO",
                    "IMAGE",
                    "UTF8",
                    "DECEMBER",
                    "VIEWPORT",
                ]

                for code in raw_matches:
                    if code not in exclude_list and len(code) >= 4:
                        if any(char.isdigit() for char in code) or len(code) >= 5:
                            if code not in coupons_list:
                                coupons_list.append(code.upper())

                # Method 2: Script tag parsing
                soup = BeautifulSoup(html_content, "html.parser")
                scripts = soup.find_all("script")

                for script in scripts:
                    if script.string and (
                        "HALLMARK" in script.string or "WANDER" in script.string
                    ):

                        matches = re.findall(r'"([A-Z0-9]{4,15})"', script.string)
                        for m in matches:
                            if m not in exclude_list and m not in coupons_list:
                                coupons_list.append(m)
                expected_codes = [
                    "HALLMARK20",
                    "CARLOSANDHEATH20",
                    "WANDER25",
                    "NUD10",
                    "CARE25",
                    "MATT20",
                ]

                for expected in expected_codes:
                    if expected in html_content and expected not in coupons_list:
                        coupons_list.append(expected)

            except Exception as e:
                logger.error(f"Error scraping dealdrop for {url}: {e}")
                continue

        # Return unique coupons after processing all URLs
        unique_coupons = list(set(coupons_list))
        if unique_coupons:
            logger.info(f"Found {len(unique_coupons)} unique coupons from DealDrop")
        return unique_coupons

    except Exception as e:
        logger.error(f"Error dealdrop fetching: {e}")
        return []


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
    """
    Fetch coupons from DazzDeals using modal extraction with coupon IDs.
    Args: links (list) - List of DazzDeals page URLs
    Returns: list - List of uppercase coupon codes
    """
    all_coupons = []

    try:
        if not links:
            return []

        urls = [link.strip() for link in links]

        for url in urls:
            cp_values = []

            html = getZenResponseAlter(url, js_render=True, isProxy=True)
            if not html:
                logger.warning(f"Failed to fetch DazzDeals page: {url}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select("[data-cp]")

            for el in elements:
                cp = el.get("data-cp")
                if cp:
                    cp_values.append(cp)

            unique_cp_values = list(set(cp_values))
            logger.info(f"Found {len(unique_cp_values)} unique cp values for {url}")
            for cp in unique_cp_values:
                try:
                    cp_url = f"{url}/?cp={cp}"
                    html2 = getZenResponseAlter(cp_url, js_render=True, isProxy=True)

                    if not html2:
                        continue

                    soup2 = BeautifulSoup(html2, "html.parser")
                    code_div = soup2.find("div", id="ccode")

                    if code_div:
                        code = code_div.get_text(strip=True)
                        if code:
                            all_coupons.append(code.upper())

                except Exception as e:
                    logger.error(f"Error fetching cp modal {cp} from {url}: {e}")
                    continue

        unique_coupons = list(set(all_coupons))
        logger.info(f"Total unique coupons from DazzDeals: {len(unique_coupons)}")
        return unique_coupons

    except Exception as e:
        logger.error(f"Error dazzdeals fetching: {e}")
        return []


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
    """
    Fetch coupons from CouponBind using cookies and proxy.
    Args: links (list) - List of CouponBind page URLs
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
            coupon_elements = soup.select(".item-code .hiddenCode")

            for element in coupon_elements:
                code = element.text.strip()
                if code is not None:
                    coupons.append(code.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error couponbind fetching {e}")
        return []


def fetch_coupons_from_promopro(links):
    """
    Fetch coupons from PromoPro by selecting button code elements.
    """
    all_final_coupons = []

    try:
        if not links:
            return []

        urls = [link.strip() for link in links]

        for url in urls:
            promo_ids = []
            parsed = urlparse(url)
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) < 2:
                logger.warning(f"Invalid URL structure for: {url}")
                continue

            store_slug = path_parts[-1]
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            articles = soup.find_all(
                "article", class_="offer_card detail_filter_all detail_filter_code"
            )

            for art in articles:
                a_tag = art.find("a", class_="go_crd crd", attrs={"data-cid": True})
                if a_tag:
                    promo_ids.append(a_tag["data-cid"])

            for pid in promo_ids:
                promo_url = (
                    f"https://www.promopro.com/coupon-codes/{store_slug}?promoid={pid}"
                )
                p_html = getZenrowsResponse(promo_url, js_render=True)

                if not p_html:
                    continue

                p_soup = BeautifulSoup(p_html, "html.parser")
                code_div = p_soup.find("div", id="codeText")

                if code_div:
                    code = code_div.get_text(strip=True)
                    if code:
                        all_final_coupons.append(code)

        unique_coupons = list(dict.fromkeys(all_final_coupons))
        logger.info(f"Total unique coupons found: {len(unique_coupons)}")
        return unique_coupons

    except Exception as e:
        logger.error(f"Error in fetch_coupons_from_promopro: {e}")
        return []


# Method 1: Extract coupons from data-clipboard-text attribute
def _extract_greenpromocode_clipboard_coupons(soup):
    """
    Extract coupon codes from elements with data-clipboard-text attribute.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        coupon_elements = soup.select(".code[data-clipboard-text]")
        for element in coupon_elements:
            code = element.get("data-clipboard-text")
            if code:
                clean_code = code.strip().upper()
                if clean_code:
                    coupons.append(clean_code)
    except Exception as e:
        logger.debug(f"Greenpromocode clipboard extraction failed: {e}")
    return coupons


# Method 2: Extract coupons from description elements using regex
def _extract_greenpromocode_description_coupons(soup):
    """
    Extract coupon codes from description elements using regex pattern.
    Args: soup (BeautifulSoup) - Parsed HTML page
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        desc_elements = soup.select(".description")
        for desc in desc_elements:
            # Match text within quotes (various quote styles)
            match = re.search(r'[""\'\'](.*?)[""\'\']', desc.get_text())
            if match:
                code = match.group(1).strip().upper()
                if code:
                    coupons.append(code)
    except Exception as e:
        logger.debug(f"Greenpromocode description extraction failed: {e}")
    return coupons


# Main function: fetch coupons from greenpromocode
def fetch_coupons_from_greenpromocode(links):
    """
    Fetch coupons from GreenPromoCode using multiple extraction methods.
    Args: links (list) - List of GreenPromoCode page URLs
    Returns: list - List of unique uppercase coupon codes
    """
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []

        for url in urls:
            try:
                html = getZenrowsResponse(url, isProxy=True)
                if not html:
                    continue

                soup = BeautifulSoup(html, "html.parser")
                desc_elements = soup.select(".description")
                for desc in desc_elements:
                    match = re.search(r'[“"\'\'](.*?)[”"\'\']', desc.get_text())
                    if match:
                        coupons.append(match.group(1).strip().upper())

            except Exception as e:
                logger.error(f"Error scraping greenpromocode for {url}: {e}")
                continue

        # Return unique coupons after processing all URLs
        unique_coupons = list(set(coupons))
        if unique_coupons:
            logger.info(
                f"Found {len(unique_coupons)} unique coupons from GreenPromoCode"
            )
        return unique_coupons

    except Exception as e:
        logger.error(f"Error greenpromocode fetching: {e}")
        return []


def fetch_coupons_from_deala(links):
    """
    Fetch coupons from DealA by parsing serverApp-state JSON data.
    Args: links (list) - List of DealA page URLs
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
            coupon_elements = soup.select("#serverApp-state")

        for element in coupon_elements:
            text = element.text.strip()
            if text is not None:
                textData = unescape(text)
                decoded = textData.replace("&q;", '"')
                data = {}
                try:
                    data = json.loads(decoded)
                except json.JSONDecodeError as e:
                    logger.error(f"[fetch_coupons_from_deala]Error scraping: {e}")

                if "store-view-page-coupons-highlight" in data:
                    jsonData = data["store-view-page-coupons-highlight"]
                    if "result" in jsonData:
                        if "data" in jsonData["result"]:
                            items = jsonData["result"]["data"]
                            for item in items:
                                if "code" in item:
                                    code = item["code"]
                                    if code:
                                        coupons.append(code.upper())
                if "store-view-page-coupons-other" in data:
                    jsonData = data["store-view-page-coupons-other"]
                    if "result" in jsonData:
                        if "data" in jsonData["result"]:
                            items = jsonData["result"]["data"]
                            for item in items:
                                if "code" in item:
                                    code = item["code"]
                                    if code:
                                        coupons.append(code.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error dealA fetching {e}")
        return []


def fetch_coupons_from_troupon(links):
    """
    Fetch coupons from Troupon pages.
    Args:
        links (list): List of Troupon page URLs
    Returns:
        list: List of uppercase coupon codes
    """

    if not links:
        return []

    coupons = []
    urls = [link.strip() for link in links]

    for url in urls:
        try:
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            page_coupons = []

            # Primary selector (itemprop)
            elements = soup.find_all(attrs={"itemprop": "couponCode"})
            for el in elements:
                code = el.get_text(strip=True)
                if code and len(code) < 20:
                    page_coupons.append(code.upper())

            # Fallback selector
            if not page_coupons:
                fallback_elements = soup.select("span.coupon-code")
                for el in fallback_elements:
                    code = el.get_text(strip=True)
                    if code:
                        page_coupons.append(code.upper())

            coupons.extend(page_coupons)

        except Exception as e:
            logger.error(f"Error fetching Troupon data from {url}: {e}")

    return list(set(coupons))


def fetch_coupons_from_worthepenny(links):
    """
    Fetch coupons from WorthEPenny using cookies and proxy with data-code extraction.
    Args: links (list) - List of WorthEPenny page URLs
    Returns: list - List of uppercase coupon codes ..
    """
    coupons = []
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        for url in urls:
            html = getZenrowsResponse(
                url,
                js_render=True,
            )
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.find_all(attrs={"data-code": True})
            for el in elements:
                code = el.get("data-code").strip()
                if code and code.lower() != "none":
                    coupons.append(code.upper())

        return coupons
    except Exception as e:
        logger.error(f"Error worthepenny fetching {e}")
        return coupons
    finally:
        return coupons


def fetch_coupons_from_savingheist(links):
    coupons = []

    if not links:
        return coupons

    for url in [link.strip() for link in links]:
        try:
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            json_scripts = soup.find_all("script", type="application/ld+json")

            for script in json_scripts:
                try:
                    data = json.loads(script.string)
                    if data.get("@type") == "ItemList":
                        for item_wrapper in data.get("itemListElement", []):
                            code = item_wrapper.get("item", {}).get("identifier")
                            if code:
                                coupons.append(code.strip().upper())
                except:
                    continue

        except Exception as e:
            logger.error(f"Error savingheist fetching {url}: {e}")

    return coupons


def fetch_coupons_from_coupongrouphy(links):
    """
    Fetch coupons from CouponGroupHy using cookies and proxy with data-clipboard-text.
    Args: links (list) - List of CouponGroupHy page URLs
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        coupons = []
        for url in urls:
            try:
                html = getZenrowsResponse(url, js_render=True)
                if not html:
                    continue

                soup = BeautifulSoup(html, "html.parser")
                elements = soup.find_all(attrs={"data-clipboard-text": True})

                for el in elements:
                    code = el["data-clipboard-text"].strip()
                    if code:
                        coupons.append(code.upper())

                if not coupons:
                    for item in soup.select(".masked_code, .coupon_code"):
                        coupons.append(item.get_text(strip=True).upper())
            except Exception as e:
                logger.error(f"Error coupongrouphy fetching {url}: {e}")
                continue

        return coupons
    except Exception as e:
        logger.error(f"Error coupongrouphy fetching {e}")
        return coupons

    finally:
        return coupons


def fetch_coupons_from_joincheckmate(links):
    """
    Fetch coupons from JoinCheckmate using modal extraction with code IDs.
    Args: links (list) - List of JoinCheckmate page URLs
    Returns: list - List of uppercase coupon codes
    """
    coupons = []
    try:
        if not links:
            return []

        urls = [link.strip() for link in links]  # remove whitespace
        for url in urls:
            html = getZenrowsResponse(url, js_render=True)
            if not html:
                continue
            raw_ids = re.findall(r'"id":"(01[a-zA-Z0-9]{20,})"', html)

            unique_ids = []
            for i in raw_ids:
                if i not in unique_ids:
                    unique_ids.append(i)

            logger.info(
                f"Found {len(unique_ids)} potential IDs. Now fetching values..."
            )

            for cid in unique_ids:
                try:
                    modal_url = f"{url}/modal?code={cid}"
                    modal_html = getZenrowsResponse(modal_url, js_render=True)
                    code_match = re.search(
                        r'"selectedCode":\{.*?"value":"([^"]+)"', modal_html
                    )

                    if code_match:
                        found_code = code_match.group(1)
                        if "#" not in found_code:
                            coupons.append(found_code)

                except Exception as e:
                    logger.error(f"Error fetching modal for {cid}: {e}")
                    continue

        return coupons
    except Exception as e:
        logger.error(f"Error joincheckmate fetching {e}")
        return []


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
    try:
        if not links:
            return []

        coupon_codes = []

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.google.com",
            "Connection": "keep-alive",
        })
        session.cookies.set("browser_source", "chrome", domain=".dealam.com")
        session.cookies.set("dealam_request_id", "964486544", domain=".dealam.com")

        for url in links:
            logger.info("URL:", url)

            response = session.get(url, timeout=20)
            if not response.ok:
                logger.info(f"DealAM bad status {response.status_code} for {url}")
                continue

            html = response.text
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select("span[data-clipboard-text]")
            for el in elements:
                code = el.get("data-clipboard-text", "").strip()
                if code:
                    coupon_codes.append(code.upper())

        unique = list(set(coupon_codes))
        logger.info(f"Found {len(unique)} unique coupons from DealAM")
        return unique

    except Exception as e:
        logger.error("Error:", e)
        return []

def fetch_coupons_from_coponcodes(links):
    """
    Fetch coupons from CouponCodes using direct request.
    Codes are in: <span id="code-NNNN" hidden>COUPONCODE</span>
    """
    try:
        if not links:
            return []

        coupon_codes = []

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.google.com",
            "Connection": "keep-alive",
        })

        for url in links:
            logger.info("URL:", url)

            response = session.get(url, timeout=20)
            if not response.ok:
                logger.info(f"CouponCodes bad status {response.status_code} for {url}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            spans = soup.find_all("span", id=re.compile(r"^code-\d+$"))
            for span in spans:
                code = span.get_text(strip=True)
                if code:
                    coupon_codes.append(code.upper())

        unique = list(set(coupon_codes))
        logger.info(f"Found {len(unique)} unique coupons from CouponCodes")
        return unique

    except Exception as e:
        logger.error("Error:", e)
        return []       
    
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
    try:
        if not links:
            return []

        coupon_codes = []

        for url in links:
            logger.info("URL:", url)

            html = getZenrowsWithCookiesResponse(url,isProxy=True)
            if not html:
                continue
            
            codes = re.findall(r'"code"\s*:\s*"([^"]+)"', html)

            for code in codes:
                coupon_codes.append(code)

        return list(set(coupon_codes))

    except Exception as e:
        logger.error("exception:", e)
        return []
    
def fetch_coupons_from_xyzcodes(links):
    try:
        if not links:
            return []

        coupon_codes = set() 

        for url in links:
            logger.info("URL:", url)
            html = getZenrowsWithCookiesResponse(url,  isProxy=True)
            if not html:
                continue

            codes = re.findall(r'data-clipboard-text="([^"]+)"', html)

            for code in codes:
                if code:
                    coupon_codes.add(code.strip())

        return list(coupon_codes)

    except Exception as e:
        logger.error("exception:", e)
        return []
    
    
def fetch_coupons_from_couponblender(links):
    try:
        if not links:
            return []

        coupon_codes = []
        
        for url in links:
            logger.info("URL:", url)
            html = getZenrowsWithCookiesResponse(url, isProxy=True)

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select("input.hidden_cv")
            for el in elements:
                code = el.get("value", "").strip()
                if code:
                    coupon_codes.append(code.upper())

        unique = list(set(coupon_codes))
        logger.info(f"Found {len(unique)} unique coupons from CouponBlender")
        return unique

    except Exception as e:
        logger.error("exception:", e)
        return []


def fetch_coupons_from_couponpromo(links):
    try:
        if not links:
            return []

        coupon_codes = set()

        for url in links:
            logger.info("URL:", url)

            html = getZenrowsWithCookiesResponse(url,  isProxy=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            inputs = soup.select('input[name="c-c"]')

            for inp in inputs:
                code = inp.get("value")
                if code:
                    coupon_codes.add(code.strip())

        return list(coupon_codes)

    except Exception as e:
        logger.error("exception:", e)
        return [] 
    
def fetch_coupons_from_shipthedeal(links):
    try:
        if not links:
            return []

        coupon_codes = set()

        for url in links:
            logger.info("URL:", url)

            html = getZenrowsWithCookiesResponse(url,isProxy=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            spans = soup.select("span.hidden[id$='_code']")

            for sp in spans:
                code = sp.get_text(strip=True)

                if not code:
                    continue
       
                if "no code" in code.lower():
                    continue

                if "(" in code or " " in code:
                    continue

                coupon_codes.add(code)

        return list(coupon_codes)

    except Exception as e:
        logger.error("exception:", e)
        return []
    
    
def fetch_coupons_from_couponkiss(links):
    try:
        if not links:
            return []

        coupon_codes = set()

        for url in links:
            logger.info("URL:", url)
            html = getZenrowsWithCookiesResponse(url, isProxy=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select("[data-clipboard-text]")

            for el in elements:
                code = el.get("data-clipboard-text", "").strip()

                if not code:
                    continue

               
                if "no code" in code.lower():
                    continue

                
                if "(" in code:
                    continue

                coupon_codes.add(code)

        return list(coupon_codes)

    except Exception as e:
        logger.error("exception:", e)
        return []
    
    
def fetch_coupons_from__couponterra(links):
    try:
        if not links:
            return []

        coupon_codes = []

        for url in links:
            logger.info("URL:", url)

            html = getZenrowsWithCookiesResponse(url, isProxy=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select("[data-code]")

            for el in elements:
                code = el.get("data-code")
                if code:
                    coupon_codes.append(code.strip())

        return list(set(coupon_codes))

    except Exception as e:
        logger.error("exception:", e)
        return []
    
def fetch_coupons_from_rediscount(links):
    """
    Extract coupon codes only from GET CODE buttons (ignore CLICK DEAL)
    """
    try:
        if not links:
            return []

        coupon_codes = []

        for url in links:
            logger.info("URL:", url)

            html = getZenrowsWithCookiesResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            buttons = soup.select(".coupons_buttons a.coupon_btn")

            for btn in buttons:
                text = btn.get_text(strip=True).upper()

                if text != "GET CODE":
                    continue

                code = btn.get("data-code")
                if code:
                    coupon_codes.append(code.strip().upper())

        return coupon_codes

    except Exception as e:
        logger.error("Error:", e)
        return []
    
def fetch_coupons_from_getscoupon(links):
    """
    Extract coupon codes from getlistr divs (data-code attribute)
    """
    try:
        if not links:
            return []

        coupon_codes = []

        for url in links:
            logger.info("URL:", url)

            html = getZenrowsWithCookiesResponse(url)
            logger.info("the html is", html)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            divs = soup.find_all("div", class_="getlistr")

            for div in divs:
                code = div.get("data-code")
                if code:
                    coupon_codes.append(code.strip().upper())

        return coupon_codes

    except Exception as e:
        logger.error("Error:", e)
        return []
    
    
def fetch_coupons_from_couponsgo(links):
    try:
        if not links:
            return []

        coupon_codes = set()
        cids = set()

        for url in links:
            logger.info("Store URL:", url)

            html = getZenrowsWithCookiesResponse(url, isProxy=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            divs = soup.select("div.coupon-item.c-r-item.couponsgoorg")
            for div in divs:
                cid = div.get("data-value", "").strip()
                if cid:
                    cids.add(cid)
        for cid in cids:
            search_url = (
                f"https://www.couponsgo.org/search?"
                f"query=tommy-john&cid={cid}"
            )


            search_html = getZenrowsResponse(
                search_url, js_render=True, isProxy=True
            )
            if not search_html:
                continue

            search_soup = BeautifulSoup(search_html, "html.parser")

            code_tags = search_soup.select("p.code-str.couponsgoorg")

            for tag in code_tags:
                code = tag.get_text(strip=True)

              
                if not code:
                    continue
                if "no code" in code.lower():
                    continue

                coupon_codes.add(code)

        final_codes = list(coupon_codes)
        logger.info("Final Coupons:", final_codes)
        return final_codes

    except Exception as e:
        logger.error("exception:", e)
        return []
    
    
    
def fetch_coupons_from_promosreview(links):
    try:
        if not links:
            return []

        coupon_codes = []
        
        for url in links:
            logger.info("URL:", url)
            html = getZenrowsWithCookiesResponse(url)
          
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select("[data-code]")
            for el in elements:
                code = el.get("data-code", "").strip()
                if code:
                    coupon_codes.append(code.upper())

        unique = list(set(coupon_codes))
        return unique

    except Exception as e:
        logger.error("exception:", e)
        return []
    
    
def fetch_coupons_from_couponupto(links):
    try:
        if not links:
            return []

        coupon_codes = set()
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
        })

        for url in links:
            logger.info("URL:", url)

            response = session.get(url, timeout=20)
            if not response.ok:
                logger.info(f"CouponUpto bad status {response.status_code} for {url}")
                continue

            html = response.text
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            for a_tag in soup.select("a.a_code"):
                p_tag = a_tag.select_one("p[id^='code-']")
                if not p_tag:
                    continue

                code = p_tag.get_text(strip=True)

                if not code:
                    continue

                if "show" in code.lower():
                    continue

                coupon_codes.add(code.upper())

        logger.info(f"Found {len(coupon_codes)} unique coupons from CouponUpto")
        return list(coupon_codes)

    except Exception as e:
        logger.error("exception:", e)
        return []
    
def fetch_coupons_from_trustdeals(links):
    try:
        if not links:
            return []

        coupon_codes = set()

        for url in links:
            html = getZenrowsWithCookiesResponse(url, isProxy=True)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

          
            code_spans = soup.select("span.code")

            for span in code_spans:
                code = span.get_text(strip=True)

                if not code:
                    continue

                # junk filter (optional but safe)
                if "no code" in code.lower():
                    continue

                coupon_codes.add(code)

        final_codes = list(coupon_codes)
        logger.info("Coupons found:", final_codes)
        return final_codes

    except Exception as e:
        logger.error("exception:", e)
        return []
    
def fetch_coupons_from_gocashback(links):
    """
    Extract coupon codes (last argument) from SEE CODE buttons
    """
    try:
        if not links:
            return []

        coupon_codes = []

        for url in links:
            logger.info("URL:", url)

            html = getZenrowsWithCookiesResponse(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            buttons = soup.find_all("div", class_="go-to-buy")

            for btn in buttons:
                onclick = btn.get("onclick")
                if not onclick:
                    continue

                # Extract LAST argument (coupon code)
                match = re.search(
                    r"goToBuy\(\d+,\d+,'([^']+)'\)",
                    onclick
                )

                if match:
                    code = match.group(1).upper()
                    coupon_codes.append(code)

        
        return coupon_codes

    except Exception as e:
        logger.error("Error:", e)
        return []
    
def fetch_coupons_from_latest_deals(links):
   
    coupons = []
    scraper = cloudscraper.create_scraper()

    headers = {
        "Accept": "application/json",
        "Referer": "https://www.latestdeals.co.uk/",
    }

    for link in links:
        try:
            if not link:
                continue
            
            path_parts = urlparse(link).path.strip("/").split("/")
            if len(path_parts) < 2 or path_parts[0] != "retailers":
                logger.info(f"Invalid LatestDeals URL: {link}")
                continue

            merchant_domain = path_parts[1]

            # Build API URL
            api_url = (
                "https://www.latestdeals.co.uk/api/posts"
                f"?merchantSlugDomain={merchant_domain}"
                "&showExpired=true"
                "&type="
                "&noExcerpts=true"
            )

            # Call API via cloudscraper to bypass Cloudflare
            res = scraper.get(api_url, headers=headers, timeout=20)
            if not res.ok:
                logger.info(f"LatestDeals bad status {res.status_code} for {merchant_domain}")
                continue

            data = res.json()
            entities = data.get("entities", {})

            # Extract coupon codes (only voucher type items with a code)
            for item in entities.values():
                code = item.get("code")
                if code and isinstance(code, str) and item.get("type") == "voucher":
                    coupons.append(code.upper())

        except Exception as e:
            logger.error("LatestDeals error:", e)

    unique_coupons = list(set(coupons))
    logger.error(f"Found {len(unique_coupons)} unique coupons from LatestDeals")
    return unique_coupons

    