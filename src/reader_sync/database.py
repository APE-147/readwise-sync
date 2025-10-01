from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import DocumentState

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    norm_url TEXT NOT NULL UNIQUE,
    source_url TEXT,
    title TEXT,
    title_source TEXT,
    file_path TEXT,
    file_mtime REAL,
    readwise_id TEXT,
    last_status INTEGER,
    last_error TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    """Thin wrapper around sqlite3 for document state persistence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def lookup(self, norm_url: str) -> DocumentState | None:
        with self.cursor() as cur:
            cur.execute(
                "SELECT norm_url, source_url, title, title_source, file_path, file_mtime, readwise_id, "
                "last_status, last_error, created_at, updated_at FROM documents WHERE norm_url = ?",
                (norm_url,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return DocumentState(
                norm_url=row["norm_url"],
                source_url=row["source_url"],
                title=row["title"],
                title_source=row["title_source"],
                file_path=row["file_path"],
                file_mtime=row["file_mtime"],
                readwise_id=row["readwise_id"],
                last_status=row["last_status"],
                last_error=row["last_error"],
                created_at=_parse_dt(row["created_at"]),
                updated_at=_parse_dt(row["updated_at"]),
            )

    def upsert(
        self,
        *,
        norm_url: str,
        source_url: str,
        title: str | None,
        title_source: str | None,
        file_path: str,
        file_mtime: float,
        readwise_id: str | None,
        last_status: int | None,
        last_error: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (
                    norm_url, source_url, title, title_source, file_path, file_mtime,
                    readwise_id, last_status, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(norm_url) DO UPDATE SET
                    source_url=excluded.source_url,
                    title=excluded.title,
                    title_source=excluded.title_source,
                    file_path=excluded.file_path,
                    file_mtime=excluded.file_mtime,
                    readwise_id=COALESCE(excluded.readwise_id, documents.readwise_id),
                    last_status=excluded.last_status,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    norm_url,
                    source_url,
                    title,
                    title_source,
                    file_path,
                    file_mtime,
                    readwise_id,
                    last_status,
                    last_error,
                    now,
                    now,
                ),
            )

    def update_status(self, norm_url: str, *, status: int | None, error: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.cursor() as cur:
            cur.execute(
                "UPDATE documents SET last_status = ?, last_error = ?, updated_at = ? WHERE norm_url = ?",
                (status, error, now, norm_url),
            )

    def close(self) -> None:
        self._conn.close()

    # --- lightweight key/value metadata ---
    def get_meta(self, key: str) -> str | None:
        with self.cursor() as cur:
            cur.execute("SELECT value FROM meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)\n"
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["Database"]
