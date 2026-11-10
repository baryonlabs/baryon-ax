"""Tests for the ingest IR, parser registry, and Broadway-style pipeline.

Dependency-free (stdlib + pytest + pytest-asyncio), so they run without the
full API stack.
"""

import asyncio
import hashlib

import pytest

from baryon.ingest import (
    Acl,
    Block,
    Document,
    NoParserError,
    ParserRegistry,
    ParserSpec,
    ParseError,
    Pipeline,
    PipelineConfig,
    REGISTRY,
    Table,
    chunk_document,
    content_hash,
    idempotency_key,
    new_document,
    parse_markdown,
    parse_rows,
    parse_text,
)

ACL = Acl(organization_id="org_acme", owner_id="user_1", allowed_roles=("member", "admin"))

MARKDOWN = """# 2026년 예산 지침

## 1. 총칙

이 지침은 예산 편성 절차를 정한다.

| 구분 | 2025 | 2026 |
| --- | --- | --- |
| 인건비 | 100 | 110 |
| 운영비 | 50 | 55 |
| 사업비 | 200 | 240 |

- 적용 범위
- 시행 일자

```python
print("ok")
```
"""


def test_markdown_parser_builds_typed_blocks() -> None:
    document = parse_markdown(MARKDOWN.encode(), "budget.md", ACL, "mem://budget.md")
    kinds = [block.kind for block in document.blocks]
    assert kinds == ["heading", "heading", "paragraph", "table", "list", "code"]
    assert document.blocks[0].level == 1
    assert document.blocks[1].level == 2
    assert document.blocks[3].table is not None
    assert document.blocks[3].table.header == ["구분", "2025", "2026"]
    assert document.parser == "markdown"
    assert document.content_hash.startswith("sha256:")


def test_heading_paths_persist_across_blocks() -> None:
    document = parse_markdown(MARKDOWN.encode(), "budget.md", ACL, "mem://budget.md")
    paths = document.heading_paths()
    assert paths[0] == "2026년 예산 지침"
    assert paths[1] == "2026년 예산 지침 > 1. 총칙"
    assert paths[3] == "2026년 예산 지침 > 1. 총칙"
    assert paths[4] == "2026년 예산 지침 > 1. 총칙"


def test_table_chunks_repeat_header() -> None:
    document = parse_markdown(MARKDOWN.encode(), "budget.md", ACL, "mem://budget.md")
    chunks = chunk_document(document, max_chars=120, table_rows=1)
    table_chunks = [chunk for chunk in chunks if chunk.kind == "table"]
    assert len(table_chunks) >= 3
    for chunk in table_chunks:
        assert "| 구분 | 2025 | 2026 |" in chunk.text
        assert chunk.heading_path == "2026년 예산 지침 > 1. 총칙"


