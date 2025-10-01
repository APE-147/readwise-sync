from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx
from aiolimiter import AsyncLimiter

logger = logging.getLogger(__name__)

SAVE_URL = "https://readwise.io/api/v3/save/"
UPDATE_URL_TEMPLATE = "https://readwise.io/api/v3/update/{id}/"
AUTH_URL = "https://readwise.io/api/v2/auth/"


class ReadwiseError(Exception):
    pass


class ReaderClient:
    """Async Readwise Reader API client with per-endpoint rate limiting."""

    def __init__(
        self,
        token: str,
        *,
        rpm_save: int,
        rpm_update: int,
        concurrency: int,
    ) -> None:
        if not token:
            raise ReadwiseError("READWISE_TOKEN is required")
        headers = {"Authorization": f"Token {token}"}
        limits = httpx.Limits(max_connections=max(5, concurrency * 2), max_keepalive_connections=max(5, concurrency))
        timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
        self._client = httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits, follow_redirects=True)
        self._save_limiter = AsyncLimiter(max(1, rpm_save), time_period=60)
        self._update_limiter = AsyncLimiter(max(1, rpm_update), time_period=60)

    async def close(self) -> None:
        await self._client.aclose()

    async def auth_check(self) -> bool:
        response = await self._client.get(AUTH_URL)
        logger.debug("Auth check status %s", response.status_code)
        return response.status_code == 204

    async def save(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None, float]:
        async with self._save_limiter:
            start = time.perf_counter()
            response = await self._client.post(SAVE_URL, json=payload)
            if response.status_code == 429:
                await self._respect_retry_after(response)
            duration = time.perf_counter() - start
            content = None
            if response.content:
                with contextlib.suppress(Exception):
                    content = response.json()
            return response.status_code, content, duration

    async def update(self, reader_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        async with self._update_limiter:
            response = await self._client.patch(UPDATE_URL_TEMPLATE.format(id=reader_id), json=payload)
            if response.status_code == 429:
                await self._respect_retry_after(response)
            data = None
            if response.content:
                with contextlib.suppress(Exception):
                    data = response.json()
            return response.status_code, data

    async def _respect_retry_after(self, response: httpx.Response) -> None:
        retry_after = response.headers.get("Retry-After")
        delay = 1.0
        if retry_after:
            try:
                delay = max(1.0, float(retry_after))
            except ValueError:
                delay = 1.0
        logger.warning("Rate limited by Readwise, sleeping for %.1fs", delay)
        await asyncio.sleep(delay)


__all__ = ["ReaderClient", "ReadwiseError"]
