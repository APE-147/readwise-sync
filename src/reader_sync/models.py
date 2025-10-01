from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


TitleSource = Literal["online", "reader", "local", "unknown"]
URLSource = Literal["canonical", "singlefile", "og:url", "inferred", "synthetic"]
SyncAction = Literal["create", "update", "skip"]


@dataclass(slots=True)
class FileMeta:
    path: Path
    mtime: float
    size: int
    # Prefer creation time when available (e.g., macOS APFS). Fallback to mtime.
    birthtime: float | None = None


@dataclass(slots=True)
class PreparedDocument:
    file: FileMeta
    html: str
    original_url: str
    original_source: URLSource
    normalized_url: str
    sha1: str
    local_title: str | None


@dataclass(slots=True)
class DocumentState:
    norm_url: str
    source_url: str
    title: str | None
    title_source: TitleSource | None
    file_path: str
    file_mtime: float
    readwise_id: str | None
    last_status: int | None
    last_error: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(slots=True)
class SyncResult:
    action: SyncAction
    status_code: int | None
    readwise_id: str | None
    error: str | None
    document: PreparedDocument


@dataclass(slots=True)
class SyncStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    def summary(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
        }
