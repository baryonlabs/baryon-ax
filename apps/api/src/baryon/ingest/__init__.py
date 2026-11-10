"""Document/DB ingest: IR, parser registry, and a Broadway-style pipeline.

Public surface:
    from baryon.ingest import Acl, Block, Table, chunk_document, REGISTRY, Pipeline
"""

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
    Pipeline,
    PipelineConfig,
    PipelineStats,
    run_pipeline,
)

__all__ = [
    "Acl",
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
    "parse_markdown",
    "parse_rows",
    "parse_text",
    "DeadLetter",
    "Pipeline",
    "PipelineConfig",
    "PipelineStats",
    "run_pipeline",
]
