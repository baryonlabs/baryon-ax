"""Content-based MIME sniffing.

Extension is a hint, not evidence: a `.pdf` may be an HWP file, and an upload
with no extension may still be a DOCX. Sniffing decides the parser, so it must
be based on bytes first and the filename second.

No third-party dependency; ZIP-based formats are inspected with `zipfile`.
"""

from __future__ import annotations

import io
import zipfile

EXTENSION_MIME = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".mdown": "text/markdown",
    ".pdf": "application/pdf",
    ".hwp": "application/x-hwp",
    ".hwpx": "application/hwp+zip",
    ".hwpml": "application/hwpml",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
}

MIME_EXTENSIONS = {
    "text/plain": (".txt",),
    "text/markdown": (".md", ".markdown", ".mdown"),
    "text/csv": (".csv",),
    "text/html": (".html", ".htm"),
    "application/json": (".json",),
    "application/pdf": (".pdf",),
    "application/x-hwp": (".hwp",),
    "application/hwp+zip": (".hwpx",),
    "application/hwpml": (".hwpml",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (".pptx",),
    "application/msword": (".doc",),
    "application/vnd.ms-excel": (".xls",),
    "application/vnd.ms-powerpoint": (".ppt",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/webp": (".webp",),
    "image/gif": (".gif",),
    "image/tiff": (".tif", ".tiff"),
}

_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE2_HINTS = (
    ("application/x-hwp", (".hwp",)),
    ("application/msword", (".doc",)),
    ("application/vnd.ms-excel", (".xls",)),
    ("application/vnd.ms-powerpoint", (".ppt",)),
)


def sniff_mime(filename: str, payload: bytes) -> str:
    """Return the most specific MIME type we can justify from the bytes."""
    head = payload[:1024]
    suffix = _suffix(filename)

    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"

    if head.startswith(_ZIP_MAGIC):
        kind = _sniff_zip(payload)
        if kind:
            return kind
        return EXTENSION_MIME.get(suffix, "application/zip")

    if head.startswith(_OLE2_MAGIC):
        for mime, extensions in _OLE2_HINTS:
            if suffix in extensions:
                return mime
        return "application/x-ole-storage"

    if _looks_like_hwpml(head):
        return "application/hwpml"

    if head.startswith(b"<?xml"):
        return EXTENSION_MIME.get(suffix, "application/xml")

    if suffix in EXTENSION_MIME and _is_probably_text(payload):
        return EXTENSION_MIME[suffix]

    if _is_probably_text(payload):
        if payload.lstrip().startswith(b"#"):
            return "text/markdown"
        return "text/plain"

    return EXTENSION_MIME.get(suffix, "application/octet-stream")


def _suffix(filename: str) -> str:
    name = (filename or "").lower()
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _sniff_zip(payload: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if "xl/workbook.xml" in names:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if "ppt/presentation.xml" in names:
                return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            if "mimetype" in names:
                try:
                    declared = archive.read("mimetype").decode("ascii", "ignore").strip()
                except (KeyError, RuntimeError):
                    declared = ""
                if declared:
                    return declared
            if any(name.startswith("Contents/") for name in names) and "version.xml" in names:
                return "application/hwp+zip"
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def _looks_like_hwpml(head: bytes) -> bool:
    if b"HWPML" not in head[:512] and b"hwpml" not in head[:512].lower():
        return False
    return head.lstrip().startswith(b"<?xml") or head.lstrip().startswith(b"<")


def _is_probably_text(payload: bytes) -> bool:
    if not payload:
        return False
    if b"\x00" in payload[:4096]:
        return False
    try:
        payload[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def suggested_extension(mime: str) -> str:
    extensions = MIME_EXTENSIONS.get(mime)
    return extensions[0] if extensions else ".bin"
