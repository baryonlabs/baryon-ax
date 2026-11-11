"""Default parser adapters, wired from environment variables.

All adapters are optional. When nothing is configured the registry keeps the
stdlib text/markdown parsers, which is why unit tests and the Basic Core
profile work without any external runtime.

    KORDOC_URL   http://kordoc:8080        # sidecar implementing POST /parse
    KORDOC_CMD   "npx -y kordoc"           # kordoc --format json
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
    if kordoc_url:
        parser = HttpSidecarParser("kordoc", kordoc_url, env.get("KORDOC_ENDPOINT", "/parse"), timeout, version="http")
        registered.append(
            ParserSpec(
                "kordoc",
                KORDOC_EXTENSIONS,
                KORDOC_MIMES,
                parser.parse,
                version=parser.version,
                priority=10,
                available=parser.available,
            )
        )
    elif kordoc_cmd or shutil.which("kordoc"):
        command = tuple((kordoc_cmd or "kordoc").split())
        parser = CliParser("kordoc", command + ("{input}", "--format", "json"), mode="stdout_json", timeout_s=timeout)
        registered.append(
            ParserSpec(
                "kordoc",
                KORDOC_EXTENSIONS,
                KORDOC_MIMES,
                parser.parse,
                version="cli",
                priority=10,
                available=parser.available,
            )
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
