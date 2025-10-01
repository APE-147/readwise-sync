from __future__ import annotations

import datetime as dt

from reader_sync.failures import append_failure, read_failures


def test_append_failure_writes_header_once(tmp_path) -> None:
    today = dt.date(2024, 1, 1)
    root = tmp_path
    append_failure(root, "https://example.com", "file.html", date=today)
    append_failure(root, "https://example.org", "file2.html", date=today)
    rows = read_failures(root, date=today)
    assert rows == [
        ("https://example.com", "file.html"),
        ("https://example.org", "file2.html"),
    ]
