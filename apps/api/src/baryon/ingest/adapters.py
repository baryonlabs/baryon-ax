"""Default parser adapters, wired from environment variables.

All adapters are optional. When nothing is configured the registry keeps the
stdlib text/markdown parsers, which is why unit tests and the Basic Core
profile work without any external runtime.

    KORDOC_URL   http://kordoc:8080        # sidecar implementing POST /parse
    KORDOC_CMD   "npx -y kordoc"           # kordoc --format json
    KORDOC_OFFICE true                     # kordoc also takes .docx/.pdf/.xlsx (after docling)
    DOCLING_URL  http://docling:5001       # sidecar implementing POST /parse
    DOCLING_CMD  "docling"                 # docling {input} --to md --output {output}
    TIKA_URL     http://tika:9998          # PUT /tika
"""

from __future__ import annotations

import os
import shutil
from typing import Mapping

from .parsers import ParserSpec, ParserRegistry, REGISTRY
from .sidecar import CliParser, HttpSidecarParser, TikaParser

KORDOC_EXTENSIONS = (".hwp", ".hwpx", ".hwpml")
KORDOC_MIMES = ("application/x-hwp", "application/hwp+zip", "application/hwpml")
# kordoc 4.x also reads these. Registered after docling, so docling wins when both
# are configured and a kordoc-only install still accepts the usual office files.
KORDOC_OFFICE_EXTENSIONS = (".docx", ".pdf", ".xlsx")
KORDOC_OFFICE_MIMES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

DOCLING_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
)
DOCLING_MIMES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/html",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/tiff",
)

TIKA_MIMES = (
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/x-ole-storage",
    "application/zip",
    "application/rtf",
)


def register_default_adapters(
    registry: ParserRegistry = REGISTRY,
    env: Mapping[str, str] | None = None,
) -> list[ParserSpec]:
    """Register every adapter that the environment can support. Idempotent."""
    env = os.environ if env is None else env
    timeout = float(env.get("PARSER_TIMEOUT_SECONDS", "180"))
    registered: list[ParserSpec] = []

    kordoc_url = env.get("KORDOC_URL", "").strip()
    kordoc_cmd = env.get("KORDOC_CMD", "").strip()
    kordoc = None
    if kordoc_url:
        kordoc = HttpSidecarParser("kordoc", kordoc_url, env.get("KORDOC_ENDPOINT", "/parse"), timeout, version="http")
        kordoc_version = kordoc.version
    elif kordoc_cmd or shutil.which("kordoc"):
        command = tuple((kordoc_cmd or "kordoc").split())
        kordoc = CliParser("kordoc", command + ("{input}", "--format", "json"), mode="stdout_json", timeout_s=timeout)
        kordoc_version = "cli"
    if kordoc is not None:
        registered.append(
            ParserSpec("kordoc", KORDOC_EXTENSIONS, KORDOC_MIMES, kordoc.parse,
                       version=kordoc_version, priority=10, available=kordoc.available)
        )
        if env.get("KORDOC_OFFICE", "true").strip().lower() not in {"0", "false", "no", "off"}:
            registered.append(
                ParserSpec("kordoc-office", KORDOC_OFFICE_EXTENSIONS, KORDOC_OFFICE_MIMES, kordoc.parse,
                           version=kordoc_version, priority=30, available=kordoc.available)
            )

    docling_url = env.get("DOCLING_URL", "").strip()
    docling_cmd = env.get("DOCLING_CMD", "").strip()
    if docling_url:
        parser = HttpSidecarParser("docling", docling_url, env.get("DOCLING_ENDPOINT", "/parse"), timeout, version="http")
        registered.append(
            ParserSpec(
                "docling",
                DOCLING_EXTENSIONS,
                DOCLING_MIMES,
                parser.parse,
                version=parser.version,
                priority=20,
                available=parser.available,
            )
        )
    elif docling_cmd or shutil.which("docling"):
        command = tuple((docling_cmd or "docling").split()) + (
            "{input}",
            "--to",
            "md",
            "--output",
            "{output}",
        )
        parser = CliParser("docling", command, mode="output_dir", timeout_s=timeout)
        registered.append(
            ParserSpec(
                "docling",
                DOCLING_EXTENSIONS,
                DOCLING_MIMES,
                parser.parse,
                version="cli",
                priority=20,
                available=parser.available,
            )
        )

    tika_url = env.get("TIKA_URL", "").strip()
    if tika_url:
        parser = TikaParser(base_url=tika_url, timeout_s=timeout)
        registered.append(
            ParserSpec(
                "tika",
                (),
                TIKA_MIMES,
                parser.parse,
                version=parser.version,
                priority=900,
                available=parser.available,
            )
        )

    for spec in registered:
        registry.register(spec)
    return registered
