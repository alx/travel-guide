#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Fix broken changedetection.io watches (404 / DNS errors) for the France_Project tag.

Strategy per broken watch:
  1. Try common press/news URL path variations on the same domain.
  2. If none work, fall back to a SerpAPI Google search.
  3. PATCH the watch URL in changedetection and update locations.geojson.

Also writes changedetection_uuid back to GeoJSON for all France_Project watches.

Env vars (or .env file):
  CHANGEDETECTION_API_KEY
  CHANGEDETECTION_BASE_URL  (default: http://lamai270:5008)
  SERPAPI_API_KEY
"""

import json
import os
import pathlib
import sys
import time
from urllib.parse import urlparse

import requests

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"
DEFAULT_BASE_URL = "http://lamai270:5008"
FRANCE_TAG = "6ff77fdb-38cb-40bf-adc4-ca25076e6ea6"

PRESS_PATHS = [
    "/presse",
    "/actualites",
    "/news",
    "/newsroom",
    "/press",
    "/media",
    "/media-room",
    "/fr/presse",
    "/fr/actualites",
    "/fr/newsroom",
    "/en/newsroom",
    "/en/press",
    "/en/news",
    "/communiques-de-presse",
    "/espace-presse",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def test_url(url: str, session: requests.Session) -> bool:
    """Return True if URL responds with 2xx or 3xx (follow redirects)."""
    try:
        r = session.head(url, timeout=10, allow_redirects=True, headers=HEADERS)
        if r.status_code < 400:
            return True
        # Some servers reject HEAD; try GET
        r = session.get(url, timeout=10, allow_redirects=True, headers=HEADERS, stream=True)
        r.close()
        return r.status_code < 400
    except Exception:
        return False


def try_path_variations(base_url: str, session: requests.Session) -> str | None:
    """Try common press-page paths on the same domain. Return first working URL."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for path in PRESS_PATHS:
        candidate = origin + path
        if candidate == base_url:
            continue
        if test_url(candidate, session):
            return candidate
        time.sleep(0.3)
    return None


def serpapi_search(query: str, api_key: str, session: requests.Session) -> list[str]:
    """Return up to 5 result URLs from SerpAPI Google search."""
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "hl": "fr",
        "gl": "fr",
        "num": 5,
    }
    try:
        r = session.get("https://serpapi.com/search.json", params=params, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        print(f"    [SEARCH ERROR] {exc}")
        return []
    data = r.json()
    return [result["link"] for result in data.get("organic_results", [])[:5]]


def find_working_url(watch_url: str, company_name: str, serpapi_key: str, session: requests.Session) -> str | None:
    """Try path variations then SerpAPI to find a working replacement."""
    parsed = urlparse(watch_url)
    domain = parsed.netloc.lstrip("www.")

    # Step 1: path variations (only if domain resolves)
    candidate = try_path_variations(watch_url, session)
    if candidate:
        return candidate

    # Step 2: SerpAPI search — skip site: constraint if domain is dead
    try:
        # quick DNS check
        import socket
        socket.getaddrinfo(parsed.netloc, 80, proto=socket.IPPROTO_TCP)
        has_dns = True
    except OSError:
        has_dns = False

    if has_dns:
        query = f'"{company_name}" presse actualités newsroom site:{domain}'
    else:
        query = f'"{company_name}" France presse actualités site officiel'

    print(f"    → SerpAPI: {query}")
    time.sleep(1)  # polite delay
    result_urls = serpapi_search(query, serpapi_key, session)

    for url in result_urls:
        if test_url(url, session):
            return url
        time.sleep(0.3)

    # Broader fallback for DNS errors
    if not has_dns:
        query2 = f"{company_name} France news press"
        print(f"    → SerpAPI fallback: {query2}")
        time.sleep(1)
        for url in serpapi_search(query2, serpapi_key, session):
            if test_url(url, session):
                return url
            time.sleep(0.3)

    return None


def main() -> None:
    load_env()

    api_key = os.environ.get("CHANGEDETECTION_API_KEY")
    if not api_key:
        sys.exit("Error: CHANGEDETECTION_API_KEY not set")
    serpapi_key = os.environ.get("SERPAPI_API_KEY")
    if not serpapi_key:
        sys.exit("Error: SERPAPI_API_KEY not set")
    base_url = os.environ.get("CHANGEDETECTION_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    cd_session = requests.Session()
    cd_session.headers.update({"x-api-key": api_key})

    http_session = requests.Session()

    # Load all watches
    resp = cd_session.get(f"{base_url}/api/v1/watch")
    resp.raise_for_status()
    all_watches = resp.json()

    france_watches = {u: w for u, w in all_watches.items() if FRANCE_TAG in w.get("tags", [])}
    print(f"France_Project watches: {len(france_watches)}")

    # Load GeoJSON
    geojson = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    features = geojson["features"]

    # Build lookup: changedetection_url → list of features (multiple features may share a URL)
    url_to_features: dict[str, list] = {}
    for feat in features:
        feeds = feat["properties"].get("feeds", {})
        cd_url = feeds.get("changedetection_url", "")
        if cd_url:
            url_to_features.setdefault(cd_url, []).append(feat)

    # Track how many times we've matched each URL (to handle duplicates in order)
    url_match_count: dict[str, int] = {}

    fixed = 0
    unfixable = []
    uuid_written = 0

    for uuid, watch in sorted(france_watches.items(), key=lambda x: x[1].get("url", "")):
        watch_url = watch.get("url", "")
        err = watch.get("last_error", "") or ""

        is_404 = "404" in err
        is_dns = "ERR_NAME_NOT_RESOLVED" in err or "getaddrinfo" in err.lower()
        is_conn = "ERR_CONNECTION" in err or "ERR_TIMED_OUT" in err or "TIMED_OUT" in err

        # Write UUID back to GeoJSON
        feat_list = url_to_features.get(watch_url, [])
        match_idx = url_match_count.get(watch_url, 0)
        if match_idx < len(feat_list):
            feat = feat_list[match_idx]
            feeds = feat["properties"].setdefault("feeds", {})
            if not feeds.get("changedetection_uuid"):
                feeds["changedetection_uuid"] = uuid
                uuid_written += 1
        url_match_count[watch_url] = match_idx + 1

        if not (is_404 or is_dns or is_conn):
            continue

        company_name = (watch.get("title") or "").strip() or urlparse(watch_url).netloc.lstrip("www.").split(".")[0]
        print(f"\n[{'404' if is_404 else 'DNS' if is_dns else 'CONN'}] {company_name} — {watch_url}")

        new_url = find_working_url(watch_url, company_name, serpapi_key, http_session)

        if not new_url:
            print(f"  [UNFIXABLE] No working URL found")
            unfixable.append((company_name, watch_url, uuid))
            continue

        if new_url == watch_url:
            print(f"  [SKIP] Same URL, skipping PATCH")
            continue

        print(f"  [FIX] {watch_url}\n      → {new_url}")

        # PUT watch URL (changedetection API only allows PUT, not PATCH)
        patch_resp = cd_session.put(
            f"{base_url}/api/v1/watch/{uuid}",
            json={"url": new_url},
        )
        if patch_resp.ok:
            fixed += 1
            # Update GeoJSON
            if match_idx < len(feat_list):
                feat_list[match_idx]["properties"]["feeds"]["changedetection_url"] = new_url
                # Also update url_to_features index for new URL
                url_to_features.setdefault(new_url, []).append(feat_list[match_idx])
        else:
            print(f"  [PATCH ERROR] {patch_resp.status_code}: {patch_resp.text[:200]}")

    # Write GeoJSON back
    GEOJSON_PATH.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"Fixed: {fixed} | UUIDs written back: {uuid_written}")
    if unfixable:
        print(f"\nUnfixable ({len(unfixable)}) — manual review needed:")
        for name, url, uuid in unfixable:
            print(f"  {name}: {url} (uuid: {uuid})")


if __name__ == "__main__":
    main()
