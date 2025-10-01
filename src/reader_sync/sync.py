from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Literal

import httpx

from .config import Settings
from .database import Database
from .failures import append_failure, read_failures
from .filesystem import compute_sha1, discover_files, read_html
from .html_utils import (
    choose_url_from_html,
    extract_local_title,
    infer_from_filename,
    normalize_url,
    synthetic_url,
    tidy_extracted_url,
)
from .models import FileMeta, PreparedDocument, SyncResult, SyncStats
from .readwise_client import ReaderClient, ReadwiseError
from .title_fetcher import fetch_remote_title

logger = logging.getLogger(__name__)

_MODE = Literal["all", "new"]


class SyncService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.db_path)
        try:
            self.reader = ReaderClient(
                settings.token,
                rpm_save=settings.rpm_save,
                rpm_update=settings.rpm_update,
                concurrency=settings.concurrency,
            )
        except ReadwiseError as exc:
            logger.error("Failed to initialize Reader client: %s", exc)
            raise
        timeout = self.settings.title_timeout
        self._title_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout, pool=timeout),
        )

    async def close(self) -> None:
        await self.reader.close()
        await self._title_client.aclose()
        self.db.close()

    async def auth_check(self) -> bool:
        return await self.reader.auth_check()

    async def push(
        self,
        mode: _MODE,
        *,
        dry_run: bool = False,
        max_items: int | None = None,
        since: float | None = None,
    ) -> SyncStats:
        stats = SyncStats()
        files = discover_files(self.settings.watch_dir, self.settings.patterns)

        # Incremental windowing for --new: if no --since provided, start from last watermark.
        window_start: float | None = since
        window_end: float | None = None
        if mode == "new" and since is None:
            raw = self.db.get_meta("last_push_new_end_ts")
            try:
                window_start = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                window_start = None
            # Bound upper window at collection time to avoid racing with concurrently-added files
            window_end = time.time()

        def _add_time(meta) -> float:
            return meta.birthtime if meta.birthtime is not None else meta.mtime

        # Apply time-window filtering when requested
        if window_start is not None:
            files = [m for m in files if _add_time(m) > window_start]
        if window_end is not None:
            files = [m for m in files if _add_time(m) <= window_end]

        # Process in ascending add-time order so the watermark advances correctly with --max
        files.sort(key=_add_time)
        total_candidates = len(files)

        if max_items is not None:
            files = files[:max_items]
        if not files:
            logger.info("No files discovered under %s", self.settings.watch_dir)
            return stats

        semaphore = asyncio.Semaphore(self.settings.concurrency)
        tasks = []
        for meta in files:
            tasks.append(self._process_file(meta, semaphore, mode=mode, dry_run=dry_run, stats=stats))
        await asyncio.gather(*tasks)

        # Advance watermark for --new auto-incremental runs (not in dry-run)
        if mode == "new" and since is None and not dry_run:
            if files:
                new_mark = max(_add_time(m) for m in files)
            else:
                # If nothing to process, advance to collection time to avoid re-scanning old files
                new_mark = window_end or time.time()
            self.db.set_meta("last_push_new_end_ts", str(new_mark))
            logger.debug(
                "Updated last_push_new_end_ts=%s (processed=%d/%d)",
                new_mark,
                len(files),
                total_candidates,
            )
        return stats

    async def replay(self, *, date: dt.date, dry_run: bool = False) -> SyncStats:
        stats = SyncStats()
        entries = read_failures(self.settings.root, date=date)
        if not entries:
            logger.info("No failure entries for %s", date.isoformat())
            return stats
        semaphore = asyncio.Semaphore(self.settings.concurrency)
        tasks = []
        for url, filename in entries:
            path = self.settings.watch_dir / filename
            if not path.exists():
                logger.warning("Replay skip: file %s missing", filename)
                continue
            stat = path.stat()
            file_meta = FileMeta(path=path, mtime=stat.st_mtime, size=stat.st_size)
            tasks.append(
                self._process_file(
                    file_meta,
                    semaphore,
                    mode="all",
                    dry_run=dry_run,
                    stats=stats,
                )
            )
        await asyncio.gather(*tasks)
        return stats

    async def _process_file(self, file_meta, semaphore: asyncio.Semaphore, *, mode: _MODE, dry_run: bool, stats: SyncStats) -> None:
        async with semaphore:
            try:
                document = await asyncio.to_thread(self._prepare_document, file_meta)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to prepare document %s: %s", file_meta.path, exc)
                append_failure(self.settings.root, "", file_meta.path.name)
                stats.failed += 1
                return

            existing = self.db.lookup(document.normalized_url)
            action = self._determine_action(existing, mode=mode)

            if action == "skip":
                self.db.upsert(
                    norm_url=document.normalized_url,
                    source_url=document.original_url,
                    title=existing.title if existing else document.local_title,
                    title_source=existing.title_source if existing else "local",
                    file_path=str(document.file.path),
                    file_mtime=document.file.mtime,
                    readwise_id=existing.readwise_id if existing else None,
                    last_status=existing.last_status if existing else None,
                    last_error=existing.last_error if existing else None,
                )
                stats.skipped += 1
                return

            result = await self._execute_action(document, action=action, dry_run=dry_run, existing=existing)
            self._update_stats(stats, result)

    def _determine_action(self, existing, *, mode: _MODE) -> Literal["create", "update", "skip"]:
        if existing is None or not existing.readwise_id:
            return "create"
        if mode == "new":
            return "skip"
        return "update"

    async def _execute_action(self, document: PreparedDocument, *, action: Literal["create", "update"], dry_run: bool, existing) -> SyncResult:
        if dry_run:
            logger.info("[DRY-RUN] %s %s", action.upper(), document.file.path)
            return SyncResult(action=action, status_code=None, readwise_id=existing.readwise_id if existing else None, error=None, document=document)

        target_url = document.original_url if document.original_source != "synthetic" else ""
        remote_title = await fetch_remote_title(
            target_url or document.normalized_url,
            timeout=self.settings.title_timeout,
            client=self._title_client,
        )
        payload_title = remote_title or document.local_title
        payload = {
            "url": document.original_url or document.normalized_url,
            "html": document.html,
            "should_clean_html": self.settings.should_clean_html,
        }
        if self.settings.default_category:
            payload["category"] = self.settings.default_category
        if payload_title:
            payload["title"] = payload_title

        if action == "create":
            try:
                status, data, duration = await self.reader.save(payload)
            except Exception as exc:  # noqa: BLE001
                logger.error("Save failed for %s: %s", document.file.path, exc)
                append_failure(self.settings.root, payload["url"], document.file.path.name)
                self.db.upsert(
                    norm_url=document.normalized_url,
                    source_url=document.original_url,
                    title=document.local_title,
                    title_source="local" if document.local_title else "unknown",
                    file_path=str(document.file.path),
                    file_mtime=document.file.mtime,
                    readwise_id=None,
                    last_status=None,
                    last_error=str(exc),
                )
                return SyncResult(action="create", status_code=None, readwise_id=None, error=str(exc), document=document)
            logger.info("Saved %s status=%s duration=%.2fs", document.file.path, status, duration)
            return self._handle_save_response(status, data, document, remote_title)

        reader_id = existing.readwise_id if existing else None
        if not reader_id:
            logger.warning("Missing readwise_id for %s, falling back to create", document.file.path)
            return await self._execute_action(document, action="create", dry_run=dry_run, existing=None)

        update_payload = {}
        if remote_title:
            update_payload["title"] = remote_title
        elif document.local_title and (existing.title or "") != document.local_title:
            update_payload["title"] = document.local_title
        if not update_payload:
            logger.info("No metadata changes for %s", document.file.path)
            return SyncResult(action="skip", status_code=None, readwise_id=reader_id, error=None, document=document)

        try:
            status, data = await self.reader.update(reader_id, update_payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("Update failed for %s: %s", document.file.path, exc)
            append_failure(self.settings.root, document.original_url, document.file.path.name)
            self.db.update_status(document.normalized_url, status=None, error=str(exc))
            return SyncResult(action="update", status_code=None, readwise_id=reader_id, error=str(exc), document=document)

        logger.info("Updated %s status=%s", document.file.path, status)
        return self._handle_update_response(status, data, document, reader_id, remote_title or document.local_title)

    def _handle_save_response(self, status: int, data, document: PreparedDocument, remote_title: str | None) -> SyncResult:
        if status not in (200, 201):
            error = f"unexpected status {status}"
            append_failure(self.settings.root, document.original_url, document.file.path.name)
            self.db.update_status(document.normalized_url, status=status, error=error)
            return SyncResult(action="create", status_code=status, readwise_id=None, error=error, document=document)

        readwise_id = _extract_id(data)
        reader_title = _extract_title(data)
        title = remote_title or reader_title or document.local_title
        title_source = "online" if remote_title else "reader" if reader_title else "local" if document.local_title else "unknown"
        self.db.upsert(
            norm_url=document.normalized_url,
            source_url=document.original_url,
            title=title,
            title_source=title_source,
            file_path=str(document.file.path),
            file_mtime=document.file.mtime,
            readwise_id=readwise_id,
            last_status=status,
            last_error=None,
        )
        return SyncResult(action="create" if status == 201 else "update", status_code=status, readwise_id=readwise_id, error=None, document=document)

    def _handle_update_response(self, status: int, data, document: PreparedDocument, reader_id: str, title: str | None) -> SyncResult:
        if status not in (200, 201, 204):
            error = f"unexpected status {status}"
            append_failure(self.settings.root, document.original_url, document.file.path.name)
            self.db.update_status(document.normalized_url, status=status, error=error)
            return SyncResult(action="update", status_code=status, readwise_id=reader_id, error=error, document=document)
        reader_title = _extract_title(data)
        final_title = reader_title or title or document.local_title
        title_source = "reader" if reader_title else "online" if title and title == reader_title else "local" if document.local_title else "unknown"
        self.db.upsert(
            norm_url=document.normalized_url,
            source_url=document.original_url,
            title=final_title,
            title_source=title_source,
            file_path=str(document.file.path),
            file_mtime=document.file.mtime,
            readwise_id=reader_id,
            last_status=status,
            last_error=None,
        )
        return SyncResult(action="update", status_code=status, readwise_id=reader_id, error=None, document=document)

    def _update_stats(self, stats: SyncStats, result: SyncResult) -> None:
        if result.error:
            stats.failed += 1
            return
        if result.action == "create":
            stats.created += 1
        elif result.action == "update":
            stats.updated += 1
        else:
            stats.skipped += 1

    def _prepare_document(self, file_meta: FileMeta) -> PreparedDocument:
        html = read_html(file_meta.path)
        sha1 = compute_sha1(html)
        # Prefer explicit URL embedded in filename per agreed convention.
        inferred = infer_from_filename(file_meta.path.name)
        if inferred:
            inferred_url, source = inferred
            primary_url = inferred_url
        else:
            # Fall back to HTML-based signals
            candidate_url, source = choose_url_from_html(html)
            primary_url = tidy_extracted_url(candidate_url)

        if not primary_url:
            primary_url = synthetic_url(sha1)
            source = "synthetic"

        norm_url = normalize_url(primary_url, self.settings.keep_params, self.settings.drop_params)
        local_title = extract_local_title(html)
        return PreparedDocument(
            file=file_meta,
            html=html,
            original_url=primary_url,
            original_source=source,
            normalized_url=norm_url,
            sha1=sha1,
            local_title=local_title,
        )


def _extract_id(data) -> str | None:
    if isinstance(data, dict):
        if "id" in data:
            return str(data["id"])
        if "document_id" in data:
            return str(data["document_id"])
        if "document" in data and isinstance(data["document"], dict) and "id" in data["document"]:
            return str(data["document"]["id"])
    return None


def _extract_title(data) -> str | None:
    if isinstance(data, dict):
        if "title" in data and data["title"]:
            return str(data["title"])
        if "document" in data and isinstance(data["document"], dict):
            title = data["document"].get("title")
            if title:
                return str(title)
    return None


__all__ = ["SyncService"]