def test_chunk_ordinals_are_contiguous() -> None:
    document = parse_markdown(MARKDOWN.encode(), "budget.md", ACL, "mem://budget.md")
    chunks = chunk_document(document)
    assert [chunk.ordinal for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(chunk.token_estimate > 0 for chunk in chunks)


def test_registry_prefers_markdown_and_falls_back_to_text() -> None:
    document = REGISTRY.parse(b"# hi\n\nbody", "a.md", ACL, "mem://a.md")
    assert document.parser == "markdown"

    plain = REGISTRY.parse(b"hello\n\nworld", "a.txt", ACL, "mem://a.txt")
    assert plain.parser == "text"

    fallback = REGISTRY.parse("한글 문서".encode(), "unknown.bin", ACL, "mem://unknown.bin")
    assert fallback.parser == "text"


def test_registry_raises_when_all_parsers_fail() -> None:
    with pytest.raises(ParseError):
        REGISTRY.parse(b"\xff\xfe\x00binary", "scan.pdf", ACL, "mem://scan.pdf")


def test_empty_registry_raises_no_parser() -> None:
    with pytest.raises(NoParserError):
        ParserRegistry().parse(b"x", "a.md", ACL, "mem://a.md")


def test_registry_is_extensible_without_touching_callers() -> None:
    registry = ParserRegistry()

    def parse_custom(payload: bytes, filename: str, acl: Acl, source_uri: str) -> Document:
        return new_document(
            source_uri=source_uri,
            title=filename,
            blocks=[Block("paragraph", text=payload.decode())],
            acl=acl,
            payload=payload,
            parser="custom",
        )

    registry.register(ParserSpec("custom", (".x",), ("application/x-custom",), parse_custom))
    document = registry.parse(b"payload", "file.x", ACL, "mem://file.x")
    assert document.parser == "custom"


def test_content_hash_depends_on_parser_version() -> None:
    payload = b"same bytes"
    assert content_hash(payload, "docling", "1") != content_hash(payload, "docling", "2")
    assert content_hash(payload, "docling", "1") == content_hash(payload, "docling", "1")


def test_document_id_is_deterministic() -> None:
    first = parse_text(b"same", "a.txt", ACL, "mem://a.txt")
    second = parse_text(b"same", "a.txt", ACL, "mem://a.txt")
    assert first.id == second.id
    assert idempotency_key(first, "fastembed:mini") == idempotency_key(second, "fastembed:mini")


def test_table_validates_shape() -> None:
    with pytest.raises(ValueError):
        Table(rows=[["a", "b"], ["c"]])
    with pytest.raises(ValueError):
        Block("table")


def test_parse_rows_produces_database_document() -> None:
    document = parse_rows(
        table="vendor_invoice",
        columns={
            "id": {"type": "bigint", "primary_key": True},
            "amount": {"type": "numeric(14,2)", "description": "청구 금액"},
            "currency": {"type": "char(3)"},
        },
        rows=[{"id": 88421, "amount": 1250000, "currency": "KRW"}],
        acl=ACL,
        source_uri="postgres://erp/public.vendor_invoice#id=88421",
    )
    assert document.source_type == "database"
    assert document.title == "vendor_invoice (id=88421)"
    assert "테이블 public.vendor_invoice" in document.blocks[0].text
    assert "청구 금액" in document.blocks[0].text
    assert document.blocks[1].table is not None
    assert document.blocks[1].table.rows[1] == ["88421", "1250000", "KRW"]

    chunks = chunk_document(document)
    assert chunks and "vendor_invoice" in chunks[0].text


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_batches_by_size() -> None:
    seen: list[int] = []

    async def handler(batch: list[int]) -> None:
        seen.append(len(batch))

    stats = await Pipeline(handler, PipelineConfig(batch_size=4, batch_timeout_s=0.05)).run(range(8))

    assert seen == [4, 4]
    assert stats.processed == 8
    assert stats.batches == 2
    assert stats.failed == 0


@pytest.mark.asyncio
async def test_pipeline_batches_by_timeout() -> None:
    seen: list[int] = []

    async def handler(batch: list[int]) -> None:
        seen.append(len(batch))

    stats = await Pipeline(handler, PipelineConfig(batch_size=32, batch_timeout_s=0.02)).run([1, 2, 3])

    assert seen == [3]
    assert stats.processed == 3


@pytest.mark.asyncio
async def test_pipeline_applies_backpressure() -> None:
    async def handler(batch: list[int]) -> None:
        await asyncio.sleep(0.01)

    config = PipelineConfig(queue_maxsize=2, batch_size=1, max_in_flight=1, batch_timeout_s=0.01)
    stats = await Pipeline(handler, config).run(range(20))

    assert stats.processed == 20
    assert stats.max_queue_depth <= config.queue_maxsize
    assert stats.peak_in_flight <= config.max_in_flight


@pytest.mark.asyncio
async def test_pipeline_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    async def handler(batch: list[int]) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")

    config = PipelineConfig(batch_size=4, batch_timeout_s=0.01, max_retries=3, backoff_s=0.001)
    stats = await Pipeline(handler, config).run([1, 2])

    assert attempts["n"] == 3
    assert stats.processed == 2
    assert stats.retries == 2
    assert stats.failed == 0


@pytest.mark.asyncio
async def test_pipeline_dead_letters_after_retries() -> None:
    async def handler(batch: list[int]) -> None:
        raise ValueError("permanent")

    config = PipelineConfig(batch_size=2, batch_timeout_s=0.01, max_retries=2, backoff_s=0.001)
    stats = await Pipeline(handler, config).run([1, 2])

    assert stats.processed == 0
    assert stats.failed == 2
    assert {letter.item for letter in stats.dead_letters} == {1, 2}
    assert all(letter.attempts == 3 for letter in stats.dead_letters)
    assert "permanent" in stats.dead_letters[0].error


@pytest.mark.asyncio
async def test_pipeline_stop_drains_in_flight() -> None:
    pipeline: Pipeline[int] | None = None
    handled: list[int] = []

    async def handler(batch: list[int]) -> None:
        assert pipeline is not None
        handled.extend(batch)
        pipeline.stop()
        await asyncio.sleep(0.01)

    config = PipelineConfig(queue_maxsize=1, batch_size=1, max_in_flight=2, batch_timeout_s=0.01)
    pipeline = Pipeline(handler, config)
    stats = await asyncio.wait_for(pipeline.run(range(200)), timeout=5)

    assert stats.produced < 200
    assert stats.processed == stats.produced == len(handled)
    assert stats.failed == 0


@pytest.mark.asyncio
async def test_pipeline_reports_producer_error() -> None:
    async def source():
        yield 1
        raise RuntimeError("source down")

    async def handler(batch: list[int]) -> None:
        return None

    stats = await Pipeline(handler, PipelineConfig(batch_timeout_s=0.01)).run(source())

    assert stats.processed == 1
    assert stats.producer_error is not None
    assert "source down" in stats.producer_error
