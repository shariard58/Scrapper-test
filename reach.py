"""Check whether ZenRows can reach a site, trying one parameter set at a time.

Usage: set URL and EXPECT below, then run `python reach.py`.
"""
import re
import time

from main import client

# The page to test, and a string that only appears when the real page loads (a code, a store name, a CSS class)
URL = "https://www.faircoupons.com/stores/cozy-earth"
EXPECT = "Cozy Earth"

# From cheapest to most expensive. Premium proxy and JS rendering cost more ZenRows credits per request.
PARAM_SETS = [
    ("plain", {}),
    ("premium_proxy", {"premium_proxy": "true"}),
    ("premium_proxy + US", {"premium_proxy": "true", "proxy_country": "us"}),
    ("js_render", {"js_render": "true"}),
    ("js_render + premium_proxy", {"js_render": "true", "premium_proxy": "true"}),
    ("js_render + premium_proxy + US + wait 5s", {"js_render": "true", "premium_proxy": "true", "proxy_country": "us", "wait": "5000"}),
    ("js_render + premium_proxy + antibot", {"js_render": "true", "premium_proxy": "true", "antibot": "true"}),
    ("original_status", {"original_status": "true"}),
]

# Tries per parameter set, since some sites block only part of the time
TRIES = 2


def describe(response):
    """Short summary of a ZenRows response: page title, or the ZenRows error reason."""
    if response.status_code == 200:
        title = re.search(r"<title>(.*?)</title>", response.text, re.S)
        return title.group(1).strip()[:60] if title else "(no title)"
    try:
        return response.json().get("title", "")[:60]
    except ValueError:
        return response.text[:60].replace("\n", " ")


def run():
    print(f"URL: {URL}")
    print(f"Looking for: {EXPECT!r}\n")
    for name, params in PARAM_SETS:
        for attempt in range(1, TRIES + 1):
            start = time.time()
            try:
                response = client.get(URL, params=params)
            except Exception as e:
                print(f"{name:<45} try {attempt}  ERROR  {e}")
                continue
            found = response.status_code == 200 and EXPECT.lower() in response.text.lower()
            print(
                f"{name:<45} try {attempt}  {response.status_code}  "
                f"{len(response.text):>8} bytes  {time.time() - start:5.1f}s  "
                f"{'FOUND' if found else '-':<5}  {describe(response)}"
            )


if __name__ == "__main__":
    run()
