#!/usr/bin/env python3
"""Verify the deployed site, not just the local files.

    python3 scripts/check_site_live.py
    python3 scripts/check_site_live.py --base https://example.com/
    python3 scripts/check_site_live.py --skip-images

Checks every sitemap page for HTTP 200, HTML content type, title and canonical
agreement with the local file, Open Graph image reachability, JSON-LD parsing,
and a single H1. Also checks robots.txt, sitemap.xml, llms.txt, llms-full.txt,
the English llms.txt, and the IndexNow key file. Exit code 1 on any failure.

GitHub Pages caches for a few minutes after a deploy, so run this after the
Pages workflow completes, not immediately after a merge.
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
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DEFAULT_BASE = "https://baryonlabs.github.io/baryon-ax/"
USER_AGENT = "baryon-ax-live-check/1.0 (+https://github.com/baryonlabs/baryon-ax)"
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
KEY_PATTERN = re.compile(r"^[0-9a-f]{8,128}$", re.IGNORECASE)

failures: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def request(url: str, method: str = "GET", timeout: float = 20.0) -> tuple[int, str, bytes]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.headers.get("content-type", ""), response.read()


def fetch(url: str, timeout: float = 20.0) -> tuple[int, str, bytes]:
    try:
        return request(url, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code == 405:
            return request(url, method="GET", timeout=timeout)
        raise


def local_path_for(url: str, base: str) -> Path | None:
    if not url.startswith(base):
        return None
    relative = url[len(base):]
    if relative in ("", "/"):
        return SITE / "index.html"
    if relative.endswith("/"):
        return SITE / relative / "index.html"
    return SITE / relative


def local_value(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.S)
    return match.group(1).strip() if match else ""


def normalize(value: str) -> str:
    return " ".join(value.split())


def check_page(url: str, base: str, timeout: float, skip_images: bool = False) -> None:
    path = local_path_for(url, base)
    if path is None or not path.exists():
        fail(f"{url}: no local source file for this sitemap URL")
        return

    status, content_type, body = fetch(url, timeout)
    if status != 200:
        fail(f"{url}: HTTP {status}")
    if not content_type.startswith("text/html"):
        fail(f"{url}: content-type is {content_type!r}, expected text/html")
    markup = body.decode("utf-8", errors="replace")

    expected_title = normalize(local_value(path, r"<title>(.*?)</title>"))
    live_title = normalize(local_value_from(markup, r"<title>(.*?)</title>"))
    if not live_title:
        fail(f"{url}: no <title> in deployed HTML")
    elif live_title != expected_title:
        fail(f"{url}: title differs from local file\n  live: {live_title}\n  local: {expected_title}")

    expected_canonical = normalize(local_value(path, r'<link rel="canonical" href="([^"]+)"'))
    live_canonical = normalize(local_value_from(markup, r'<link rel="canonical" href="([^"]+)"'))
    if live_canonical != expected_canonical:
        fail(f"{url}: canonical differs\n  live: {live_canonical}\n  local: {expected_canonical}")

    expected_lang = local_value(path, r'<html[^>]*lang="([^"]+)"')
    live_lang = local_value_from(markup, r'<html[^>]*lang="([^"]+)"')
    if expected_lang and live_lang != expected_lang:
        fail(f"{url}: html lang is {live_lang!r}, expected {expected_lang!r}")

    headings = re.findall(r"<h1\b", markup, re.I)
    if len(headings) != 1:
        fail(f"{url}: expected exactly one <h1>, found {len(headings)}")

    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', markup, re.S)
    if not blocks:
        fail(f"{url}: no JSON-LD block")
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError as error:
            fail(f"{url}: invalid JSON-LD: {error}")
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else []
        types = {node.get("@type") for node in nodes if isinstance(node, dict)}
        if "FAQPage" not in types:
            fail(f"{url}: JSON-LD missing FAQPage")

    og_image = local_value_from(markup, r'property="og:image" content="([^"]+)"')
    if not og_image:
        fail(f"{url}: missing og:image")
    elif not skip_images:
        image_url = urljoin(url, og_image)
        try:
            image_status, image_type, _ = fetch(image_url, timeout)
            if image_status != 200:
                fail(f"{og_image}: HTTP {image_status}")
            if not image_type.startswith("image/"):
                fail(f"{og_image}: content-type is {image_type!r}, expected image/*")
        except (urllib.error.URLError, TimeoutError) as error:
            fail(f"{og_image}: unreachable: {error}")

    print(f"OK   {url}")


def local_value_from(markup: str, pattern: str) -> str:
    match = re.search(pattern, markup, re.S)
    return match.group(1).strip() if match else ""


def check_file(url: str, expected_type: str, timeout: float, contains: str | None = None) -> bytes:
    try:
        status, content_type, body = fetch(url, timeout)
    except (urllib.error.URLError, TimeoutError) as error:
        fail(f"{url}: unreachable: {error}")
        return b""
    if status != 200:
        fail(f"{url}: HTTP {status}")
    if expected_type not in content_type:
        fail(f"{url}: content-type is {content_type!r}, expected {expected_type}")
    if contains and contains.encode() not in body:
        fail(f"{url}: does not contain {contains!r}")
    print(f"OK   {url}")
    return body


def check_key_file(base: str, timeout: float) -> None:
    candidates = [
        path
        for path in sorted(SITE.glob("*.txt"))
        if KEY_PATTERN.match(path.stem) and path.stem in path.read_text(encoding="utf-8").strip()
    ]
    for path in candidates:
        url = base + path.name
        body = check_file(url, "text/plain", timeout)
        if body and body.decode("utf-8", errors="replace").strip() != path.stem:
            fail(f"{url}: content does not match the file name")
    if not candidates:
        warn("site/: no IndexNow key file; run scripts/submit_indexnow.py after adding one")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the deployed Baryon AX site.")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"site base URL (default: {DEFAULT_BASE})")
    parser.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    parser.add_argument("--skip-images", action="store_true", help="skip og:image reachability checks")
    args = parser.parse_args()
    base = args.base if args.base.endswith("/") else args.base + "/"

    try:
        sitemap_status, _, sitemap_body = fetch(base + "sitemap.xml", args.timeout)
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"cannot reach {base}: {error}", file=sys.stderr)
        return 2
    if sitemap_status != 200:
        print(f"{base}sitemap.xml: HTTP {sitemap_status}", file=sys.stderr)
        return 2

    try:
        tree = ET.fromstring(sitemap_body)
    except ET.ParseError as error:
        print(f"deployed sitemap.xml: invalid XML: {error}", file=sys.stderr)
        return 2
    urls = [node.text.strip() for node in tree.findall(".//s:loc", SITEMAP_NS) if node.text]
    if not urls:
        print("deployed sitemap.xml: no <loc> entries", file=sys.stderr)
        return 2

    for url in urls:
        check_page(url, base, args.timeout, args.skip_images)

    check_file(base + "robots.txt", "text/plain", args.timeout, "Sitemap:")
    check_file(base + "llms.txt", "text/plain", args.timeout, "Baryon AX")
    check_file(base + "llms-full.txt", "text/plain", args.timeout, "Baryon AX")
    check_file(base + "en/llms.txt", "text/plain", args.timeout, "Baryon AX")
    check_key_file(base, args.timeout)

    print(f"\nfailures: {len(failures)}, warnings: {len(warnings)}")
    for message in failures:
        print(f"FAIL {message}")
    for message in warnings:
        print(f"WARN {message}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
