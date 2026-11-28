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

OCR (scanned PDFs, photos, screenshots) uses RapidOCR with the Korean recognizer by default; the stock
recognizer is Chinese/English and drops Hangul entirely. DOCLING_OCR_LANG picks another PP-OCR language
(english, chinese, japan, latin, ...), DOCLING_OCR_BACKEND the engine (torch is in the image; onnxruntime
needs that package), and DOCLING_OCR_ORIENTATION=1 turns the 180° text classifier back on. It is off
because it flipped "9,000원" into "공000'6" on ordinary upright receipts.
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

OCR_LANG = os.getenv("DOCLING_OCR_LANG", "korean").strip() or "korean"
OCR_BACKEND = os.getenv("DOCLING_OCR_BACKEND", "torch").strip() or "torch"
OCR_ORIENTATION = os.getenv("DOCLING_OCR_ORIENTATION", "0") == "1"


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
    # The Korean recognizer is not in the default set; ask for it (with the detector it pairs with).
    download_models(output_dir=Path(path), rapidocr_models=[f"{OCR_BACKEND}:{OCR_LANG}"])  # skips what is there


def pipeline_options():
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(lang=[OCR_LANG], backend=OCR_BACKEND, use_cls=OCR_ORIENTATION)
    return options


def converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

        ensure_models()
        logger.info("initializing docling converter (OCR %s on %s)", OCR_LANG, OCR_BACKEND)
        options = pipeline_options()
        _converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options),
                                                       InputFormat.IMAGE: ImageFormatOption(pipeline_options=options)})
    return _converter


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "parser": "docling",
        "version": VERSION,
        "artifacts": os.getenv("DOCLING_ARTIFACTS_PATH", ""),
        "ocr": {"lang": OCR_LANG, "backend": OCR_BACKEND},
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
