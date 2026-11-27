"""Bounded input contracts for host-resolved knowledge and MCP Code Mode."""

from __future__ import annotations

import base64
import binascii
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


MAX_ITEMS = 16
MAX_ENCODED_BYTES = 8_000_000
SourceKind = Literal["file", "link", "thread", "reply", "analysis", "attachment", "repository_file"]
Reference = Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class StrictInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


def source_reference(value: str) -> str:
    """Validate an inert provenance URL and drop query credentials."""
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        raise ValueError("invalid source reference")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username is not None:
        raise ValueError("source references must be HTTPS URLs without credentials")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


class KnowledgeItem(StrictInput):
    filename: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    kind: SourceKind
    source: Reference | None = None
    parent: Reference | None = None
    repository_path: Reference | None = None
    content_base64: Annotated[str, StringConstraints(min_length=4, max_length=MAX_ENCODED_BYTES)]

    @field_validator("source", "parent")
    @classmethod
    def validate_reference(cls, value: str | None) -> str | None:
        return source_reference(value) if value is not None else None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if (value in {".", ".."} or PureWindowsPath(value).name != value
                or any(char in value for char in "/\\:") or any(ord(char) < 32 for char in value)):
            raise ValueError("filename must be a single file name")
        return value

    def unpack(self) -> tuple[bytes, dict]:
        if self.kind != "file" and self.source is None:
            raise ValueError("source is required for this item kind")
        if self.kind in {"reply", "analysis", "attachment"} and self.parent is None:
            raise ValueError("parent is required for this item kind")
        if self.kind == "repository_file":
            path = self.repository_path or ""
            relative = PurePosixPath(path)
            if (not relative.parts or relative.is_absolute() or ".." in relative.parts
                    or "\\" in path or ":" in path or relative.name != self.filename):
                raise ValueError("repository_path must be a relative path ending in filename")
        elif self.repository_path is not None:
            raise ValueError("repository_path is only valid for repository files")
        try:
            payload = base64.b64decode(self.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("content_base64 is invalid") from error
        return payload, self.model_dump(exclude={"content_base64"}, exclude_none=True)


class KnowledgeSearch(StrictInput):
    operation: Literal["search"]
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=2000)]
    limit: int = Field(default=6, ge=1, le=20)


class KnowledgeRead(StrictInput):
    operation: Literal["read"]
    document_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    chunk: int = Field(ge=1)


class KnowledgeIngest(StrictInput):
    operation: Literal["ingest"]
    authorized: Literal[True]
    items: list[dict] = Field(min_length=1, max_length=MAX_ITEMS)


class KnowledgeProgram(StrictInput):
    steps: list[Annotated[KnowledgeSearch | KnowledgeRead | KnowledgeIngest, Field(discriminator="operation")]] = Field(
        min_length=1, max_length=8,
    )
