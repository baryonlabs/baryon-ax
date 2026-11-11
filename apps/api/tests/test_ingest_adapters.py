"""MIME sniffing and external parser adapters (HTTP sidecar, Tika, CLI)."""

from __future__ import annotations

import io
import json
import sys
import textwrap
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from baryon.ingest import Acl, REGISTRY, ParseError, sniff_mime
from baryon.ingest.adapters import register_default_adapters

ACL = Acl(organization_id="org_adapters", owner_id="user_1")


def hwpx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("version.xml", "<version/>")
        archive.writestr("Contents/section0.xml", "<section/>")
    return buffer.getvalue()


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# sniffing
# --------------------------------------------------------------------------


def test_sniff_mime_detects_magic_bytes() -> None:
    assert sniff_mime("a.pdf", b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_mime("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 8) == "image/png"
    assert sniff_mime("a.jpg", b"\xff\xd8\xff\xe0" + b"0" * 8) == "image/jpeg"
    assert sniff_mime("a.webp", b"RIFF" + b"\x00" * 4 + b"WEBP" + b"0" * 8) == "image/webp"


def test_sniff_mime_inspects_zip_formats() -> None:
    assert sniff_mime("doc.bin", docx_bytes()) == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert sniff_mime("unknown", hwpx_bytes()) == "application/hwp+zip"


def test_sniff_mime_handles_hwp_variants() -> None:
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    assert sniff_mime("report.hwp", ole2) == "application/x-hwp"
    assert sniff_mime("report.doc", ole2) == "application/msword"

    hwpml = '<?xml version="1.0"?><HWPML><BODY/></HWPML>'.encode()
    assert sniff_mime("report.hwpml", hwpml) == "application/hwpml"


def test_sniff_mime_falls_back_to_text_and_binary() -> None:
    assert sniff_mime("a.txt", "평범한 텍스트".encode()) == "text/plain"
    assert sniff_mime("a.md", b"# heading\n\nbody") == "text/markdown"
    assert sniff_mime("weird.bin", b"\x00\x01\x02\x03") == "application/octet-stream"


# --------------------------------------------------------------------------
# fake servers / workers
# --------------------------------------------------------------------------


def start_server(responder) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            status, content_type, body = responder("POST", self.path)
            self._reply(status, content_type, body)

        def do_PUT(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            status, content_type, body = responder("PUT", self.path)
            self._reply(status, content_type, body)

        def _reply(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


BLOCK_RESPONSE = {
    "blocks": [
        {"type": "heading", "text": "예산 지침", "level": 1},
        {"type": "paragraph", "text": "인건비 예산은 110입니다.", "page": 1},
        {
            "type": "table",
            "page": 2,
            "rows": [["구분", "2026"], ["인건비", "110"]],
            "header_rows": 1,
        },
    ],
    "metadata": {"page_count": 2},
}


def test_http_sidecar_parses_korean_document() -> None:
    server, url = start_server(lambda method, path: (200, "application/json", json.dumps(BLOCK_RESPONSE).encode()))
    register_default_adapters(env={"KORDOC_URL": url, "PARSER_TIMEOUT_SECONDS": "5"})
    try:
        document = REGISTRY.parse(hwpx_bytes(), "budget.hwpx", ACL, "mem://budget.hwpx")
        assert document.parser == "kordoc"
        assert document.mime == "application/hwp+zip"
        assert [block.kind for block in document.blocks] == ["heading", "paragraph", "table"]
        assert document.blocks[2].table is not None
        assert document.blocks[2].table.rows[1] == ["인건비", "110"]
        assert document.metadata == {"page_count": 2}
    finally:
        REGISTRY.unregister("kordoc")
        server.shutdown()


def test_kordoc_table_cell_shape_is_normalized() -> None:
    payload = {
        "blocks": [
            {
                "type": "table",
                "pageNumber": 3,
                "table": {
                    "rows": 2,
                    "cols": 2,
                    "hasHeader": True,
                    "cells": [
                        [{"text": "항목", "colSpan": 1}, {"text": "예산", "colSpan": 1}],
                        [{"text": "인건비", "colSpan": 1}, {"text": "110", "colSpan": 1}],
                    ],
                },
            }
        ],
        "markdown": "(ignored when blocks exist)",
        "metadata": {"pageCount": 1},
    }
    server, url = start_server(lambda method, path: (200, "application/json", json.dumps(payload).encode()))
    register_default_adapters(env={"KORDOC_URL": url, "PARSER_TIMEOUT_SECONDS": "5"})
    try:
        document = REGISTRY.parse(hwpx_bytes(), "form.hwpx", ACL, "mem://form.hwpx")
        table = document.blocks[0].table
        assert table is not None
        assert table.rows == [["항목", "예산"], ["인건비", "110"]]
        assert table.header_rows == 1
        assert document.blocks[0].page == 3
    finally:
        REGISTRY.unregister("kordoc")
        server.shutdown()


def test_kordoc_markdown_fallback_when_blocks_do_not_map() -> None:
    payload = {"blocks": [{"type": "unknown-type-no-text"}], "markdown": "# 제목\n\n본문입니다."}
    server, url = start_server(lambda method, path: (200, "application/json", json.dumps(payload).encode()))
    register_default_adapters(env={"KORDOC_URL": url, "PARSER_TIMEOUT_SECONDS": "5"})
    try:
        document = REGISTRY.parse(hwpx_bytes(), "fallback.hwpx", ACL, "mem://fallback.hwpx")
        assert document.blocks[0].kind == "heading"
        assert "본문입니다" in document.render_markdown()
    finally:
        REGISTRY.unregister("kordoc")
        server.shutdown()


def test_tika_adapter_reads_plain_text() -> None:
    server, url = start_server(lambda method, path: (200, "text/plain; charset=utf-8", "# 제목\n\n본문입니다.".encode()))
    register_default_adapters(env={"TIKA_URL": url, "PARSER_TIMEOUT_SECONDS": "5"})
    try:
        payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
        document = REGISTRY.parse(payload, "memo.doc", ACL, "mem://memo.doc", "application/msword")
        assert document.parser == "tika"
        assert document.blocks[0].kind == "heading"
    finally:
        REGISTRY.unregister("tika")
        server.shutdown()


def test_sidecar_failure_raises_parse_error() -> None:
    server, url = start_server(lambda method, path: (500, "text/plain", b"boom"))
    try:
        from baryon.ingest.sidecar import HttpSidecarParser

        with pytest.raises(ParseError):
            HttpSidecarParser("kordoc", url, timeout_s=5).parse(hwpx_bytes(), "budget.hwpx", ACL, "mem://budget.hwpx")
    finally:
        server.shutdown()


def test_docling_http_markdown_with_table() -> None:
    payload = {
        "markdown": "# 예산 지침\n\n| 구분 | 2026 |\n| --- | --- |\n| 인건비 | 110 |",
        "metadata": {"page_count": 1},
    }
    server, url = start_server(lambda method, path: (200, "application/json", json.dumps(payload).encode()))
    register_default_adapters(env={"DOCLING_URL": url, "PARSER_TIMEOUT_SECONDS": "5"})
    try:
        document = REGISTRY.parse(b"%PDF-1.7\n...", "budget.pdf", ACL, "mem://budget.pdf", "application/pdf")
        assert document.parser == "docling"
        assert document.blocks[0].kind == "heading"
        table = next(block.table for block in document.blocks if block.kind == "table")
        assert table.rows[1] == ["인건비", "110"]
    finally:
        REGISTRY.unregister("docling")
        server.shutdown()


def test_cli_parser_stdout_json(tmp_path) -> None:
    script = tmp_path / "fake_parser.py"
    script.write_text(
        "import json, sys\n"
        "assert sys.argv[-1] == 'json'\n"
        "print(json.dumps({'blocks': [{'type': 'paragraph', 'text': 'cli parsed'}]}))\n",
        encoding="utf-8",
    )
    register_default_adapters(env={"KORDOC_CMD": f"{sys.executable} {script}", "PARSER_TIMEOUT_SECONDS": "10"})
    try:
        document = REGISTRY.parse(hwpx_bytes(), "budget.hwpx", ACL, "mem://budget.hwpx")
        assert document.parser == "kordoc"
        assert "cli parsed" in document.render_markdown()
    finally:
        REGISTRY.unregister("kordoc")


def test_cli_parser_output_dir(tmp_path) -> None:
    script = tmp_path / "fake_docling.py"
    script.write_text(
        textwrap.dedent(
            """
            import pathlib, sys
            args = sys.argv[1:]
            target = pathlib.Path(args[args.index("--output") + 1])
            (target / "doc.md").write_text("# 회의록\\n\\n결정 사항입니다.", encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    register_default_adapters(env={"DOCLING_CMD": f"{sys.executable} {script}", "PARSER_TIMEOUT_SECONDS": "10"})
    try:
        document = REGISTRY.parse(b"%PDF-1.7\n...", "memo.pdf", ACL, "mem://memo.pdf")
        assert document.parser == "docling"
        assert document.blocks[0].kind == "heading"
        assert "결정 사항" in document.render_markdown()
    finally:
        REGISTRY.unregister("docling")


def test_unconfigured_adapters_are_skipped() -> None:
    names = [spec.name for spec in REGISTRY.candidates("a.hwpx", "application/hwp+zip")]
    assert names == []
    with pytest.raises(ParseError):
        REGISTRY.parse(hwpx_bytes(), "a.hwpx", ACL, "mem://a.hwpx")
