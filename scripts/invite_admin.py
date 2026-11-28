#!/usr/bin/env python3
"""Issue, list and revoke invite codes for Baryon AX signup (docs/multi-tenancy.md).

    export BARYON_API_URL=https://ax.example.com
    export BARYON_TOKEN=<platform-admin bearer token>
    python3 scripts/invite_admin.py create --org acme --email admin@acme.example --expires 7d
    python3 scripts/invite_admin.py list
    python3 scripts/invite_admin.py revoke <invite-id>

The code is printed once by ``create`` and never stored in clear text. Without
BARYON_TOKEN (dev mode) the script sends ``X-Roles: platform-admin`` headers,
which only a server running ``AUTH_MODE=dev`` accepts. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request

DURATION = re.compile(r"^\s*(\d+)\s*([dhm]?)\s*$", re.IGNORECASE)


def parse_expires(value: str) -> int:
    match = DURATION.match(value or "")
    if not match:
        sys.exit("--expires must look like 7d, 24h or 30 (days)")
    amount, unit = int(match.group(1)), (match.group(2) or "d").lower()
    seconds = amount * {"d": 86400, "h": 3600, "m": 60}[unit]
    days = max(1, math.ceil(seconds / 86400))
    if days > 365:
        sys.exit("--expires cannot exceed 365 days")
    return days


def request(method: str, path: str, body: dict | None = None) -> dict:
    base = os.getenv("BARYON_API_URL", "http://localhost:8000").rstrip("/")
    headers = {"Accept": "application/json"}
    token = os.getenv("BARYON_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers.update({"X-User-Id": os.getenv("BARYON_USER", "platform-operator"),
                        "X-Organization-Id": os.getenv("BARYON_ORG", "platform-ops"),
                        "X-Roles": "platform-admin"})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        sys.exit(f"{method} {path} -> HTTP {error.code}: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="issue a one-time invite code")
    create.add_argument("--org", required=True, help="tenant/organization id, e.g. acme")
    create.add_argument("--email", required=True, help="address of the first administrator")
    create.add_argument("--expires", default="7d", help="7d, 24h or a number of days (default 7d)")
    create.add_argument("--notes", default="")

    listing = sub.add_parser("list", help="list invites (never shows codes)")
    listing.add_argument("--status", choices=["pending", "used", "expired", "revoked"])

    revoke = sub.add_parser("revoke", help="revoke a pending invite")
    revoke.add_argument("id")

    args = parser.parse_args()
    if args.command == "create":
        result = request("POST", "/v1/platform/invites", {
            "organization_id": args.org.strip().lower(),
            "email": args.email.strip(),
            "expires_days": parse_expires(args.expires),
            "notes": args.notes,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\nThe code above is shown once. Store it nowhere; send it to the invited person.", file=sys.stderr)
    elif args.command == "list":
        result = request("GET", "/v1/platform/invites" + (f"?status={args.status}" if args.status else ""))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = request("DELETE", f"/v1/platform/invites/{args.id}")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
