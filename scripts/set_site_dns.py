#!/usr/bin/env python3
"""Create the GitHub Pages CNAME record in Cloudflare.

    export CLOUDFLARE_API_TOKEN=...   # Zone.DNS:Edit on baryon.ai
    python3 scripts/set_site_dns.py --dry-run
    python3 scripts/set_site_dns.py --apply

The record is created as DNS-only (not proxied) so GitHub can issue its own
certificate. Re-running the script is safe: an identical record is left alone
and a conflicting one is updated only with --apply.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
DEFAULT_DOMAIN = "baryon-ax.baryon.ai"
DEFAULT_TARGET = "baryonlabs.github.io"


def call(token: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"errors": [{"message": body[:300]}]}


def explain(status: int, data: dict) -> str:
    if status == 403:
        return (
            "permission denied. The token needs Zone.DNS:Edit on the baryon.ai zone. "
            "Create one at https://dash.cloudflare.com/profile/api-tokens"
        )
    errors = data.get("errors") or []
    return "; ".join(error.get("message", "") for error in errors) or f"HTTP {status}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the GitHub Pages CNAME in Cloudflare.")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN, help=f"record name (default: {DEFAULT_DOMAIN})")
    parser.add_argument("--target", default=DEFAULT_TARGET, help=f"CNAME target (default: {DEFAULT_TARGET})")
    parser.add_argument("--zone", default="baryon.ai", help="Cloudflare zone (default: baryon.ai)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="create or update the record")
    group.add_argument("--dry-run", action="store_true", help="show the planned change (default)")
    args = parser.parse_args()

    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        print("CLOUDFLARE_API_TOKEN is not set", file=sys.stderr)
        return 2

    status, data = call(token, "GET", f"/zones?name={args.zone}")
    if status != 200 or not data.get("success"):
        print(f"zone lookup failed: {explain(status, data)}", file=sys.stderr)
        return 1
    zones = data.get("result") or []
    if not zones:
        print(f"zone not found: {args.zone}", file=sys.stderr)
        return 1
    zone_id = zones[0]["id"]

    status, data = call(token, "GET", f"/zones/{zone_id}/dns_records?name={args.domain}")
    if status != 200 or not data.get("success"):
        print(f"record lookup failed: {explain(status, data)}", file=sys.stderr)
        return 1
    records = data.get("result") or []
    desired = {
        "type": "CNAME",
        "name": args.domain,
        "content": args.target,
        "proxied": False,
        "ttl": 1,
        "comment": "GitHub Pages for baryon-ax",
    }

    if records:
        record = records[0]
        same = (
            record.get("type") == desired["type"]
            and record.get("content") == desired["content"]
            and record.get("proxied") is False
        )
        if same:
            print(f"OK   {args.domain} CNAME {args.target} (DNS only) already exists")
            return 0
        print(f"existing: {record.get('type')} {record.get('content')} proxied={record.get('proxied')}")
        if not args.apply:
            print("dry-run: would update the record to CNAME baryonlabs.github.io (DNS only)")
            return 0
        status, data = call(token, "PUT", f"/zones/{zone_id}/dns_records/{record['id']}", desired)
        action = "updated"
    else:
        print(f"missing: no record for {args.domain}")
        if not args.apply:
            print("dry-run: would create CNAME baryonlabs.github.io (DNS only)")
            return 0
        status, data = call(token, "POST", f"/zones/{zone_id}/dns_records", desired)
        action = "created"

    if status not in (200, 201) or not data.get("success"):
        print(f"failed to write the record: {explain(status, data)}", file=sys.stderr)
        return 1

    result = data.get("result") or {}
    print(f"OK   {action}: {result.get('type')} {result.get('name')} -> {result.get('content')} (DNS only)")
    print(f"verify: dig +short {args.domain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
