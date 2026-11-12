#!/usr/bin/env python3
"""Submit every sitemap URL to IndexNow (Bing, Yandex, Naver, Seznam).

    python3 scripts/submit_indexnow.py --dry-run
    python3 scripts/submit_indexnow.py
    python3 scripts/submit_indexnow.py --timeout 30

The key file lives at ``site/<key>.txt`` and is published to
``https://baryon-ax.baryon.ai/<key>.txt``. Because this is a
GitHub Pages project site, the key location is passed explicitly instead of
being hosted at the host root. Exit code 1 when any endpoint rejects the
submission.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DEFAULT_BASE = "https://baryon-ax.baryon.ai/"
ENDPOINTS = ("https://api.indexnow.org/indexnow", "https://www.bing.com/indexnow")
USER_AGENT = "baryon-ax-indexnow/1.0 (+https://github.com/baryonlabs/baryon-ax)"
KEY_PATTERN = re.compile(r"^[0-9a-f]{8,128}$", re.IGNORECASE)
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def find_key() -> tuple[str, Path]:
    candidates = [
        path
        for path in sorted(SITE.glob("*.txt"))
        if KEY_PATTERN.match(path.stem) and path.stem in path.read_text(encoding="utf-8").strip()
    ]
    if not candidates:
        raise SystemExit(
            "no IndexNow key file found in site/ "
            "(expected site/<32 hex chars>.txt containing the same key)"
        )
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise SystemExit(f"multiple IndexNow key files found: {names}")
    path = candidates[0]
    key = path.read_text(encoding="utf-8").strip()
    if key.lower() != path.stem.lower():
        raise SystemExit(f"{path}: file name and key content differ")
    return key, path


def load_urls(base: str) -> list[str]:
    try:
        tree = ET.parse(SITE / "sitemap.xml")
    except ET.ParseError as error:
        raise SystemExit(f"site/sitemap.xml: invalid XML: {error}") from error
    urls = [node.text.strip() for node in tree.findall(".//s:loc", SITEMAP_NS) if node.text]
    if not urls:
        raise SystemExit("site/sitemap.xml: no <loc> entries")
    for url in urls:
        if not url.startswith(base):
            raise SystemExit(f"sitemap URL is outside {base}: {url}")
    return urls


def submit(endpoint: str, payload: dict, timeout: float, dry_run: bool) -> tuple[bool, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if dry_run:
        return True, "dry-run"
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status in (200, 202), f"{response.status} {response.reason}"
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        return False, f"{error.code} {error.reason} {detail}".strip()
    except (urllib.error.URLError, TimeoutError) as error:
        return False, f"network error: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit sitemap URLs to IndexNow.")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"site base URL (default: {DEFAULT_BASE})")
    parser.add_argument("--endpoint", action="append", dest="endpoints", help="override endpoints (repeatable)")
    parser.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="print the payload without sending it")
    args = parser.parse_args()

    key, key_path = find_key()
    urls = load_urls(args.base)
    key_location = args.base + key_path.name
    host = urlsplit(urls[0]).netloc

    payload = {
        "host": host,
        "key": key,
        "keyLocation": key_location,
        "urlList": urls,
    }

    print(f"key file : {key_path.relative_to(ROOT)}")
    print(f"key URL  : {key_location}")
    print(f"host     : {host}")
    print(f"urls     : {len(urls)}")
    for url in urls:
        print(f"  - {url}")

    endpoints = tuple(args.endpoints or ENDPOINTS)
    failures = 0
    print()
    for endpoint in endpoints:
        ok, detail = submit(endpoint, payload, args.timeout, args.dry_run)
        print(f"{'OK  ' if ok else 'FAIL'} {endpoint} — {detail}")
        if not ok:
            failures += 1

    if args.dry_run:
        print("\npayload:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
