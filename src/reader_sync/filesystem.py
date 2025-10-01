from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from .models import FileMeta


def discover_files(root: Path, patterns: Iterable[str]) -> list[FileMeta]:
    """Return sorted list of candidate HTML files."""
    metas: list[FileMeta] = []
    watch_dir = root
    if not watch_dir.exists():
        return metas
    for pattern in patterns:
        for path in sorted(watch_dir.rglob(pattern)):
            if path.is_file():
                stat = path.stat()
                birth = getattr(stat, "st_birthtime", None)
                metas.append(
                    FileMeta(
                        path=path,
                        mtime=stat.st_mtime,
                        size=stat.st_size,
                        birthtime=birth if isinstance(birth, (int, float)) else None,
                    )
                )
    return metas


def read_html(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def compute_sha1(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8"), usedforsecurity=False).hexdigest()


__all__ = ["discover_files", "read_html", "compute_sha1"]
