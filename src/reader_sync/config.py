from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
import os

import yaml
from dotenv import load_dotenv


_DEFAULT_PATTERNS = ("*.html", "*.htm")
_DEFAULT_KEEP_PARAMS = ("id", "p", "page", "s", "v", "t", "q")
_DEFAULT_DROP_PARAMS = (
    "utm_",
    "gclid",
    "fbclid",
    "mkt_tok",
    "mc_cid",
    "mc_eid",
    "yclid",
    "spm",
    "ref",
)


@dataclass(slots=True)
class Settings:
    """Runtime settings resolved from YAML + environment variables."""

    root: Path
    watch_dir: Path
    patterns: tuple[str, ...]
    db_path: Path
    title_timeout: float
    concurrency: int
    rpm_save: int
    rpm_update: int
    should_clean_html: bool
    default_category: str
    keep_params: frozenset[str]
    drop_params: frozenset[str]
    token: str = ""
    log_level: str = "INFO"
    config_path: Path | None = None

    def ensure_data_dirs(self) -> None:
        """Ensure directories backing state and data exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        root_data = self.root / "data"
        for sub in ("failures", "out", "reports", "state"):
            (root_data / sub).mkdir(parents=True, exist_ok=True)

    @property
    def has_token(self) -> bool:
        return bool(self.token)


def _tuple_from(value: Iterable[str] | None, default: Sequence[str]) -> tuple[str, ...]:
    if not value:
        return tuple(default)
    return tuple(str(item) for item in value if item)


def load_settings(path: str | os.PathLike[str] | None) -> Settings:
    """Load settings from YAML file and environment variables."""
    load_dotenv()
    cfg_path = Path(path).resolve() if path else None
    data: dict[str, object] = {}

    if cfg_path:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
            if not isinstance(raw, dict):
                raise ValueError("Configuration file must contain a mapping at top level")
            data = raw
    else:
        data = {}

    root = (cfg_path.parent if cfg_path else Path.cwd()).resolve()
    watch = data.get("watch", {}) if isinstance(data, dict) else {}
    state = data.get("state", {}) if isinstance(data, dict) else {}
    net = data.get("network", {}) if isinstance(data, dict) else {}
    rw = data.get("readwise", {}) if isinstance(data, dict) else {}
    norm = data.get("url_norm", {}) if isinstance(data, dict) else {}

    watch_dir = (root / _coerce_path(watch, "dir", "./inbox")).resolve()
    # Allow environment override for watch directory to avoid committing user-specific paths.
    env_watch_dir = os.getenv("RW_SYNC_WATCH_DIR")
    if env_watch_dir:
        watch_dir = Path(env_watch_dir).expanduser().resolve()
    patterns = _tuple_from(_coerce_list(watch, "patterns"), _DEFAULT_PATTERNS)

    db_path = (root / _coerce_path(state, "db_path", "./data/state/rw_sync.db")).resolve()
    # Allow environment override for database path as it may be user-specific.
    env_db_path = os.getenv("RW_SYNC_DB_PATH")
    if env_db_path:
        db_path = Path(env_db_path).expanduser().resolve()

    title_timeout = float(_coerce_value(net, "title_fetch_timeout", 3.0))
    concurrency = int(_coerce_value(net, "concurrency", 6))
    rpm_save = int(_coerce_value(net, "rpm_save", 50))
    rpm_update = int(_coerce_value(net, "rpm_update", 50))

    should_clean_html = bool(_coerce_value(rw, "should_clean_html", True))
    default_category = str(_coerce_value(rw, "default_category", "article"))

    keep_params = frozenset(_tuple_from(_coerce_list(norm, "keep_params"), _DEFAULT_KEEP_PARAMS))
    drop_params = frozenset(_tuple_from(_coerce_list(norm, "drop_params"), _DEFAULT_DROP_PARAMS))

    token = os.getenv("READWISE_TOKEN", "").strip()

    settings = Settings(
        root=root,
        watch_dir=watch_dir,
        patterns=patterns,
        db_path=db_path,
        title_timeout=title_timeout,
        concurrency=concurrency,
        rpm_save=rpm_save,
        rpm_update=rpm_update,
        should_clean_html=should_clean_html,
        default_category=default_category,
        keep_params=keep_params,
        drop_params=drop_params,
        token=token,
        config_path=cfg_path,
    )
    return settings


def _coerce_path(section: object, key: str, default: str) -> Path:
    if isinstance(section, dict) and key in section and section[key]:
        return Path(str(section[key]))
    return Path(default)


def _coerce_list(section: object, key: str) -> list[str] | None:
    if isinstance(section, dict) and key in section and section[key]:
        raw = section[key]
        if isinstance(raw, list):
            return [str(item) for item in raw if item]
        return [str(raw)]
    return None


def _coerce_value(section: object, key: str, default: object) -> object:
    if isinstance(section, dict) and key in section:
        value = section[key]
        if value is None:
            return default
        return value
    return default


__all__ = ["Settings", "load_settings"]
