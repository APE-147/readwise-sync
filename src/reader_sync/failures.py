from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from filelock import FileLock


def failure_csv_path(root: Path, *, date: dt.date | None = None) -> Path:
    date = date or dt.datetime.now().date()
    day_str = date.strftime("%Y%m%d")
    path = root / "data" / "failures" / day_str / "failed_urls.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_failure(root: Path, url: str, filename: str, *, date: dt.date | None = None) -> None:
    csv_path = failure_csv_path(root, date=date)
    lock = FileLock(str(csv_path) + ".lock")
    with lock:
        is_new = not csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if is_new:
                writer.writerow(["url", "file_name"])
            writer.writerow([url, filename])


def read_failures(root: Path, *, date: dt.date) -> list[tuple[str, str]]:
    csv_path = failure_csv_path(root, date=date)
    if not csv_path.exists():
        return []
    rows: list[tuple[str, str]] = []
    with open(csv_path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            url = row.get("url")
            name = row.get("file_name")
            if url and name:
                rows.append((url, name))
    return rows


__all__ = ["failure_csv_path", "append_failure", "read_failures"]
