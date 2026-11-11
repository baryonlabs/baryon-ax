"""docling HTTP sidecar for Baryon AX ingest.

    GET  /health -> {"status":"ok","parser":"docling","version":...}
    POST /parse  multipart `file` -> {"markdown":"...","metadata":{"page_count":N}}

docling's native output is a `DoclingDocument`; Markdown is the most stable
representation across versions and preserves headings and tables (GFM) that the
ingest pipeline needs. The API adapter parses the Markdown back into blocks, so
this service stays a thin wrapper.

First conversion downloads layout/table models into the container cache. Mount
a volume at /models (DOCLING_ARTIFACTS_PATH) to keep restarts offline.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

logger = logging.getLogger("docling.sidecar")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

MAX_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(60 * 1024 * 1024)))
VERSION = os.getenv("DOCLING_VERSION", "unknown")

app = FastAPI(title="baryon-docling", version=VERSION)
_converter = None


def converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter

        logger.info("initializing docling converter")
        _converter = DocumentConverter()
    return _converter


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "parser": "docling",
        "version": VERSION,
        "artifacts": os.getenv("DOCLING_ARTIFACTS_PATH", ""),
    }


@app.post("/parse")
def parse(file: UploadFile = File(...)) -> dict:
    payload = file.file.read()
    if not payload:
        raise HTTPException(422, "empty upload")
    if len(payload) > MAX_BYTES:
        raise HTTPException(413, f"file exceeds {MAX_BYTES} bytes")

    suffix = Path(file.filename or "upload").suffix or ".bin"
    workdir = tempfile.mkdtemp(prefix="docling-")
    target = Path(workdir) / f"upload{suffix}"
    try:
        target.write_bytes(payload)
        result = converter().convert(str(target))
        document = result.document
        markdown = document.export_to_markdown()
        if not markdown or not markdown.strip():
            raise HTTPException(422, "docling produced no markdown")
        pages = len(getattr(document, "pages", {}) or {})
        return {
            "markdown": markdown,
            "metadata": {
                "page_count": pages,
                "docling_version": VERSION,
                "filename": file.filename,
            },
        }
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - sidecar boundary
        logger.exception("docling conversion failed")
        raise HTTPException(422, f"docling failed: {type(error).__name__}: {error}") from error
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.get("/")
def root() -> dict:
    return {"service": "baryon-docling", "contract": json.dumps({"parse": "POST /parse multipart file"})}
