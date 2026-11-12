#!/usr/bin/env python3
"""Insert search-engine ownership verification meta tags into the site pages.

    python3 scripts/set_site_verification.py --google <token> --naver <token>
    python3 scripts/set_site_verification.py --bing <token>
    python3 scripts/set_site_verification.py --remove
    python3 scripts/set_site_verification.py --show

Tokens come from each console after you add the site:

- Google Search Console -> URL prefix property -> HTML tag
- Naver Search Advisor  -> 사이트 등록 -> 소유확인 -> HTML 태그
- Bing Webmaster Tools  -> 사이트 추가 -> 메타 태그

The script only rewrites the three verification tags; everything else in the
head is untouched. Run ``scripts/check_site_seo.py`` afterwards, then open a PR.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PAGES = {"ko": SITE / "index.html", "en": SITE / "en" / "index.html"}
PROVIDERS = {
    "google": "google-site-verification",
    "naver": "naver-site-verification",
    "bing": "msvalidate.01",
}
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,120}$")
ROBOTS_PATTERN = re.compile(r'<meta name="robots" content="[^"]*">')


def apply(markup: str, name: str, token: str | None) -> str:
    markup = re.sub(rf'<meta name="{re.escape(name)}" content="[^"]*">[ \t]*\r?\n?', "", markup)
    if token is None:
        return markup
    tag = f'<meta name="{name}" content="{token}">'
    robots = ROBOTS_PATTERN.search(markup)
    if robots:
        return markup[: robots.end()] + "\n" + tag + markup[robots.end():]
    return markup.replace("</head>", tag + "\n</head>", 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Set search-engine verification meta tags.")
    parser.add_argument("--google", help="google-site-verification token")
    parser.add_argument("--naver", help="naver-site-verification token")
    parser.add_argument("--bing", help="msvalidate.01 token")
    parser.add_argument("--remove", action="store_true", help="remove all three verification tags")
    parser.add_argument("--show", action="store_true", help="print the tags currently present")
    args = parser.parse_args()

    if not any((args.google, args.naver, args.bing, args.remove, args.show)):
        parser.print_help()
        return 2
    if args.remove and any((args.google, args.naver, args.bing)):
        parser.error("--remove cannot be combined with token arguments")

    tokens: dict[str, str | None] = {}
    for provider, value in (("google", args.google), ("naver", args.naver), ("bing", args.bing)):
        if value is None:
            continue
        value = value.strip()
        if not TOKEN_PATTERN.match(value):
            print(f"{provider}: token must be 8-120 chars of [A-Za-z0-9_-]", file=sys.stderr)
            return 2
        tokens[provider] = value
    if args.remove:
        tokens = {provider: None for provider in PROVIDERS}

    if args.show:
        for label, path in PAGES.items():
            markup = path.read_text(encoding="utf-8")
            print(f"[{label}] {path.relative_to(ROOT)}")
            found = False
            for provider, name in PROVIDERS.items():
                match = re.search(rf'<meta name="{re.escape(name)}" content="([^"]*)"', markup)
                if match:
                    found = True
                    print(f"  {provider:6} {match.group(1)}")
            if not found:
                print("  (none)")
        return 0

    for label, path in PAGES.items():
        markup = path.read_text(encoding="utf-8")
        for provider, token in tokens.items():
            markup = apply(markup, PROVIDERS[provider], token)
        path.write_text(markup, encoding="utf-8")
        changed = ", ".join(f"{provider}={'removed' if token is None else 'set'}" for provider, token in tokens.items())
        print(f"{label}: {path.relative_to(ROOT)} — {changed}")

    remaining = [provider for provider in PROVIDERS if provider not in tokens]
    if remaining:
        print(f"unchanged: {', '.join(remaining)}")
    print("\nnext: python3 scripts/check_site_seo.py, then open a PR and verify in the console.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
