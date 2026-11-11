"""Database -> knowledge: introspection, row streaming, IR serialization.

Two paths are supported:

* **SQLite (stdlib)** — used by tests and small on-prem deployments.
* **SQLAlchemy** — Postgres/MySQL/MSSQL/Oracle, when the driver is installed.

Both converge on `TableProfile` and the same `parse_rows` serializer, so rows
land in the same Document IR as files and share chunking, ACL, citation, and
deletion semantics.

Deliberately *not* here: CDC and scheduling. Snapshot + cursor pagination first;
Debezium/whale sinks belong to the P2 worker tier (see architecture.md §6).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from .ir import Acl, Document
from .parsers import parse_rows

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def quote_identifier(name: str) -> str:
    """Reject anything that is not a plain SQL identifier."""
    if not _IDENTIFIER.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


@dataclass
class ColumnProfile:
    name: str
    type: str = ""
    nullable: bool = True
    primary_key: bool = False
    description: str = ""


@dataclass
class TableProfile:
    schema: str
    table: str
    columns: list[ColumnProfile]
    row_count: int | None = None
    description: str = ""

    @property
    def primary_keys(self) -> list[str]:
        return [column.name for column in self.columns if column.primary_key]

    @property
    def cursor_column(self) -> str | None:
        keys = self.primary_keys
        return keys[0] if len(keys) == 1 else None

    def columns_mapping(self) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        for column in self.columns:
            entry: dict[str, Any] = {"type": column.type, "nullable": column.nullable}
            if column.primary_key:
                entry["primary_key"] = True
            if column.description:
                entry["description"] = column.description
            mapping[column.name] = entry
        return mapping

    def describe(self) -> str:
        if self.description:
            return self.description
        columns = ", ".join(f"{column.name}({column.type})" if column.type else column.name for column in self.columns)
        return f"{self.schema}.{self.table} — 컬럼: {columns}"


def introspect_sqlite(connection: sqlite3.Connection, table: str, schema: str = "main") -> TableProfile:
    if schema != "main":
        raise ValueError("sqlite introspection supports the main schema only")
    quoted = quote_identifier(table)
    rows = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
    if not rows:
        raise ValueError(f"table not found: {table}")
    columns = [
        ColumnProfile(
            name=row[1],
            type=str(row[2] or ""),
            nullable=not bool(row[3]),
            primary_key=bool(row[5]),
        )
        for row in rows
    ]
    count = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()
    profile = TableProfile(schema=schema, table=table, columns=columns, row_count=int(count[0]) if count else None)
    return profile


def iter_rows_sqlite(
    connection: sqlite3.Connection,
    profile: TableProfile,
    *,
    batch_size: int = 200,
    after: Any | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    table = quote_identifier(profile.table)
    cursor_column = profile.cursor_column
    fetched = 0
    offset = 0

    while True:
        remaining = batch_size if limit is None else min(batch_size, limit - fetched)
        if remaining <= 0:
            return
        if cursor_column and after is not None:
            sql = f"SELECT * FROM {table} WHERE {quote_identifier(cursor_column)} > ? ORDER BY {quote_identifier(cursor_column)} LIMIT ?"
            params: tuple[Any, ...] = (after, remaining)
        else:
            sql = f"SELECT * FROM {table} LIMIT ? OFFSET ?"
            params = (remaining, offset)
        cursor = connection.execute(sql, params)
        names = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        if not rows:
            return
        for row in rows:
            record = dict(zip(names, row))
            if cursor_column:
                after = record[cursor_column]
            yield record
            fetched += 1
        offset += len(rows)
        if len(rows) < remaining:
            return


def rows_to_documents(
    profile: TableProfile,
    rows: Iterable[dict[str, Any]],
    acl: Acl,
    uri_prefix: str,
) -> Iterator[Document]:
    columns = profile.columns_mapping()
    keys = profile.primary_keys
    for index, row in enumerate(rows, start=1):
        if keys:
            key_text = ",".join(f"{key}={row.get(key)}" for key in keys)
        else:
            key_text = f"row={index}"
        yield parse_rows(
            table=profile.table,
            columns=columns,
            rows=[row],
            acl=acl,
            source_uri=f"{uri_prefix}#{key_text}",
            schema=profile.schema,
            description=profile.describe(),
        )


def documents_from_sqlite(
    connection: sqlite3.Connection,
    table: str,
    acl: Acl,
    uri_prefix: str | None = None,
    *,
    schema: str = "main",
    batch_size: int = 200,
    limit: int | None = None,
) -> Iterator[Document]:
    profile = introspect_sqlite(connection, table, schema)
    prefix = uri_prefix or f"sqlite://{schema}.{table}"
    rows = iter_rows_sqlite(connection, profile, batch_size=batch_size, limit=limit)
    yield from rows_to_documents(profile, rows, acl, prefix)


# --------------------------------------------------------------------------
# SQLAlchemy path (optional dependency)
# --------------------------------------------------------------------------


def introspect_sqlalchemy(engine_or_url: Any, table: str, schema: str | None = None) -> TableProfile:
    """Introspect any SQLAlchemy-supported database.

    `engine_or_url` may be an Engine or a URL string; a string is connected with
    `create_engine`. The caller owns the engine lifecycle.
    """
    try:
        import sqlalchemy as sa
    except ModuleNotFoundError as error:  # pragma: no cover - optional dependency
        raise RuntimeError("SQLAlchemy is required for non-SQLite sources") from error

    engine = sa.create_engine(engine_or_url) if isinstance(engine_or_url, str) else engine_or_url
    inspector = sa.inspect(engine)
    if not inspector.has_table(table, schema=schema):
        raise ValueError(f"table not found: {schema + '.' if schema else ''}{table}")

    primary_keys = set(inspector.get_pk_constraint(table, schema=schema).get("constrained_columns") or [])
    columns = [
        ColumnProfile(
            name=column["name"],
            type=str(column.get("type") or ""),
            nullable=bool(column.get("nullable", True)),
            primary_key=column["name"] in primary_keys,
        )
        for column in inspector.get_columns(table, schema=schema)
    ]
    description = ""
    try:
        comment = inspector.get_table_comment(table, schema=schema)
        description = (comment or {}).get("text") or ""
    except NotImplementedError:
        description = ""

    target = f"{quote_identifier(schema)}.{quote_identifier(table)}" if schema else quote_identifier(table)
    with engine.connect() as connection:
        row_count = connection.execute(sa.text(f"SELECT COUNT(*) FROM {target}")).scalar()
    return TableProfile(
        schema=schema or "",
        table=table,
        columns=columns,
        row_count=int(row_count) if row_count is not None else None,
        description=description,
    )


def documents_from_sqlalchemy(
    engine: Any,
    table: str,
    acl: Acl,
    uri_prefix: str | None = None,
    *,
    schema: str | None = None,
    batch_size: int = 500,
    limit: int | None = None,
) -> Iterator[Document]:
    try:
        import sqlalchemy as sa
    except ModuleNotFoundError as error:  # pragma: no cover
        raise RuntimeError("SQLAlchemy is required for non-SQLite sources") from error

    profile = introspect_sqlalchemy(engine, table, schema)
    target = f"{quote_identifier(schema)}.{quote_identifier(table)}" if schema else quote_identifier(table)
    prefix = uri_prefix or f"{engine.url.render_as_string(hide_password=True)}#{profile.schema or 'default'}.{table}"
    cursor_column = profile.cursor_column

    def row_iter() -> Iterator[dict[str, Any]]:
        fetched = 0
        offset = 0
        after: Any | None = None
        while True:
            remaining = batch_size if limit is None else min(batch_size, limit - fetched)
            if remaining <= 0:
                return
            if cursor_column and after is not None:
                sql = sa.text(
                    f"SELECT * FROM {target} WHERE {quote_identifier(cursor_column)} > :after "
                    f"ORDER BY {quote_identifier(cursor_column)} LIMIT :size"
                )
                params = {"after": after, "size": remaining}
            else:
                sql = sa.text(f"SELECT * FROM {target} LIMIT :size OFFSET :offset")
                params = {"size": remaining, "offset": offset}
            with engine.connect() as connection:
                result = connection.execute(sql, params)
                names = list(result.keys())
                rows = result.fetchall()
            if not rows:
                return
            for row in rows:
                record = dict(zip(names, row))
                if cursor_column:
                    after = record[cursor_column]
                yield record
                fetched += 1
            offset += len(rows)
            if len(rows) < remaining:
                return

    yield from rows_to_documents(profile, row_iter(), acl, prefix)
