#!/usr/bin/env python3
"""Validate the static site's SEO/AEO metadata before publishing.

    python3 scripts/check_site_seo.py

Checks titles, descriptions, canonical/hreflang, Open Graph, Twitter cards,
JSON-LD, heading order, image alt text, robots, sitemap, llms.txt, and OG image
dimensions. Exit code 1 when any required check fails.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BASE = "https://baryon-ax.baryon.ai/"

failures: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def warn(message: str) -> None:
    warnings.append(message)


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.headings: list[tuple[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.jsonld: list[str] = []
        self.text: list[str] = []
        self._in_title = False
        self._in_jsonld = False
        self._jsonld_buffer: list[str] = []
        self._heading: str | None = None
        self._heading_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: (value or "") for key, value in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            self.metas.append(values)
        elif tag == "link":
            self.links.append(values)
        elif tag == "img":
            self.images.append(values)
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buffer = []
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._heading = tag
            self._heading_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._jsonld_buffer))
        elif tag == self._heading:
            self.headings.append((tag, " ".join(self._heading_text).strip()))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_jsonld:
            self._jsonld_buffer.append(data)
        if self._heading:
            self._heading_text.append(data)
        self.text.append(data)


def meta_value(parser: HeadParser, key: str, attr: str = "name") -> str:
    for meta in parser.metas:
        if meta.get(attr) == key:
            return meta.get("content", "")
    return ""


def link_value(parser: HeadParser, rel: str, hreflang: str | None = None) -> str:
    for link in parser.links:
        if rel not in link.get("rel", "").split():
            continue
        if hreflang is not None and link.get("hreflang") != hreflang:
            continue
        return link.get("href", "")
    return ""


def check_page(path: Path, expected_lang: str, og_image: str, landing: bool = True) -> None:
    label = path.relative_to(ROOT)
    parser = HeadParser()
    parser.feed(path.read_text(encoding="utf-8"))

    title = " ".join(parser.title.split())
    if not 25 <= len(title) <= 65:
        fail(f"{label}: title should be 25-65 chars, got {len(title)}: {title!r}")

    description = meta_value(parser, "description")
    if not 100 <= len(description) <= 180:
        fail(f"{label}: description should be 100-180 chars, got {len(description)}")

    if not meta_value(parser, "robots"):
        fail(f"{label}: missing meta robots")
    elif "noindex" in meta_value(parser, "robots"):
        fail(f"{label}: robots says noindex")

    canonical = link_value(parser, "canonical")
    if not canonical.startswith(BASE):
        fail(f"{label}: canonical should start with {BASE}, got {canonical!r}")

    for lang in ("ko", "en", "x-default"):
        if not link_value(parser, "alternate", lang):
            fail(f"{label}: missing hreflang {lang}")

    og_required = {
        "og:type": "website",
        "og:site_name": "Baryon AX",
        "og:title": title,
        "og:url": canonical,
        "og:image": og_image,
    }
    for key, expected in og_required.items():
        actual = meta_value(parser, key, "property")
        if not actual:
            fail(f"{label}: missing {key}")
        elif expected and actual != expected and key != "og:title":
            fail(f"{label}: {key} expected {expected!r}, got {actual!r}")

    if meta_value(parser, "og:image:width", "property") != "1200":
        fail(f"{label}: og:image:width should be 1200")
    if meta_value(parser, "og:image:height", "property") != "630":
        fail(f"{label}: og:image:height should be 630")
    if not meta_value(parser, "og:image:alt", "property"):
        fail(f"{label}: missing og:image:alt")
    og_image_url = meta_value(parser, "og:image", "property")
    if og_image_url.startswith(BASE):
        local = SITE / og_image_url[len(BASE):]
        if not local.exists():
            fail(f"{label}: og:image file missing: {local}")

    if meta_value(parser, "twitter:card") != "summary_large_image":
        fail(f"{label}: twitter:card should be summary_large_image")
    for key in ("twitter:title", "twitter:description", "twitter:image", "twitter:image:alt"):
        if not meta_value(parser, key):
            fail(f"{label}: missing {key}")

    if len(parser.headings) and parser.headings[0][0] != "h1":
        fail(f"{label}: first heading is {parser.headings[0][0]}, expected h1")
    levels = [int(tag[1]) for tag, _ in parser.headings if tag in {"h1", "h2", "h3", "h4"}]
    if levels.count(1) != 1:
        fail(f"{label}: expected exactly one h1, got {levels.count(1)}")
    previous = 0
    for level in levels:
        if previous and level > previous + 1:
            fail(f"{label}: heading level jumps from h{previous} to h{level}")
        previous = level

    for index, image in enumerate(parser.images):
        if "alt" not in image:
            fail(f"{label}: image #{index + 1} ({image.get('src', '?')}) has no alt attribute")

    if html_lang := re.search(r'<html[^>]*lang="([^"]+)"', path.read_text(encoding="utf-8")):
        if html_lang.group(1) != expected_lang:
            fail(f"{label}: html lang is {html_lang.group(1)!r}, expected {expected_lang!r}")
    else:
        fail(f"{label}: missing html lang")

    if not parser.jsonld:
        fail(f"{label}: missing JSON-LD")
    all_types: set[str | None] = set()
    faq_node = None
    for block in parser.jsonld:
        try:
            data = json.loads(block)
        except json.JSONDecodeError as error:
            fail(f"{label}: invalid JSON-LD: {error}")
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else []
        all_types |= {node.get("@type") for node in nodes if isinstance(node, dict)}
        if faq_node is None:
            faq_node = next((node for node in nodes if node.get("@type") == "FAQPage"), None)

    if landing:
        for required in ("Organization", "SoftwareApplication", "FAQPage", "WebSite", "SoftwareSourceCode", "HowTo", "BreadcrumbList"):
            if required not in all_types:
                fail(f"{label}: JSON-LD missing {required}")
        if not faq_node or len(faq_node.get("mainEntity") or []) < 3:
            fail(f"{label}: FAQPage should expose at least 3 questions")
    else:
        if "Organization" not in all_types:
            fail(f"{label}: JSON-LD missing Organization")
        if not all_types & {"CollectionPage", "WebPage", "SoftwareApplication"}:
            fail(f"{label}: JSON-LD missing a page type (CollectionPage or WebPage)")
        if "BreadcrumbList" not in all_types:
            fail(f"{label}: JSON-LD missing BreadcrumbList")

    body = " ".join(parser.text)
    if "Baryon AX" not in body:
        fail(f"{label}: body does not mention Baryon AX")

    if "cloudflareinsights.com/beacon.min.js" not in path.read_text(encoding="utf-8"):
        fail(f"{label}: missing Cloudflare Web Analytics beacon")

    text_bytes = path.stat().st_size
    if text_bytes > 200_000:
        warn(f"{label}: HTML is {text_bytes} bytes; consider trimming inline content")


def check_static() -> None:
    robots = (SITE / "robots.txt").read_text(encoding="utf-8")
    if "Sitemap:" not in robots:
        fail("robots.txt: missing Sitemap")
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"):
        if bot not in robots:
            warn(f"robots.txt: no explicit rule for {bot}")
    if "llms.txt" not in robots:
        warn("robots.txt: no comment pointing at llms.txt")

    try:
        tree = ET.parse(SITE / "sitemap.xml")
    except ET.ParseError as error:
        fail(f"sitemap.xml: invalid XML: {error}")
        tree = None
    if tree is not None:
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [node.text or "" for node in tree.findall(".//s:loc", namespace)]
        for expected in (BASE, BASE + "en/"):
            if expected not in locations:
                fail(f"sitemap.xml: missing {expected}")

    for name in ("llms.txt", "llms-full.txt", "en/llms.txt"):
        path = SITE / name
        if not path.exists():
            fail(f"{name}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        if "Baryon AX" not in text:
            fail(f"{name}: does not mention Baryon AX")
        if BASE not in text:
            fail(f"{name}: does not include the canonical site URL")


def check_og_images() -> None:
    for name in ("og.jpg", "og-en.jpg"):
        path = SITE / "assets" / name
        if not path.exists():
            fail(f"assets/{name}: missing")
            continue
        size = path.stat().st_size
        if size > 400_000:
            warn(f"assets/{name}: {size} bytes (>400 KB) may slow social previews")
        try:
            output = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                capture_output=True, text=True, check=True,
            ).stdout
            width = re.search(r"pixelWidth: (\d+)", output)
            height = re.search(r"pixelHeight: (\d+)", output)
            if not width or not height or (width.group(1), height.group(1)) != ("1200", "630"):
                fail(f"assets/{name}: expected 1200x630, got {width.group(1) if width else '?'}x{height.group(1) if height else '?'}")
        except (FileNotFoundError, subprocess.CalledProcessError):
            warn(f"assets/{name}: could not verify dimensions (sips unavailable)")


def page_targets() -> list[tuple[Path, str, str, bool]]:
    """Map every sitemap URL to its local file, language, OG image, and page kind."""
    tree = ET.parse(SITE / "sitemap.xml")
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    targets: list[tuple[Path, str, str, bool]] = []
    for node in tree.findall(".//s:loc", namespace):
        url = (node.text or "").strip()
        if not url.startswith(BASE):
            fail(f"sitemap.xml: URL outside {BASE}: {url}")
            continue
        relative = url[len(BASE):]
        if relative in ("", "/"):
            path = SITE / "index.html"
        elif relative.endswith("/"):
            path = SITE / relative / "index.html"
        else:
            path = SITE / relative
        if not path.exists():
            fail(f"sitemap.xml: no local file for {url} (expected {path.relative_to(ROOT)})")
            continue
        lang = "en" if relative.startswith("en/") else "ko"
        image = BASE + ("assets/og-en.jpg" if lang == "en" else "assets/og.jpg")
        targets.append((path, lang, image, relative in ("", "en/")))
    return targets


def main() -> int:
    if not SITE.exists():
        print("site/ not found", file=sys.stderr)
        return 2
    for path, lang, image, landing in page_targets():
        check_page(path, lang, image, landing)
    check_static()
    check_og_images()

    print(f"failures: {len(failures)}, warnings: {len(warnings)}")
    for message in failures:
        print(f"FAIL  {message}")
    for message in warnings:
        print(f"WARN  {message}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
