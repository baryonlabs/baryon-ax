#!/usr/bin/env python3
"""Switch the published site to a custom domain in one pass.

    python3 scripts/apply_site_domain.py --domain baryon-ax.baryon.ai
    python3 scripts/apply_site_domain.py --domain baryon-ax.baryon.ai --write

Dry-run by default. The script rewrites the canonical base URL in the site
pages, llms files, sitemap, robots, scripts, docs, and README, converts
absolute ``/baryon-ax/`` asset paths to root-relative paths, and writes
``site/CNAME``. Run ``scripts/check_site_seo.py`` afterwards and open a PR.

DNS must point at ``baryonlabs.github.io`` before the custom domain is enabled
in GitHub Pages, otherwise the site can be unreachable at both URLs.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
SCRIPT = Path(__file__).resolve()
OLD_BASE = "https://baryonlabs.github.io/baryon-ax/"
OLD_PREFIX = "/baryon-ax/"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
TEXT_SUFFIXES = {".html", ".txt", ".xml", ".md", ".py", ".json", ".yml", ".yaml", ".toml"}
DOMAIN_PATTERN = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def iter_text_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path == SCRIPT:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def transform(text: str, old_base: str, new_base: str) -> str:
    text = text.replace(old_base, new_base)
    text = text.replace(old_base.rstrip("/"), new_base.rstrip("/"))
    text = text.replace("baryonlabs.github.io/baryon-ax/", new_base.split("//", 1)[1])
    text = text.replace(f'"{OLD_PREFIX}', '"/')
    text = text.replace(f"'{OLD_PREFIX}", "'/")
    text = text.replace(f"({OLD_PREFIX}", "(/")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Point the site at a custom domain.")
    parser.add_argument("--domain", required=True, help="custom domain, for example baryon-ax.baryon.ai")
    parser.add_argument("--old-base", default=OLD_BASE, help=f"current base URL (default: {OLD_BASE})")
    parser.add_argument("--write", action="store_true", help="apply the changes (default: dry-run)")
    args = parser.parse_args()

    domain = args.domain.strip().lower().rstrip(".")
    if not DOMAIN_PATTERN.match(domain):
        print(f"invalid domain: {args.domain!r}", file=sys.stderr)
        return 2
    if domain.endswith("github.io"):
        print("the new domain must not be a github.io host", file=sys.stderr)
        return 2

    old_base = args.old_base if args.old_base.endswith("/") else args.old_base + "/"
    new_base = f"https://{domain}/"

    changes: list[tuple[Path, int]] = []
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8")
        rewritten = transform(text, old_base, new_base)
        if rewritten == text:
            continue
        count = text.count(old_base) + text.count(f'"{OLD_PREFIX}') + text.count(f"'{OLD_PREFIX}")
        changes.append((path, max(count, 1)))
        if args.write:
            path.write_text(rewritten, encoding="utf-8")

    if not changes:
        print("no files reference the old base URL; nothing to do")
        return 0

    print(f"{'apply' if args.write else 'dry-run'}: {old_base} -> {new_base}")
    for path, count in changes:
        print(f"  {path.relative_to(ROOT)}  ({count} references)")

    cname = SITE / "CNAME"
    if args.write:
        cname.write_text(domain + "\n", encoding="utf-8")
        print(f"\nwrote {cname.relative_to(ROOT)}")
    else:
        print(f"\nwould write {cname.relative_to(ROOT)} -> {domain}")

    remaining = 0
    if args.write:
        for path in iter_text_files():
            remaining += path.read_text(encoding="utf-8").count(old_base)
        print(f"remaining references to {old_base}: {remaining}")

    print(
        "\nnext:\n"
        f"  1. DNS: CNAME {domain.split('.')[0]} -> baryonlabs.github.io (DNS only, not proxied)\n"
        f"  2. dig +short {domain}\n"
        "  3. python3 scripts/check_site_seo.py\n"
        "  4. PR -> merge -> Pages deploy\n"
        f"  5. gh api -X PUT repos/baryonlabs/baryon-ax/pages -f cname={domain}\n"
        f"  6. python3 scripts/check_site_live.py --base https://{domain}/\n"
        "  7. python3 scripts/submit_indexnow.py\n"
        f"  8. re-register https://{domain}/ in Search Console / Naver / Bing"
    )
    return 0 if not remaining else 1


if __name__ == "__main__":
    raise SystemExit(main())
