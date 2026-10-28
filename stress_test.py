"""Check how one site behaves under repeated requests.

Usage: python stress_test.py <site> [rounds]
Runs scrape_<site> on every test link <rounds> times in a row, then on all links at once twice,
and reports code counts, times, ZenRows status codes and any redirects.
"""
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import main
from main import client
from test_links import TEST_LINKS

site = sys.argv[1]
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
scraper = getattr(main, f"scrape_{site}")
urls = TEST_LINKS[site]

calls = []
original_get = client.get


def logged_get(url, params=None, **kwargs):
    response = original_get(url, params=params, **kwargs)
    mode = "js" if (params or {}).get("js_render") else "plain"
    calls.append((url, mode, response.status_code, response.headers.get("Zr-Final-Url")))
    return response


client.get = logged_get
main.print = lambda *args, **kwargs: None  # hide retry lines


def scrape(url):
    start = time.time()
    try:
        return url, len(scraper(url)), round(time.time() - start, 1), None
    except Exception as e:
        return url, 0, round(time.time() - start, 1), str(e)[:80]


results = [scrape(url) for _ in range(rounds) for url in urls]
for _ in range(2):
    with ThreadPoolExecutor(len(urls)) as pool:
        results += list(pool.map(scrape, urls))

ok = sum(1 for _, count, _, error in results if count and not error)
print(f"scrapes: {len(results)}, successful with codes: {ok}")
for url in urls:
    counts = Counter(count for u, count, _, _ in results if u == url)
    times = [seconds for u, _, seconds, _ in results if u == url]
    print(f"  {url}\n    code counts: {dict(counts)}  time min/avg/max: {min(times)}/{sum(times) / len(times):.1f}/{max(times)}")
errors = [r for r in results if r[3]]
if errors:
    print("  errors:", errors[:5])
print("zenrows calls:", len(calls), dict(Counter((mode, status) for _, mode, status, _ in calls)))
redirects = [(url, final) for url, _, _, final in calls if final and final.rstrip("/") != url.rstrip("/")]
print("redirected:", redirects[:5] or "none")
