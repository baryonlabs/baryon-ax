"""docling HTTP sidecar for Baryon AX ingest.

    GET  /health -> {"status":"ok","parser":"docling","version":...}
    POST /parse  multipart `file` -> {"markdown":"...","metadata":{"page_count":N}}

docling's native output is a `DoclingDocument`; Markdown is the most stable
representation across versions and preserves headings and tables (GFM) that the
ingest pipeline needs. The API adapter parses the Markdown back into blocks, so
this service stays a thin wrapper.

The first conversion downloads the layout, table and OCR models into /models (DOCLING_ARTIFACTS_PATH).
Mount a volume there to keep restarts offline; DOCLING_DOWNLOAD_MODELS=0 skips the download when the
models are copied in by hand (air-gapped installs).
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


def ensure_models() -> None:
    """With DOCLING_ARTIFACTS_PATH set, docling only reads models from that folder and never downloads
    them, so a fresh /models volume made every PDF fail ("RapidOCR artifacts not found"). Download the
    layout, table and OCR models into it once; later starts find them and stay offline.
    DOCLING_DOWNLOAD_MODELS=0 skips this for air-gapped installs that copy the models in themselves."""
    path = os.getenv("DOCLING_ARTIFACTS_PATH", "")
    if not path or os.getenv("DOCLING_DOWNLOAD_MODELS", "1") == "0":
        return
    from pathlib import Path

    from docling.utils.model_downloader import download_models

    logger.info("making sure docling models are in %s (first run downloads them)", path)
    download_models(output_dir=Path(path))  # skips what is already there


def converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter

        ensure_models()
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
