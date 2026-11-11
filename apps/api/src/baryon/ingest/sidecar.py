"""Adapters for external parsers: HTTP sidecars and CLI workers.

Why sidecars
------------
Parsers such as kordoc (Node 20), Tika (JVM), and docling (heavy ML models)
should not run inside the API process. They are untrusted-input consumers, they
carry their own runtimes, and they can block for minutes. A sidecar gives us a
timeout, a process boundary, and an independent sandbox.

Contract
--------
HTTP:  POST {base}{endpoint}  multipart `file`
       -> JSON {"blocks": [...]} | {"markdown": "..."} | text/markdown
Tika:  PUT {base}/tika  (raw bytes, Accept: text/plain) -> text/plain

CLI:   `{command}` with `{input}`/`{output}` placeholders substituted.
       mode=stdout_json       -> command prints parser JSON (kordoc --format json)
       mode=output_dir        -> command writes markdown into {output} (docling)

Every failure is a `ParseError`, so the registry can fall through to the next
candidate and finally dead-letter the document.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ir import Acl, Block, Document, Table, new_document
from .mime import sniff_mime
from .parsers import ParseError, parse_markdown

_KIND_ALIASES = {
    "heading": "heading",
    "title": "heading",
    "paragraph": "paragraph",
    "text": "paragraph",
    "list": "list",
    "list_item": "list",
    "table": "table",
    "code": "code",
    "codeblock": "code",
    "caption": "caption",
    "figure_caption": "caption",
}


def _document_from_blocks(
    blocks: list[Block],
    *,
    payload: bytes,
    filename: str,
    acl: Acl,
    source_uri: str,
    parser: str,
    parser_version: str,
    mime: str,
    metadata: dict[str, Any] | None = None,
) -> Document:
    if not blocks:
        raise ParseError("parser returned no blocks")
    return new_document(
        source_uri=source_uri,
        title=filename,
        blocks=blocks,
        acl=acl,
        payload=payload,
        parser=parser,
        parser_version=parser_version,
        mime=mime,
        metadata=metadata,
    )


def blocks_from_json(items: list[dict[str, Any]]) -> list[Block]:
    blocks: list[Block] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_kind = str(item.get("kind") or item.get("type") or "paragraph").lower()
        kind = _KIND_ALIASES.get(raw_kind)
        if kind is None:
            kind = "paragraph"

        text = str(item.get("text") or item.get("content") or item.get("markdown") or "").strip()
        page = item.get("page") if isinstance(item.get("page"), int) else None
        meta: dict[str, Any] = {}
        if item.get("bbox"):
            meta["bbox"] = item["bbox"]

        if kind == "table":
            rows = item.get("rows") or (item.get("table") or {}).get("rows")
            if rows:
                width = max(len(row) for row in rows)
                normalized = [[str(cell) for cell in row] + [""] * (width - len(row)) for row in rows]
                header_rows = int(item.get("header_rows") or (item.get("table") or {}).get("header_rows") or 1)
                blocks.append(
                    Block("table", page=page, table=Table(rows=normalized, header_rows=header_rows), meta=meta)
                )
                continue
            # A parser that only returns table markdown degrades to text.
            if text:
                blocks.append(Block("paragraph", text=text, page=page, meta=meta))
            continue

        if kind == "heading":
            level = int(item.get("level") or item.get("depth") or 1)
            if not text:
                continue
            blocks.append(Block("heading", text=text, level=max(1, min(level, 6)), page=page, meta=meta))
            continue

        if text:
            blocks.append(Block(kind, text=text, page=page, meta=meta))  # type: ignore[arg-type]
    return blocks


def parse_response(
    body: bytes,
    content_type: str,
    *,
    payload: bytes,
    filename: str,
    acl: Acl,
    source_uri: str,
    parser: str,
    parser_version: str,
    mime: str,
) -> Document:
    """Normalize a sidecar response into a Document."""
    if "json" in content_type:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as error:
            raise ParseError("sidecar returned invalid JSON") from error
        if isinstance(data, dict):
            data = data.get("result") or data.get("data") or data
            if data.get("success") is False:
                raise ParseError(str(data.get("error") or "parser reported failure"))
            if isinstance(data.get("blocks"), list):
                blocks = blocks_from_json(data["blocks"])
                return _document_from_blocks(
                    blocks,
                    payload=payload,
                    filename=filename,
                    acl=acl,
                    source_uri=source_uri,
                    parser=parser,
                    parser_version=parser_version,
                    mime=mime,
                    metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
                )
            for key in ("markdown", "content", "text"):
                if isinstance(data.get(key), str) and data[key].strip():
                    return _from_markdown(
                        data[key], payload=payload, filename=filename, acl=acl, source_uri=source_uri,
                        parser=parser, parser_version=parser_version, mime=mime,
                    )
        raise ParseError("sidecar JSON had no blocks or markdown")

    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise ParseError("sidecar returned an empty body")
    return _from_markdown(
        text, payload=payload, filename=filename, acl=acl, source_uri=source_uri,
        parser=parser, parser_version=parser_version, mime=mime,
    )


def _from_markdown(
    text: str,
    *,
    payload: bytes,
    filename: str,
    acl: Acl,
    source_uri: str,
    parser: str,
    parser_version: str,
    mime: str,
) -> Document:
    markdown_doc = parse_markdown(text.encode("utf-8"), filename, acl, source_uri)
    return _document_from_blocks(
        markdown_doc.blocks,
        payload=payload,
        filename=filename,
        acl=acl,
        source_uri=source_uri,
        parser=parser,
        parser_version=parser_version,
        mime=mime,
    )


@dataclass(frozen=True)
class HttpSidecarParser:
    name: str
    base_url: str
    endpoint: str = "/parse"
    timeout_s: float = 180.0
    version: str = "1"

    def available(self) -> bool:
        return bool(self.base_url)

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.endpoint.lstrip("/")

    def __call__(self, payload: bytes, filename: str, acl: Acl, source_uri: str) -> Document:
        return self.parse(payload, filename, acl, source_uri, mime="application/octet-stream")

    def parse(self, payload: bytes, filename: str, acl: Acl, source_uri: str, mime: str = "") -> Document:
        mime = mime or sniff_mime(filename, payload)
        try:
            import httpx
        except ModuleNotFoundError as error:  # pragma: no cover - env guard
            raise ParseError("httpx is required for HTTP sidecar parsers") from error
        try:
            response = httpx.post(
                self.url,
                files={"file": (filename, payload, mime or "application/octet-stream")},
                timeout=self.timeout_s,
            )
        except Exception as error:  # noqa: BLE001 - network boundary
            raise ParseError(f"{self.name} sidecar unreachable: {type(error).__name__}") from error
        if response.status_code >= 400:
            raise ParseError(f"{self.name} sidecar returned HTTP {response.status_code}")
        return parse_response(
            response.content,
            response.headers.get("content-type", ""),
            payload=payload,
            filename=filename,
            acl=acl,
            source_uri=source_uri,
            parser=self.name,
            parser_version=self.version,
            mime=mime,
        )


@dataclass(frozen=True)
class TikaParser:
    name: str = "tika"
    base_url: str = ""
    timeout_s: float = 120.0
    version: str = "1"

    def available(self) -> bool:
        return bool(self.base_url)

    def __call__(self, payload: bytes, filename: str, acl: Acl, source_uri: str) -> Document:
        return self.parse(payload, filename, acl, source_uri, mime="application/octet-stream")

    def parse(self, payload: bytes, filename: str, acl: Acl, source_uri: str, mime: str = "") -> Document:
        mime = mime or sniff_mime(filename, payload)
        try:
            import httpx
        except ModuleNotFoundError as error:  # pragma: no cover
            raise ParseError("httpx is required for the Tika parser") from error
        url = self.base_url.rstrip("/") + "/tika"
        try:
            response = httpx.put(
                url,
                content=payload,
                headers={
                    "Accept": "text/plain",
                    "Content-Type": mime or "application/octet-stream",
                    "Content-Disposition": f'attachment; filename="{Path(filename).name}"',
                },
                timeout=self.timeout_s,
            )
        except Exception as error:  # noqa: BLE001
            raise ParseError(f"tika unreachable: {type(error).__name__}") from error
        if response.status_code >= 400:
            raise ParseError(f"tika returned HTTP {response.status_code}")
        text = response.text.strip()
        if not text:
            raise ParseError("tika returned no text")
        return _from_markdown(
            text, payload=payload, filename=filename, acl=acl, source_uri=source_uri,
            parser=self.name, parser_version=self.version, mime=mime,
        )


@dataclass(frozen=True)
class CliParser:
    """Run a parser binary in a subprocess.

    mode="stdout_json": the command prints the sidecar JSON contract.
    mode="output_dir":  the command writes markdown files into {output}.
    """

    name: str
    command: tuple[str, ...]
    mode: str = "stdout_json"
    timeout_s: float = 180.0
    version: str = "1"
    suffix: str = ".bin"

    def available(self) -> bool:
        return bool(self.command) and shutil.which(self.command[0]) is not None

    def __call__(self, payload: bytes, filename: str, acl: Acl, source_uri: str) -> Document:
        return self.parse(payload, filename, acl, source_uri, mime="application/octet-stream")

    def parse(self, payload: bytes, filename: str, acl: Acl, source_uri: str, mime: str = "") -> Document:
        mime = mime or sniff_mime(filename, payload)
        with tempfile.TemporaryDirectory(prefix="baryon-parse-") as workdir:
            directory = Path(workdir)
            input_path = directory / (Path(filename).stem + (Path(filename).suffix or self.suffix))
            input_path.write_bytes(payload)
            output_dir = directory / "out"
            output_dir.mkdir()
            argv = [
                part.replace("{input}", str(input_path)).replace("{output}", str(output_dir))
                for part in self.command
            ]
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    timeout=self.timeout_s,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ParseError(f"{self.name} failed to run: {type(error).__name__}") from error
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", "replace").strip()[:400]
                raise ParseError(f"{self.name} exited {completed.returncode}: {detail}")

            if self.mode == "output_dir":
                produced = sorted(output_dir.rglob("*.md"))
                if not produced:
                    raise ParseError(f"{self.name} produced no markdown")
                text = produced[0].read_text(encoding="utf-8", errors="replace")
                return _from_markdown(
                    text, payload=payload, filename=filename, acl=acl, source_uri=source_uri,
                    parser=self.name, parser_version=self.version, mime=mime,
                )

            return parse_response(
                completed.stdout,
                "application/json",
                payload=payload,
                filename=filename,
                acl=acl,
                source_uri=source_uri,
                parser=self.name,
                parser_version=self.version,
                mime=mime,
            )
