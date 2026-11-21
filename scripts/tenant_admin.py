#!/usr/bin/env python3
"""Tiny CLI for the Baryon AX platform tenant API (docs/multi-tenancy.md).

    export BARYON_API_URL=https://ax.example.com
    export BARYON_TOKEN=<platform-admin bearer token>
    python3 scripts/tenant_admin.py list
    python3 scripts/tenant_admin.py create acme "Acme Corp" --plan standard --max-documents 5000
    python3 scripts/tenant_admin.py update acme --chat-per-day 2000 --tokens-per-month 20000000
    python3 scripts/tenant_admin.py update acme --unlimited max_documents
    python3 scripts/tenant_admin.py suspend acme
    python3 scripts/tenant_admin.py resume acme
    python3 scripts/tenant_admin.py usage acme
    python3 scripts/tenant_admin.py audit acme --limit 50
    python3 scripts/tenant_admin.py delete acme --yes

Without BARYON_TOKEN (dev mode) it sends ``X-Roles: platform-admin`` headers,
which only a server running ``AUTH_MODE=dev`` accepts. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

QUOTA_FLAGS = {
    "max_documents": "max-documents",
    "max_storage_bytes": "max-storage-bytes",
    "chat_requests_per_day": "chat-per-day",
    "tokens_per_month": "tokens-per-month",
}


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


def quotas_from(args: argparse.Namespace) -> dict:
    quotas: dict = {}
    for key, flag in QUOTA_FLAGS.items():
        value = getattr(args, flag.replace("-", "_"), None)
        if value is not None:
            quotas[key] = value
    for key in getattr(args, "unlimited", None) or []:
        if key not in QUOTA_FLAGS:
            sys.exit(f"unknown quota {key}; choose from {', '.join(QUOTA_FLAGS)}")
        quotas[key] = None
    return quotas


def add_quota_flags(parser: argparse.ArgumentParser) -> None:
    for flag in QUOTA_FLAGS.values():
        parser.add_argument(f"--{flag}", type=int)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").add_argument("--status", choices=["active", "suspended", "deleting"])
    create = sub.add_parser("create")
    create.add_argument("id")
    create.add_argument("name")
    create.add_argument("--plan", default="standard")
    create.add_argument("--notes", default="")
    add_quota_flags(create)
    update = sub.add_parser("update")
    update.add_argument("id")
    update.add_argument("--name")
    update.add_argument("--plan")
    update.add_argument("--notes")
    update.add_argument("--unlimited", nargs="*", metavar="QUOTA", help="set these quotas to unlimited")
    add_quota_flags(update)
    for name in ("get", "suspend", "resume", "usage"):
        sub.add_parser(name).add_argument("id")
    audit = sub.add_parser("audit")
    audit.add_argument("id")
    audit.add_argument("--limit", type=int, default=100)
    delete = sub.add_parser("delete", help="offboard and purge all tenant data (irreversible)")
    delete.add_argument("id")
    delete.add_argument("--yes", action="store_true", help="confirm the purge")
    args = parser.parse_args()

    if args.command == "list":
        result = request("GET", "/v1/platform/tenants" + (f"?status={args.status}" if args.status else ""))
    elif args.command == "create":
        result = request("POST", "/v1/platform/tenants", {
            "id": args.id, "name": args.name, "plan": args.plan, "notes": args.notes, "quotas": quotas_from(args),
        })
    elif args.command == "update":
        body = {key: value for key, value in (("name", args.name), ("plan", args.plan), ("notes", args.notes)) if value is not None}
        quotas = quotas_from(args)
        if quotas:
            body["quotas"] = quotas
        if not body:
            sys.exit("nothing to update")
        result = request("PATCH", f"/v1/platform/tenants/{args.id}", body)
    elif args.command == "get":
        result = request("GET", f"/v1/platform/tenants/{args.id}")
    elif args.command in {"suspend", "resume"}:
        result = request("POST", f"/v1/platform/tenants/{args.id}/{args.command}")
    elif args.command == "usage":
        result = request("GET", f"/v1/platform/tenants/{args.id}/usage")
    elif args.command == "audit":
        result = request("GET", f"/v1/platform/tenants/{args.id}/audit-events?limit={args.limit}")
    else:
        if not args.yes:
            sys.exit(f"refusing to purge {args.id} without --yes")
        result = request("DELETE", f"/v1/platform/tenants/{args.id}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
