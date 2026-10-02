"""
Live RSS Feed TLS and Connectivity Validator.
Fetches all configured feeds with strict SSL verification (verify=True) and logs individual status.
"""

import asyncio
import json
import os
import ssl
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from app.integrations.news.rss.allowlist import ALLOWLISTED_FEEDS

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

async def check_feed(client: httpx.AsyncClient, feed):
    start = time.perf_counter()
    url = feed.url
    try:
        resp = await client.get(url, headers=HTTP_HEADERS, timeout=8.0)
        dur_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "name": feed.name,
            "publisher": feed.publisher,
            "url": url,
            "status_code": resp.status_code,
            "tls_valid": True,
            "latency_ms": dur_ms,
            "error": None if resp.status_code < 400 else f"HTTP_{resp.status_code}",
        }
    except ssl.SSLError as exc:
        dur_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "name": feed.name,
            "publisher": feed.publisher,
            "url": url,
            "status_code": None,
            "tls_valid": False,
            "latency_ms": dur_ms,
            "error": f"SSL_ERROR: {exc}",
        }
    except httpx.ConnectError as exc:
        dur_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "name": feed.name,
            "publisher": feed.publisher,
            "url": url,
            "status_code": None,
            "tls_valid": "unknown",
            "latency_ms": dur_ms,
            "error": f"CONNECT_ERROR: {str(exc)[:60]}",
        }
    except httpx.TimeoutException:
        dur_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "name": feed.name,
            "publisher": feed.publisher,
            "url": url,
            "status_code": None,
            "tls_valid": "timeout",
            "latency_ms": dur_ms,
            "error": "TIMEOUT",
        }
    except Exception as exc:
        dur_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "name": feed.name,
            "publisher": feed.publisher,
            "url": url,
            "status_code": None,
            "tls_valid": "error",
            "latency_ms": dur_ms,
            "error": str(exc)[:60],
        }

async def main():
    print(f"[*] Probing {len(ALLOWLISTED_FEEDS)} configured feeds with verify=True...")
    limits = httpx.Limits(max_connections=15, max_keepalive_connections=5)
    async with httpx.AsyncClient(verify=True, limits=limits, follow_redirects=True) as client:
        tasks = [check_feed(client, f) for f in ALLOWLISTED_FEEDS]
        results = await asyncio.gather(*tasks)

    print("\n" + "=" * 110)
    print(f"{'Feed Name':<35} | {'Publisher':<20} | {'Status':<10} | {'Latency':<10} | {'TLS Valid':<10} | {'Error'}")
    print("-" * 110)
    for r in results:
        status_str = str(r["status_code"]) if r["status_code"] is not None else "FAIL"
        tls_str = "YES" if r["tls_valid"] is True else ("NO" if r["tls_valid"] is False else str(r["tls_valid"]))
        err_str = r["error"] or "None (OK)"
        print(f"{r['name'][:34]:<35} | {r['publisher'][:19]:<20} | {status_str:<10} | {str(r['latency_ms']) + 'ms':<10} | {tls_str:<10} | {err_str}")
    print("=" * 110)

    with open("feed_probe_results.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
