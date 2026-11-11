"""SQLite introspection and row-to-IR serialization."""

from __future__ import annotations

import sqlite3

import pytest

from baryon.ingest import Acl, chunk_document, documents_from_sqlite, introspect_sqlite
from baryon.ingest.db import iter_rows_sqlite, quote_identifier

ACL = Acl(organization_id="org_db", owner_id="user_db", allowed_roles=("member", "admin"))


def seed() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE vendor_invoice (id INTEGER PRIMARY KEY, vendor TEXT NOT NULL, amount REAL, currency TEXT)"
    )
    connection.executemany(
        "INSERT INTO vendor_invoice (id, vendor, amount, currency) VALUES (?, ?, ?, ?)",
        [
            (1, "에이스상사", 1250000.0, "KRW"),
            (2, "베스트물산", 980000.0, "KRW"),
            (3, "글로벌테크", 4200.5, "USD"),
        ],
    )
    connection.commit()
    return connection


def test_introspect_sqlite_reports_columns_and_keys() -> None:
    connection = seed()
    profile = introspect_sqlite(connection, "vendor_invoice")

    assert profile.table == "vendor_invoice"
    assert profile.row_count == 3
    assert profile.primary_keys == ["id"]
    assert profile.cursor_column == "id"
    assert [column.name for column in profile.columns] == ["id", "vendor", "amount", "currency"]
    assert profile.columns_mapping()["id"]["primary_key"] is True
    assert "vendor_invoice" in profile.describe()


def test_iter_rows_sqlite_paginates() -> None:
    connection = seed()
    profile = introspect_sqlite(connection, "vendor_invoice")
    rows = list(iter_rows_sqlite(connection, profile, batch_size=2))
    assert [row["id"] for row in rows] == [1, 2, 3]


def test_documents_from_sqlite_builds_row_documents() -> None:
    connection = seed()
    documents = list(documents_from_sqlite(connection, "vendor_invoice", ACL))

    assert len(documents) == 3
    first = documents[0]
    assert first.source_type == "database"
    assert first.source_uri == "sqlite://main.vendor_invoice#id=1"
    assert first.title == "vendor_invoice (id=1)"
    assert "테이블 main.vendor_invoice" in first.blocks[0].text
    assert first.blocks[1].table is not None
    assert first.blocks[1].table.rows[1][1] == "에이스상사"
    assert first.acl.organization_id == "org_db"


def test_row_documents_chunk_like_files() -> None:
    connection = seed()
    document = next(documents_from_sqlite(connection, "vendor_invoice", ACL, limit=1))
    chunks = chunk_document(document)
    assert chunks
    assert "vendor_invoice" in chunks[0].text
    assert chunks[0].ordinal == 1


def test_documents_from_sqlite_honours_limit() -> None:
    connection = seed()
    documents = list(documents_from_sqlite(connection, "vendor_invoice", ACL, limit=2))
    assert [document.source_uri.rsplit("=", 1)[-1] for document in documents] == ["1", "2"]


def test_quote_identifier_rejects_injection() -> None:
    assert quote_identifier("vendor_invoice") == '"vendor_invoice"'
    with pytest.raises(ValueError):
        quote_identifier('x"; DROP TABLE y; --')


def test_missing_table_raises() -> None:
    connection = seed()
    with pytest.raises(ValueError):
        introspect_sqlite(connection, "no_such_table")
