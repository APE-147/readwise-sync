from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


async def fetch_remote_title(url: str, *, timeout: float = 3.0, client: httpx.AsyncClient | None = None) -> str | None:
    if not url:
        return None
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, read=timeout, write=timeout, connect=timeout, pool=timeout),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )
    try:
        response = await http_client.get(url, timeout=timeout)
        response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("Title fetch failed for %s: %s", url, exc)
        if owns_client:
            await http_client.aclose()
        return None
    else:
        soup = BeautifulSoup(response.text, "lxml")
        tag = soup.find("title")
        title = tag.get_text(strip=True) if tag else None
        if owns_client:
            await http_client.aclose()
        return title or None


__all__ = ["fetch_remote_title"]
