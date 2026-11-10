"""Parser adapters and the MIME/extension router.

Parsers are registered, not hard-coded. A new backend (kordoc for Korean
documents, docling for PDF/Office, Tika as the fallback) only needs to produce
`Document` blocks; nothing downstream changes.

Adapters that require external runtimes are intentionally *not* imported here.
`SidecarParser` describes the contract for them:

    POST {sidecar}/parse  (multipart: file)  ->  {"blocks": [...], "metadata": {...}}

The sidecar returns the same block JSON shape as `ir.Block`, so the adapter is
a thin transport wrapper. See docs/ingestion/architecture.md §11.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .ir import Acl, Block, Document, Table, new_document

ParseFn = Callable[[bytes, str, Acl, str], Document]


class ParseError(Exception):
    """A parser was selected but could not read the payload."""


class NoParserError(ParseError):
    """No registered parser accepted the input."""


@dataclass(frozen=True)
class ParserSpec:
    name: str
    extensions: tuple[str, ...]
    mimes: tuple[str, ...]
    parse: ParseFn
    version: str = "1"
    priority: int = 100  # lower runs first among candidates

    def matches(self, filename: str, mime: str) -> bool:
        name = filename.lower()
        if any(name.endswith(ext) for ext in self.extensions):
            return True
        return bool(mime) and mime in self.mimes


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[ParserSpec] = []

    def register(self, spec: ParserSpec) -> ParserSpec:
        self._parsers = [p for p in self._parsers if p.name != spec.name]
        self._parsers.append(spec)
        self._parsers.sort(key=lambda spec: spec.priority)
        return spec

    def specs(self) -> tuple[ParserSpec, ...]:
        return tuple(self._parsers)

    def candidates(self, filename: str, mime: str = "") -> list[ParserSpec]:
        matched = [spec for spec in self._parsers if spec.matches(filename, mime)]
        # Catch-all text parser should always be able to try last.
        fallback = [spec for spec in self._parsers if not spec.extensions and not spec.mimes]
        return matched + [spec for spec in fallback if spec not in matched]

    def parse(
        self,
        payload: bytes,
        filename: str,
        acl: Acl,
        source_uri: str,
        mime: str = "",
    ) -> Document:
        candidates = self.candidates(filename, mime)
        if not candidates:
            raise NoParserError(f"no parser for {filename!r} ({mime or 'unknown mime'})")
        errors: list[str] = []
        for spec in candidates:
            try:
                return spec.parse(payload, filename, acl, source_uri)
            except ParseError as error:
                errors.append(f"{spec.name}: {error}")
        raise ParseError("all parsers failed -> " + "; ".join(errors))


# --------------------------------------------------------------------------
# text / markdown
# --------------------------------------------------------------------------


def parse_text(payload: bytes, filename: str, acl: Acl, source_uri: str) -> Document:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ParseError("payload is not valid UTF-8") from error
    if not text.strip():
        raise ParseError("document is empty")
    blocks = [
        Block("paragraph", text=part.strip())
        for part in re.split(r"\n\s*\n", text)
        if part.strip()
    ]
    return new_document(
        source_uri=source_uri,
        title=filename,
        blocks=blocks,
        acl=acl,
        payload=payload,
        parser="text",
        parser_version="1",
        mime="text/plain",
    )


_FENCE = re.compile(r"^```")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_markdown(payload: bytes, filename: str, acl: Acl, source_uri: str) -> Document:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ParseError("markdown payload is not valid UTF-8") from error
    if not text.strip():
        raise ParseError("document is empty")

    lines = text.splitlines()
    blocks: list[Block] = []
    paragraph: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(Block("paragraph", text="\n".join(paragraph).strip()))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _FENCE.match(stripped):
            flush_paragraph()
            index += 1
            code: list[str] = []
            while index < len(lines) and not _FENCE.match(lines[index].strip()):
                code.append(lines[index])
                index += 1
            blocks.append(Block("code", text="\n".join(code)))
            index += 1
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush_paragraph()
            blocks.append(Block("heading", text=heading.group(2).strip(), level=len(heading.group(1))))
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[index + 1]):
            flush_paragraph()
            rows = [_split_row(stripped)]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_split_row(lines[index]))
                index += 1
            width = max(len(row) for row in rows)
            rows = [row + [""] * (width - len(row)) for row in rows]
            blocks.append(Block("table", table=Table(rows=rows, header_rows=1)))
            continue

        if _LIST.match(line):
            flush_paragraph()
            items: list[str] = []
            while index < len(lines) and _LIST.match(lines[index]):
                items.append(_LIST.sub("", lines[index]).strip())
                index += 1
            blocks.append(Block("list", text="\n".join(f"- {item}" for item in items)))
            continue

        if not stripped:
            flush_paragraph()
            index += 1
            continue

        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    if not blocks:
        raise ParseError("markdown produced no blocks")
    return new_document(
        source_uri=source_uri,
        title=filename,
        blocks=blocks,
        acl=acl,
        payload=payload,
        parser="markdown",
        parser_version="1",
        mime="text/markdown",
    )


# --------------------------------------------------------------------------
# database rows -> IR
# --------------------------------------------------------------------------


def parse_rows(
    *,
    table: str,
    columns: dict[str, dict[str, Any]],
    rows: Iterable[dict[str, Any]],
    acl: Acl,
    source_uri: str,
    schema: str = "public",
    description: str = "",
) -> Document:
    """Serialize table rows into the same IR as documents.

    One row is one logical document (see architecture.md §5). The header block
    carries table/column semantics so a row chunk never loses its context.
    """
    rows = list(rows)
    if not rows:
        raise ParseError("no rows to serialize")

    column_names = list(columns) or list(rows[0])
    column_lines = []
    for name in column_names:
        spec = columns.get(name, {})
        kind = spec.get("type", "")
        note = spec.get("description", "")
        column_lines.append(f"- {name}" + (f" ({kind})" if kind else "") + (f": {note}" if note else ""))

    header_parts = [f"테이블 {schema}.{table}"]
    if description:
        header_parts.append(description)
    header_parts.append("컬럼:\n" + "\n".join(column_lines))
    blocks = [Block("paragraph", text="\n\n".join(header_parts))]

    body = [column_names]
    for row in rows:
        body.append([_stringify(row.get(name)) for name in column_names])
    blocks.append(Block("table", table=Table(rows=body, header_rows=1)))

    payload = repr((schema, table, tuple(map(tuple, body)))).encode("utf-8")
    title = f"{table} ({_primary_key_hint(rows, columns)})"
    return new_document(
        source_uri=source_uri,
        title=title,
        blocks=blocks,
        acl=acl,
        payload=payload,
        parser="rows",
        parser_version="1",
        source_type="database",
        mime="application/x-sql-rows",
        metadata={"schema": schema, "table": table, "row_count": len(rows)},
    )


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _primary_key_hint(rows: list[dict[str, Any]], columns: dict[str, dict[str, Any]]) -> str:
    for name, spec in columns.items():
        if spec.get("primary_key"):
            return f"{name}={rows[0].get(name)}"
    if "id" in rows[0]:
        return f"id={rows[0]['id']}"
    return f"rows={len(rows)}"


# --------------------------------------------------------------------------
# default registry
# --------------------------------------------------------------------------

REGISTRY = ParserRegistry()
REGISTRY.register(ParserSpec("markdown", (".md", ".markdown", ".mdown"), ("text/markdown",), parse_markdown))
REGISTRY.register(ParserSpec("text", (".txt",), ("text/plain",), parse_text))
REGISTRY.register(ParserSpec("fallback-text", (), (), parse_text, version="1", priority=1000))
