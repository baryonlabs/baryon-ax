"""Parser-agnostic document intermediate representation (IR).

Every parser (kordoc, docling, Tika, SQL row serializer, ...) must produce a
`Document` made of typed `Block`s. Downstream stages (chunking, embedding,
indexing, citation) only depend on this module, so adding a parser never
changes the retrieval contract.

See docs/ingestion/architecture.md for the design rationale.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

BlockKind = Literal["heading", "paragraph", "list", "table", "code", "caption"]


def normalize_text(text: str) -> str:
    """Normalize to NFC.

    macOS documents and filenames are frequently NFD (decomposed Hangul). Korean
    tokenizers and substring search operate on precomposed syllables, so an NFD
    document silently matches nothing. Normalize at every boundary.
    """
    return unicodedata.normalize("NFKC", text) if text else text


@dataclass
class Table:
    """A table as a grid of cell strings.

    `header_rows` cells are repeated when a table is split into chunks so that
    each chunk stays self-describing.
    """

    rows: list[list[str]]
    header_rows: int = 1

    def __post_init__(self) -> None:
        self.rows = [[normalize_text(str(cell)) for cell in row] for row in self.rows]
        if self.header_rows < 0:
            raise ValueError("header_rows must be >= 0")
        if self.rows and self.header_rows > len(self.rows):
            raise ValueError("header_rows exceeds row count")
        width = max((len(row) for row in self.rows), default=0)
        for row in self.rows:
            if len(row) != width:
                raise ValueError("table rows must be rectangular")

    @property
    def header(self) -> list[str]:
        return self.rows[0] if self.header_rows and self.rows else []

    def to_markdown(self, rows: list[list[str]] | None = None) -> str:
        body = self.rows if rows is None else rows
        if not body:
            return ""
        header_count = min(self.header_rows, len(body))
        lines: list[str] = []
        if header_count:
            header = body[0]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join("---" for _ in header) + " |")
            rest = body[1:]
        else:
            cols = max(len(row) for row in body)
            lines.append("| " + " | ".join(f"c{i + 1}" for i in range(cols)) + " |")
            lines.append("| " + " | ".join("---" for _ in range(cols)) + " |")
            rest = body
        for row in rest:
            lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
        return "\n".join(lines)


@dataclass
class Block:
    kind: BlockKind
    text: str = ""
    level: int | None = None
    page: int | None = None
    table: Table | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = normalize_text(self.text)
        if self.kind == "table":
            if self.table is None:
                raise ValueError("table blocks require a Table")
        elif self.kind == "heading":
            if not self.level or self.level < 1:
                raise ValueError("heading blocks require level >= 1")
            if not self.text.strip():
                raise ValueError("heading blocks require text")

    def as_markdown(self) -> str:
        if self.kind == "table":
            return self.table.to_markdown() if self.table else ""
        if self.kind == "heading":
            return f"{'#' * (self.level or 1)} {self.text.strip()}"
        if self.kind == "code":
            return f"```\n{self.text.rstrip()}\n```"
        return self.text.strip()


@dataclass
class Acl:
    organization_id: str
    owner_id: str
    allowed_roles: tuple[str, ...] = ("member", "admin")

    def as_payload(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "owner_id": self.owner_id,
            "allowed_roles": list(self.allowed_roles),
        }


@dataclass
class Document:
    id: str
    source_uri: str
    title: str
    content_hash: str
    blocks: list[Block]
    acl: Acl
    source_type: str = "file"
    mime: str = "text/plain"
    parser: str = "text"
    parser_version: str = "0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def render_markdown(self) -> str:
        parts = [block.as_markdown() for block in self.blocks]
        return "\n\n".join(part for part in parts if part).strip()

    def heading_paths(self) -> list[str]:
        """Return the heading path for every block, in block order."""
        paths: list[str] = []
        stack: list[tuple[int, str]] = []
        for block in self.blocks:
            if block.kind == "heading":
                level = block.level or 1
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, block.text.strip()))
            paths.append(" > ".join(text for _, text in stack))
        return paths


@dataclass
class Chunk:
    ordinal: int
    text: str
    heading_path: str = ""
    page: int | None = None
    kind: str = "text"
    token_estimate: int = 0

    def __post_init__(self) -> None:
        self.text = normalize_text(self.text)
        if self.ordinal < 1:
            raise ValueError("chunk ordinal starts at 1")
        if not self.text.strip():
            raise ValueError("chunk text must not be empty")
        if not self.token_estimate:
            # Rough proxy: Korean/Latin mix averages ~2.2 characters per token.
            self.token_estimate = max(1, round(len(self.text) / 2.2))


def content_hash(payload: bytes, parser: str, parser_version: str) -> str:
    """Stable hash used for deduplication and re-index decisions."""
    digest = hashlib.sha256()
    digest.update(parser.encode("utf-8"))
    digest.update(b"@")
    digest.update(parser_version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(payload)
    return "sha256:" + digest.hexdigest()


def document_id_for(source_uri: str, content_hash_value: str) -> str:
    """Deterministic id: re-ingesting identical content keeps the same id."""
    return "doc_" + uuid.uuid5(uuid.NAMESPACE_URL, f"{source_uri}|{content_hash_value}").hex[:24]


def new_document(
    *,
    source_uri: str,
    title: str,
    blocks: list[Block],
    acl: Acl,
    payload: bytes = b"",
    parser: str,
    parser_version: str = "1",
    source_type: str = "file",
    mime: str = "text/plain",
    metadata: dict[str, Any] | None = None,
) -> Document:
    hashed = content_hash(payload, parser, parser_version)
    return Document(
        id=document_id_for(source_uri, hashed),
        source_uri=source_uri,
        title=title,
        content_hash=hashed,
        blocks=blocks,
        acl=acl,
        source_type=source_type,
        mime=mime,
        parser=parser,
        parser_version=parser_version,
        metadata=dict(metadata or {}),
    )


def idempotency_key(document: Document, embedding_model: str) -> str:
    """A batch may be retried; this key makes the sink idempotent."""
    raw = "|".join(
        [
            document.source_uri,
            document.content_hash,
            f"{document.parser}@{document.parser_version}",
            embedding_model,
            str(document.acl),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class _Buffer:
    """Accumulates text blocks until a size limit, then flushes to a chunk."""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars
        self.parts: list[str] = []
        self.size = 0
        self.heading_path = ""
        self.page: int | None = None
        self.last_flushed: tuple[str, str, int | None] | None = None

    def add(self, text: str, heading_path: str, page: int | None) -> str | None:
        """Return finished chunk text when the buffer overflows."""
        text = text.strip()
        if not text:
            return None
        overflow = bool(self.parts) and self.size + len(text) > self.max_chars
        finished = self.flush() if overflow else None
        if not self.parts:
            self.heading_path = heading_path
            self.page = page
        self.parts.append(text)
        self.size += len(text)
        return finished

    def flush(self) -> str | None:
        if not self.parts:
            return None
        body = "\n\n".join(self.parts)
        text = f"{self.heading_path}\n\n{body}" if self.heading_path else body
        self.last_flushed = (text, self.heading_path, self.page)
        self.parts = []
        self.size = 0
        self.heading_path = ""
        self.page = None
        return text

    def take_flushed(self) -> tuple[str, str, int | None] | None:
        """Return and clear metadata for the most recent flush."""
        flushed = self.last_flushed
        self.last_flushed = None
        return flushed


def chunk_document(document: Document, max_chars: int = 1200, table_rows: int = 20) -> list[Chunk]:
    """Structure-aware chunking.

    - headings become the chunk's `heading_path`, not a chunk of their own
    - paragraphs accumulate up to `max_chars`
    - tables split into row groups with the header repeated in every chunk
    - code blocks stay whole
    """
    if max_chars < 100:
        raise ValueError("max_chars must be >= 100")

    chunks: list[Chunk] = []
    buffer = _Buffer(max_chars)
    paths = document.heading_paths()

    def emit(text: str, heading_path: str, page: int | None, kind: str = "text") -> None:
        if not text or not text.strip():
            return
        chunks.append(
            Chunk(ordinal=len(chunks) + 1, text=text, heading_path=heading_path, page=page, kind=kind)
        )

    def drain(kind: str = "text") -> None:
        text = buffer.flush()
        flushed = buffer.take_flushed()
        if text and flushed:
            emit(text, flushed[1], flushed[2], kind)

    for index, block in enumerate(document.blocks):
        path = paths[index]
        if block.kind == "heading":
            drain()
            continue
        if block.kind == "table" and block.table:
            drain()
            emit_table(block, block.table, path, emit, max_chars, table_rows)
            continue
        if block.kind == "code":
            drain()
            text = block.as_markdown()
            if path:
                text = f"{path}\n\n{text}"
            emit(text, path, block.page, kind="code")
            continue

        finished = buffer.add(block.text, path, block.page)
        if finished:
            flushed = buffer.take_flushed()
            if flushed:
                emit(finished, flushed[1], flushed[2])
        if block.kind == "caption":
            drain(kind="caption")

    drain()
    if not chunks:
        text = document.render_markdown()
        if text:
            emit(text, "", None)
    return chunks


def emit_table(
    block: Block,
    table: Table,
    heading_path: str,
    emit,
    max_chars: int,
    table_rows: int,
) -> None:
    header = table.header
    rows = table.rows[table.header_rows:] if table.header_rows else table.rows
    group: list[list[str]] = []
    size = len(table.to_markdown([header])) if header else 0
    for row in rows:
        row_len = len("| " + " | ".join(row) + " |")
        if group and (size + row_len > max_chars or len(group) >= table_rows):
            emit(_table_chunk(heading_path, table, header, group), heading_path, block.page, kind="table")
            group, size = [], 0
        group.append(row)
        size += row_len
    if group or not rows:
        emit(_table_chunk(heading_path, table, header, group), heading_path, block.page, kind="table")


def _table_chunk(heading_path: str, table: Table, header: list[str], rows: list[list[str]]) -> str:
    body = table.to_markdown(([header] if header else []) + rows)
    return f"{heading_path}\n\n{body}" if heading_path else body
