"""API-level ingest behaviour: async jobs, failure isolation, sidecar formats."""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("DOCUMENT_STORE", str(Path(tempfile.mkdtemp(prefix="baryon-ax-api-ingest-")) / "documents"))
os.environ.setdefault("AUDIT_LOG", str(Path(tempfile.mkdtemp(prefix="baryon-ax-api-ingest-audit-")) / "events.jsonl"))
os.environ.setdefault("SYSTEM_METRICS_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient

from baryon.ingest import REGISTRY
from baryon.ingest.adapters import register_default_adapters
from baryon.main import JOBS, IngestJob, Principal, app, documents, run_ingest_job

client = TestClient(app)


def headers(user: str = "ingest-user", organization: str = "org-ingest", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def hwpx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("version.xml", "<version/>")
        archive.writestr("Contents/section0.xml", "<section/>")
    return buffer.getvalue()


def start_sidecar() -> tuple[HTTPServer, str]:
    payload = json.dumps(
        {
            "blocks": [
                {"type": "heading", "text": "예산 지침", "level": 1},
                {"type": "paragraph", "text": "인건비 예산은 110입니다."},
            ]
        }
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_upload_rejects_unparseable_pdf_and_writes_audit() -> None:
    org = "org-ingest-pdf"
    response = client.post(
        "/v1/documents",
        files={"file": ("broken.pdf", b"%PDF-1.7\nnot really a pdf")},
        headers=headers(user="pdf-user", organization=org),
    )
    assert response.status_code == 415

    events = client.get("/v1/audit-events", headers=headers(user="pdf-admin", organization=org, roles="admin")).json()["data"]
    assert any(event["type"] == "document.parse_failed" for event in events)


def test_upload_rejects_non_utf8_markdown() -> None:
    response = client.post(
        "/v1/documents",
        files={"file": ("broken.md", b"\xff\xfe\x00")},
        headers=headers(),
    )
    assert response.status_code == 415


def test_hwpx_upload_uses_sidecar_and_is_retrievable() -> None:
    server, url = start_sidecar()
    register_default_adapters(env={"KORDOC_URL": url, "PARSER_TIMEOUT_SECONDS": "5"})
    try:
        org = "org-ingest-hwpx"
        upload = client.post(
            "/v1/documents",
            files={"file": ("budget.hwpx", hwpx_bytes(), "application/hwp+zip")},
            data={"allowed_roles": "member,admin"},
            headers=headers(user="hwpx-user", organization=org),
        )
        assert upload.status_code == 200, upload.text
        body = upload.json()
        assert body["parser"] == "kordoc"
        assert body["mime"] == "application/hwp+zip"
        assert body["chunks"] >= 1

        search = client.get("/v1/documents/search", params={"q": "인건비"}, headers=headers(user="hwpx-user", organization=org))
        assert search.status_code == 200
        assert any(item["title"] == "budget.hwpx" for item in search.json()["data"])

        chat = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "인건비 예산은?"}], "stream": False},
            headers=headers(user="hwpx-user", organization=org),
        )
        assert chat.status_code == 200
        assert chat.json()["citations"]
        assert chat.json()["citations"][0]["document_id"] == body["id"]
    finally:
        REGISTRY.unregister("kordoc")
        server.shutdown()


@pytest.mark.asyncio
async def test_async_job_runs_to_completion() -> None:
    job = IngestJob(
        id="job-test-1", organization_id="org-ingest-job", owner_id="job-user",
        filename="notes.md", mime="text/markdown", trace_id="trace-job-1",
    )
    JOBS[job.id] = job
    principal = Principal(user_id="job-user", organization_id="org-ingest-job", roles={"member"})

    await run_ingest_job(job, principal, b"# note\n\nasync body content", {"member"})

    assert job.state == "done", job.error
    assert job.chunks >= 1
    assert job.document_id in documents.documents
    assert job.finished_at


@pytest.mark.asyncio
async def test_async_job_failure_is_isolated() -> None:
    job = IngestJob(
        id="job-test-2", organization_id="org-ingest-job", owner_id="job-user",
        filename="broken.pdf", mime="application/pdf", trace_id="trace-job-2",
    )
    JOBS[job.id] = job
    principal = Principal(user_id="job-user", organization_id="org-ingest-job", roles={"member"})

    await run_ingest_job(job, principal, b"%PDF-1.7\nbroken", {"member"})

    assert job.state == "failed"
    assert "Could not parse" in job.error


def test_async_upload_returns_job_and_is_scoped_to_organization() -> None:
    org = "org-ingest-async"
    response = client.post(
        "/v1/documents?async=true",
        files={"file": ("async.md", b"async upload body")},
        headers=headers(user="async-user", organization=org),
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.time() + 10
    state = "queued"
    while time.time() < deadline:
        found = client.get(f"/v1/jobs/{job_id}", headers=headers(user="async-user", organization=org))
        assert found.status_code == 200
        state = found.json()["state"]
        if state in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert state == "done"

    other_org = client.get(f"/v1/jobs/{job_id}", headers=headers(user="async-user", organization="org-other"))
    assert other_org.status_code == 404


FORMAT_SAMPLES = {
    "report.pdf": b"%PDF-1.7\n" + b"0" * 64,
    "report.png": b"\x89PNG\r\n\x1a\n" + b"0" * 64,
    "report.docx": None,
    "report.xlsx": None,
    "report.pptx": None,
}


def _zip_with(entry: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(entry, "<xml/>")
    return buffer.getvalue()


def test_every_supported_format_routes_to_docling() -> None:
    server, url = start_sidecar()
    register_default_adapters(env={"DOCLING_URL": url, "PARSER_TIMEOUT_SECONDS": "5"})
    try:
        org = "org-ingest-formats"
        samples = dict(FORMAT_SAMPLES)
        samples["report.docx"] = _zip_with("word/document.xml")
        samples["report.xlsx"] = _zip_with("xl/workbook.xml")
        samples["report.pptx"] = _zip_with("ppt/presentation.xml")

        for filename, payload in samples.items():
            upload = client.post(
                "/v1/documents",
                files={"file": (filename, payload)},
                headers=headers(user="format-user", organization=org),
            )
            assert upload.status_code == 200, f"{filename}: {upload.text}"
            body = upload.json()
            assert body["parser"] == "docling", filename
            assert body["chunks"] >= 1, filename

        listed = client.get("/v1/documents", headers=headers(user="format-user", organization=org)).json()["data"]
        assert {item["title"] for item in listed} >= set(samples)

        search = client.get(
            "/v1/documents/search", params={"q": "인건비"}, headers=headers(user="format-user", organization=org)
        )
        assert search.status_code == 200
        # The search endpoint caps results at 4 by design; all formats are indexed.
        assert len(search.json()["data"]) == 4
    finally:
        REGISTRY.unregister("docling")
        server.shutdown()


def test_unknown_job_returns_not_found() -> None:
    assert client.get("/v1/jobs/missing", headers=headers()).status_code == 404
