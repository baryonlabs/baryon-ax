"""Document/DB ingest: IR, parser registry, and a Broadway-style pipeline.

Public surface:
    from baryon.ingest import Acl, Block, Table, chunk_document, REGISTRY, Pipeline
"""

from .db import (
    ColumnProfile,
    TableProfile,
    documents_from_sqlalchemy,
    documents_from_sqlite,
    introspect_sqlalchemy,
    introspect_sqlite,
    rows_to_documents,
)
from .ir import (
    Acl,
    Block,
    Chunk,
    Document,
    Table,
    chunk_document,
    content_hash,
    document_id_for,
    idempotency_key,
    new_document,
)
from .mime import MIME_EXTENSIONS, EXTENSION_MIME, sniff_mime, suggested_extension
from .parsers import (
    NoParserError,
    ParserRegistry,
    ParserSpec,
    ParseError,
    REGISTRY,
    parse_markdown,
    parse_rows,
    parse_text,
)
from .pipeline import (
    DeadLetter,
    PermanentError,
    Pipeline,
    PipelineConfig,
    PipelineStats,
    run_pipeline,
)

__all__ = [
    "Acl",
    "ColumnProfile",
    "TableProfile",
    "documents_from_sqlalchemy",
    "documents_from_sqlite",
    "introspect_sqlalchemy",
    "introspect_sqlite",
    "rows_to_documents",
    "Block",
    "Chunk",
    "Document",
    "Table",
    "chunk_document",
    "content_hash",
    "document_id_for",
    "idempotency_key",
    "new_document",
    "NoParserError",
    "ParserRegistry",
    "ParserSpec",
    "ParseError",
    "REGISTRY",
    "EXTENSION_MIME",
    "MIME_EXTENSIONS",
    "sniff_mime",
    "suggested_extension",
    "parse_markdown",
    "parse_rows",
    "parse_text",
    "DeadLetter",
    "PermanentError",
    "Pipeline",
    "PipelineConfig",
    "PipelineStats",
    "run_pipeline",
]
