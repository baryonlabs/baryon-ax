#!/usr/bin/env python3
"""Sync a database table into Baryon AX as knowledge.

    # inspect first (no writes)
    python3 scripts/ingest_db.py --dsn sqlite:///erp.db --table vendor_invoice --dry-run

    # sync into a running API
    python3 scripts/ingest_db.py --dsn sqlite:///erp.db --table vendor_invoice \
        --api http://127.0.0.1:8000 --org acme --user u-7 --roles member

    # emit IR as NDJSON instead of uploading
    python3 scripts/ingest_db.py --dsn sqlite:///erp.db --table vendor_invoice --json

Each row becomes one document (see docs/ingestion/architecture.md §5). Until the
native /v1/sources endpoint lands, uploads go through POST /v1/documents as
rendered Markdown, which preserves the table header and row values.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))

from baryon.ingest import Acl, documents_from_sqlalchemy, documents_from_sqlite  # noqa: E402
from baryon.ingest.ir import Document  # noqa: E402
from baryon.ingest.mime import suggested_extension  # noqa: E402


def sqlite_path(dsn: str) -> str | None:
    if dsn.startswith("sqlite:///"):
        return unquote(dsn[len("sqlite:///") :])
    if dsn.startswith("sqlite://"):
        return unquote(dsn[len("sqlite://") :])
    return None


def iterate(dsn: str, table: str, acl: Acl, limit: int | None):
    path = sqlite_path(dsn)
    if path is not None:
        connection = sqlite3.connect(path)
        try:
            yield from documents_from_sqlite(connection, table, acl, limit=limit)
        finally:
            connection.close()
        return

    try:
        import sqlalchemy as sa
    except ModuleNotFoundError as error:
        raise SystemExit("SQLAlchemy is required for non-SQLite DSNs: pip install sqlalchemy") from error
    engine = sa.create_engine(dsn)
    try:
        yield from documents_from_sqlalchemy(engine, table, acl, limit=limit)
    finally:
        engine.dispose()


def upload(document: Document, api: str, org: str, user: str, roles: str, timeout: float) -> dict:
    try:
        import httpx
    except ModuleNotFoundError as error:
        raise SystemExit("httpx is required for uploads: pip install httpx") from error

    filename = document.title.replace("/", "_") + suggested_extension("text/markdown")
    response = httpx.post(
        api.rstrip("/") + "/v1/documents",
        files={"file": (filename, document.render_markdown().encode("utf-8"), "text/markdown")},
        data={"allowed_roles": roles},
        headers={"X-User-Id": user, "X-Organization-Id": org, "X-Roles": roles.split(",")[0]},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", required=True, help="sqlite:///path.db or a SQLAlchemy URL")
    parser.add_argument("--table", required=True)
    parser.add_argument("--api", default=os.getenv("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--org", default=os.getenv("INGEST_ORG", "default"))
    parser.add_argument("--user", default=os.getenv("INGEST_USER", "db-sync"))
    parser.add_argument("--roles", default=os.getenv("INGEST_ROLES", "member,admin"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true", help="print the first documents without uploading")
    parser.add_argument("--json", action="store_true", help="write IR JSON lines to stdout")
    args = parser.parse_args()

    acl = Acl(organization_id=args.org, owner_id=args.user, allowed_roles=tuple(r.strip() for r in args.roles.split(",") if r.strip()))
    count = 0
    failures = 0
    for document in iterate(args.dsn, args.table, acl, args.limit):
        count += 1
        if args.json:
            print(json.dumps(
                {
                    "id": document.id,
                    "source_uri": document.source_uri,
                    "title": document.title,
                    "content_hash": document.content_hash,
                    "metadata": document.metadata,
                    "markdown": document.render_markdown(),
                },
                ensure_ascii=False,
            ))
            continue
        if args.dry_run:
            if count <= 2:
                print(f"--- {document.title} ({document.source_uri}) ---")
                print(document.render_markdown()[:1200])
            continue
        try:
            result = upload(document, args.api, args.org, args.user, args.roles, args.timeout)
        except Exception as error:  # noqa: BLE001 - CLI boundary
            failures += 1
            print(f"FAILED {document.source_uri}: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        print(f"ok {document.title} -> {result.get('id')} ({result.get('chunks', 0)} chunks)")

    scope = "rows" if args.json or args.dry_run else "rows synced"
    print(f"{count} {scope}, {failures} failed", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
