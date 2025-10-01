from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl, unquote

from bs4 import BeautifulSoup

from .models import URLSource

_CANONICAL_RE = re.compile(r"<link[^>]+rel=['\"]canonical['\"][^>]*href=['\"]([^'\"]+)['\"]", re.I)
_SINGLEFILE_RE = re.compile(r"<!--\s*saved from url=\(?\s*([^)>\s]+)\s*\)?\s*-->", re.I)

# Filename URL segment patterns
_EXT_RE = re.compile(r"\.html?$", re.I)
_TS_SUFFIX_RE = re.compile(r"_(\d{8})_(\d{6})$")
_URL_MARK_RE = re.compile(r"\[URL\]", re.I)


def choose_url_from_html(html: str) -> tuple[str, URLSource]:
    """Extract best-effort URL and its origin from HTML."""
    canonical = _CANONICAL_RE.search(html)
    if canonical:
        return canonical.group(1).strip(), "canonical"

    singlefile = _SINGLEFILE_RE.search(html)
    if singlefile:
        return singlefile.group(1).strip(), "singlefile"

    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.has_attr("content"):
        return og["content"].strip(), "og:url"

    return "", "synthetic"


def tidy_extracted_url(url: str) -> str | None:
    url = url.strip()
    if not url:
        return None

    lowered = url.lower()
    if lowered.startswith(("http://", "https://")):
        candidate = url
    elif lowered.startswith("//"):
        candidate = "https:" + url
    elif lowered.startswith("/www."):
        candidate = "https://" + url.lstrip("/")
    elif lowered.startswith("www."):
        candidate = "https://" + url.lstrip("/")
    else:
        temp = "https://" + url.lstrip("/")
        parsed_temp = urlsplit(temp)
        if not parsed_temp.netloc or "." not in parsed_temp.netloc:
            return None
        candidate = urlunsplit(("https", parsed_temp.netloc, parsed_temp.path or "/", parsed_temp.query, parsed_temp.fragment))

    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.netloc or "." not in parsed.netloc:
        return None
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))


def infer_from_filename(name: str) -> tuple[str, URLSource] | None:
    """Infer source URL from filename per naming conventions.

    Supports both sanitized and non-sanitized variants:
      1) (Title)_[URL]_ENCODED_URL.html
      2) Cleaned_Title_YYYYMMDD_HHMMSS_[URL]_ENCODED_URL.html
      3) Cleaned Title YYYYMMDD_HHMMSS [URL] ENCODED_URL.html

    Also tolerates truncated encoded URLs ending with the ellipsis '…'.
    Returns normalized best-effort URL string with source "inferred".
    """
    # Strip extension
    base = _EXT_RE.sub("", name)
    # Drop timestamp suffix if present (e.g., _20251001_123456)
    base = _TS_SUFFIX_RE.sub("", base)

    # Locate the [URL] marker
    m = _URL_MARK_RE.search(base)
    if not m:
        # Legacy heuristic: Twitter short form remains for backward compatibility
        tw = re.match(r"(?:x|twitter)\.com_([A-Za-z0-9_]+)_status_(\d+)", base)
        if tw:
            username, status_id = tw.groups()
            return f"https://x.com/{username}/status/{status_id}", "inferred"
        return None

    # Capture everything after the marker as the encoded URL candidate
    enc = base[m.end():]
    # Trim separators between marker and encoded URL
    enc = enc.lstrip(" _-")

    # If title or other segments used underscores, that's OK — the encoded url is the entire tail.
    # Remove any remaining whitespace just in case (older templates)
    enc = enc.strip()

    # If it still contains the timestamp (rare ordering), remove it
    enc = _TS_SUFFIX_RE.sub("", enc)

    # Tolerate trailing ellipsis … indicating truncation
    enc = enc.rstrip("…")

    # Guard against incomplete percent-escapes at the very end
    while enc.endswith("%") or re.search(r"%[0-9A-Fa-f]$", enc):
        enc = enc[:-1]
    enc = enc.strip()
    if not enc:
        return None

    try:
        decoded = unquote(enc)
    except Exception:
        decoded = enc  # fall back to raw if for any reason unquote goes sideways

    url = tidy_extracted_url(decoded) or tidy_extracted_url(enc)
    if not url:
        return None
    return url, "inferred"


def synthetic_url(sha1: str) -> str:
    return f"https://local/doc/{sha1}"


def normalize_url(url: str, keep_params: Iterable[str], drop_prefixes: Iterable[str]) -> str:
    if not url:
        return url
    parts = urlsplit(url)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if any(key.startswith(prefix) for prefix in drop_prefixes):
            continue
        if key in keep_params:
            kept.append((key, value))
    query = urlencode(sorted(kept))
    return urlunsplit((scheme, netloc, path, query, ""))


def extract_local_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    title = soup.find("title")
    if not title:
        return None
    text = title.get_text(strip=True)
    return text or None


__all__ = [
    "choose_url_from_html",
    "tidy_extracted_url",
    "infer_from_filename",
    "synthetic_url",
    "normalize_url",
    "extract_local_title",
]
