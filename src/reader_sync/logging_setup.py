from __future__ import annotations

import logging
from logging import Handler


def setup_logging(level: str = "INFO") -> None:
    """Configure application logging once."""
    logging.captureWarnings(True)

    root = logging.getLogger()
    if root.handlers:
        for handler in list(root.handlers):
            root.removeHandler(handler)
    handler: Handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level.upper())


__all__ = ["setup_logging"]
