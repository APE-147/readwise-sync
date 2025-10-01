from __future__ import annotations

from pathlib import Path

from reader_sync.config import load_settings


def test_load_settings_defaults(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / ".rw-sync.yaml"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = load_settings(cfg)
    assert settings.watch_dir == (tmp_path / "inbox").resolve()
    assert settings.db_path == (tmp_path / "data/state/rw_sync.db").resolve()
    assert "id" in settings.keep_params


def test_load_settings_obeys_env(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "custom.yaml"
    cfg.write_text(
        """
watch:
  dir: ./html
network:
  concurrency: 3
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("READWISE_TOKEN", "token123")
    settings = load_settings(cfg)
    assert settings.watch_dir == (cfg.parent / "html").resolve()
    assert settings.concurrency == 3
    assert settings.token == "token123"
