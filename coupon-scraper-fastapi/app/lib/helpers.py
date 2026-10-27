import asyncio
import json
import os
import re
from urllib.request import Request, urlopen

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from zenrows import ZenRowsClient

from app.config import settings
from app.lib.logger import get_logger

client = ZenRowsClient("fa0f561cee2fa9ba7976f152b852a663fdcd8791")

ua = UserAgent()

# set log level
logger = get_logger(__name__)

cookies = None


# Helper function to extract coupons from HTML
def extract_coupons_from_html(html):
    soup = BeautifulSoup(html, "html.parser")

    # Locate the script containing the JSON data
    script_tag = soup.find("script", string=lambda t: t and "window.initialState" in t)
    if not script_tag:
        return []

    # Extract JSON using regex
    match = re.search(r"window\.initialState\s*=\s*({.*?});", script_tag.text)
    if not match:
        return []

    try:
        json_data = json.loads(match.group(1))
        return parse_coupons_from_json(json_data)
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON.")
        return []


# Helper function to parse coupon codes from JSON
def parse_coupons_from_json(json_data):
    coupons_info = (
        json_data.get("StorePage", {})
        .get("site", {})
        .get("couponsInfo", {})
        .get("coupons", [])
    )

    return [item["code"].upper() for item in coupons_info if "code" in item]


# Helper function to scrape a URL using requests
def scrape_with_requests(url, headers=None):
    try:
        if not headers:
            headers = headers

        response = requests.get(url, headers=headers)
        response.raise_for_status()  # raise an error if the request failed
        return response.text
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return ""


def fetchRequest(url):
    try:
        headers = {"User-Agent": ua.random}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error fetchRequest {url}: {e}")
        return None


def fetchURL(url):
    try:
        headers = {"User-Agent": ua.random}
        request = Request(url, headers=headers)
        response = urlopen(request)
        return response.read().decode("utf-8")
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None


def discountimeHeaders():
    return {"User-Agent": ua.random, "Content-Type": "application/json"}


# Helper ZenRows Response
def getZenrowsWithCookiesResponse(
    url, is_json=False, isCookies=False, isProxy=False, headers=None, isJsRender=False
):
    try:
        global cookies
        params = {
            "apikey": settings.ZENROWS_API_KEY,
            "url": url,
            "custom_headers": "true",
        }
        headers = {"Referer": "https://www.google.com"}
        if isProxy:
            params["premium_proxy"] = "true"
            params["proxy_country"] = "us"
        if isJsRender:
            params["js_render"] = "true"

        if isCookies and not cookies:
            response = requests.get(
                "https://api.zenrows.com/v1/",
                params=params,
                headers=headers,
                timeout=60,
            )
            cookies = response.cookies
        csheaders = {
            "Referer": "https://www.google.com",
            **(headers or {}),
            **({"Cookies": cookies} if isCookies and cookies else {}),
        }

        response = requests.get(
            "https://api.zenrows.com/v1/", params=params, headers=csheaders, timeout=60
        )
        if is_json:
            return response
        else:
            return response.text
    except Exception as e:
        logger.error(f"Error fetching URL with ZenRows: {e}")
        return None


def getZenrowsResponse(url, is_json=False, isProxy=False, js_render=False):
    try:
        params = {"apikey": settings.ZENROWS_API_KEY, "url": url}
        if isProxy:
            params["premium_proxy"] = "true"
            params["premium_proxy_country"] = "us"

        if js_render:
            params["js_render"] = "true"

        response = requests.get("https://api.zenrows.com/v1/", params=params)
        if is_json:
            return response
        else:
            return response.text
    except Exception as e:
        logger.error(f"Error fetching URL with ZenRows: {e}")
        return None


def getZenResponseAlter(
    url,
    headers=None,
    is_json=False,
    isProxy=False,
    js_render=False,
):

    try:
        params = {
            "premium_proxy": "true",
            "proxy_country": "us",
        }

        if js_render:
            params["js_render"] = "true"

        csheaders = {
            "Referer": "https://www.google.com",
            **(headers or {}),
        }

        response = (
            client.get(url, headers=csheaders, params=params)
            if isProxy
            else client.get(url, headers=csheaders)
        )

        if is_json:
            return response
        return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None


# Helper function to POST data to a URL
def postZenrowsResponse(url, data=None, headers=None, isProxy=False):
    try:

        # zenrows client
        default_headers = {
            "Referer": "https://www.google.com",
            "Content-Type": "application/json",
        }

        # Merge custom headers with default headers
        if headers:
            default_headers.update(headers)

        params = {"apikey": settings.ZENROWS_API_KEY, "url": url}

        # Add proxy parameters if requested
        if isProxy:
            params["premium_proxy"] = "true"
            params["proxy_country"] = "us"

        response = requests.post(
            "https://api.zenrows.com/v1/",
            params=params,
            data=data,
            headers=default_headers,
            timeout=60,
        )
        # Ensure we always return exactly 2 values
        response_text = response.text if hasattr(response, "text") else str(response)
        status_code = response.status_code if hasattr(response, "status_code") else None
        return response_text, status_code
    except Exception as e:
        logger.error(f"Error posting data to {url}: {e}")
        return None, None


# Helper function to create a ZenRows client
def zenrowsClient():
    logger.info(f"settings.ZENROWS_API_KEY: {settings.ZENROWS_API_KEY}")
    api_key = settings.ZENROWS_API_KEY
    client = ZenRowsClient(apikey=api_key, concurrency=5, timeout=60, retries=1)
    return client


# Helper function to fetch a page using a ZenRows client
async def fetch_page(client, url, params=None, headers=None):
    try:
        response = await client.get_async(url, params=params, headers=headers)
        # Extract text from response object if needed
        if hasattr(response, "text"):
            return response.text
        elif hasattr(response, "content"):
            return response.content.decode("utf-8")
        elif isinstance(response, str):
            return response
        else:
            # Try to convert to string
            return str(response)
    except Exception as e:
        logger.error(f"Error fetching page {url}: {e}")
        return None


def getZenrowsTwoResponse(
    url, headers=None, isJson=False, isProxy=False, isJsRender=False
):
    try:
        csheaders = {"Referer": "https://www.google.com", **(headers or {})}
        params = {"apikey": settings.ZENROWS_API_KEY, "url": url}
        if isProxy:
            params["premium_proxy"] = "true"
            params["proxy_country"] = "us"

        if isJsRender:
            params["js_render"] = "true"

        response = requests.get(
            "https://api.zenrows.com/v1/", params=params, headers=csheaders
        )

        if isJson:
            return [response, response.status_code]
        else:
            return [response.text, response.status_code]
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return [None, None]


# Helper function to create a ZenRows client
def zenrowsClient():
    api_key = settings.ZENROWS_API_KEY
    client = ZenRowsClient(apikey=api_key, concurrency=5, retries=1)
    return client


# Helper function to fetch a page using a ZenRows client
async def fetch_page(client, url, params=None, headers=None):
    try:
        response = await client.get_async(url, params=params, headers=headers)
        # Extract text from response object if needed
        if hasattr(response, "text"):
            return response.text
        elif hasattr(response, "content"):
            return response.content.decode("utf-8")
        elif isinstance(response, str):
            return response
        else:
            # Try to convert to string
            return str(response)
    except Exception as e:
        logger.error(f"Error fetching page {url}: {e}")
        return None
