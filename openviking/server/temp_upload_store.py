# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Temporary upload storage backends for HTTP server uploads."""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from queue import Full, Queue
from threading import Lock, Thread
from typing import Any, Optional

from openviking.server.config import ServerConfig, TempUploadConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.local_input_guard import _read_upload_meta
from openviking.storage.viking_fs import LS_ALL_NODES, get_viking_fs
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.utils.config.open_viking_config import get_openviking_config

_CHUNK_SIZE = 1024 * 1024
_SHARED_UPLOAD_ROOT = "viking://upload"
_SHARED_CLEANUP_QUEUE_MAX_SIZE = 100
# Seconds in one hour; shared uploads are grouped into `YYYYMMDDHH` (UTC) buckets
# so the upload root never holds a single huge flat directory.
_SHARED_BUCKET_SECONDS = 3600
_SHARED_CLEANUP_QUEUE: Queue[tuple["TempUploadStore", RequestContext, str]] = Queue(
    maxsize=_SHARED_CLEANUP_QUEUE_MAX_SIZE
)
_SHARED_CLEANUP_WORKER_STARTED = False
_SHARED_CLEANUP_WORKER_LOCK = Lock()

# Module-level throttle state. TempUploadStore is not a singleton (HTTP router
# and MCP endpoint call TempUploadStore.build(...) per request), so throttle
# state must live at module scope rather than on the instance.
#
# _SHARED_CLEANUP_DUE_AT maps account_id -> Unix epoch seconds: the expiry time
# of the earliest still-valid upload observed by the last cleanup. Before that
# instant there is nothing new to expire, so submitting cleanup is pointless.
# _SHARED_CLEANUP_PENDING holds account_ids already queued or executing, so we
# never pile duplicate jobs for one account onto the single-worker queue.
_SHARED_CLEANUP_DUE_AT: dict[str, float] = {}
_SHARED_CLEANUP_PENDING: set[str] = set()
_SHARED_CLEANUP_STATE_LOCK = Lock()

logger = logging.getLogger(__name__)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)


def _write_bytes(path: str, content: bytes) -> None:
    with open(path, "wb") as f:
        f.write(content)


def _open_binary_for_write(path: str | Path):
    return open(path, "wb")


def _close_file(file_obj: Any) -> None:
    file_obj.close()


def _create_temp_file(*, prefix: str, suffix: str) -> str:
    fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return temp_path


@dataclass
class ResolvedTempUpload:
    mode: str
    temp_file_id: str
    original_filename: Optional[str]
    local_path: str

    async def cleanup(self) -> None:
        if self.mode == "shared" and self.local_path:
            with suppress(FileNotFoundError):
                await asyncio.to_thread(os.unlink, self.local_path)


def get_temp_upload_config(server_config: ServerConfig) -> TempUploadConfig:
    return server_config.temp_upload


def _shared_bucket_start(bucket: str) -> Optional[float]:
    """Return the epoch-seconds start of a `YYYYMMDDHH` (UTC) bucket, else None."""
    if len(bucket) != 10 or not bucket.isdigit():
        return None
    try:
        parsed = time.strptime(bucket, "%Y%m%d%H")
    except ValueError:
        return None
    return calendar.timegm(parsed)


def _shared_content_uri(bucket: str, leaf: str) -> str:
    return f"{_SHARED_UPLOAD_ROOT}/{bucket}/{leaf}/content"


def _shared_meta_uri(bucket: str, leaf: str) -> str:
    return f"{_SHARED_UPLOAD_ROOT}/{bucket}/{leaf}/meta"


def _legacy_shared_meta_uri(upload_id: str) -> str:
    """Flat pre-bucket meta URI, kept for read compatibility during TTL."""
    return f"{_SHARED_UPLOAD_ROOT}/{upload_id}/meta"


def _parse_shared_temp_file_id(temp_file_id: str) -> Optional[str]:
    if not temp_file_id.startswith("shared_"):
        return None
    upload_id = temp_file_id[len("shared_") :].strip()
    if not upload_id or "/" in upload_id or "\\" in upload_id:
        return None
    return upload_id


def _new_shared_upload_id() -> str:
    """Return a new bucketed upload id: ``<YYYYMMDDHH-UTC>-<uuid hex>``.

    The 10-digit hour prefix is both the storage bucket and a marker of the new
    bucketed format. Legacy ids start with a 13-digit millisecond timestamp
    instead, so the two are distinguishable by the first segment's length.
    """
    return f"{time.strftime('%Y%m%d%H', time.gmtime())}-{uuid.uuid4().hex}"


def _shared_bucket_from_upload_id(upload_id: str) -> Optional[str]:
    """Return the bucket for a NEW-format upload id, else None.

    New ids are ``<YYYYMMDDHH>-<32-hex>``; the bucket is the literal 10-digit
    hour prefix. Legacy ids (``<13-digit-ms>-<uuid>``) and anything malformed
    return None.
    """
    split = _split_shared_upload_id(upload_id)
    return split[0] if split is not None else None


def _split_shared_upload_id(upload_id: str) -> Optional[tuple[str, str]]:
    """Split a NEW-format upload id into ``(bucket, leaf)``, else None.

    ``bucket`` is the 10-digit hour prefix (the storage bucket); ``leaf`` is the
    uuid hex used as the in-bucket directory name, so the bucket prefix is not
    repeated on the child directory. Legacy/malformed ids return None.
    """
    prefix, sep, rest = upload_id.partition("-")
    if not sep or len(prefix) != 10 or not prefix.isdigit():
        return None
    if len(rest) != 32 or any(char not in "0123456789abcdef" for char in rest):
        return None
    if _shared_bucket_start(prefix) is None:
        return None
    return prefix, rest


def _shared_upload_created_at(upload_id: str) -> Optional[float]:
    """Return the creation epoch for a LEGACY flat upload id, else None.

    Legacy ids are ``<13-digit-ms-timestamp>-<32-hex>``; new bucketed ids use a
    10-digit hour prefix and return None here (their expiry is bucket-level).
    """
    timestamp_ms, separator, nonce = upload_id.partition("-")
    if (
        not separator
        or len(timestamp_ms) != 13
        or not timestamp_ms.isdigit()
        or len(nonce) != 32
        or any(char not in "0123456789abcdef" for char in nonce)
    ):
        return None
    return int(timestamp_ms) / 1_000


async def _stream_upload_to_local_temp(upload_file: Any, max_size_bytes: int) -> tuple[str, int]:
    suffix = Path(upload_file.filename or "upload.tmp").suffix or ".tmp"
    temp_path = await asyncio.to_thread(
        _create_temp_file, prefix="ov_http_upload_", suffix=suffix
    )
    total = 0
    f = None
    try:
        f = await asyncio.to_thread(_open_binary_for_write, temp_path)
        while True:
            chunk = await upload_file.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size_bytes:
                raise InvalidArgumentError(
                    f"Upload exceeds size limit ({max_size_bytes} bytes)."
                )
            # UploadFile reads already yield to the event loop.  Local disk writes do
            # not, so run each bounded write in the default executor rather than
            # stalling the Core worker event loop on slow local storage.
            await asyncio.to_thread(f.write, chunk)
        return temp_path, total
    except Exception:
        with suppress(FileNotFoundError):
            await asyncio.to_thread(os.unlink, temp_path)
        raise
    finally:
        if f is not None:
            await asyncio.to_thread(_close_file, f)


class TempUploadStore:
    def __init__(self, server_config: ServerConfig):
        self.server_config = server_config
        self.temp_cfg = get_temp_upload_config(server_config)

    @staticmethod
    def build(server_config: ServerConfig) -> "TempUploadStore":
        return TempUploadStore(server_config)

    @staticmethod
    def _internal_ctx(ctx: RequestContext) -> RequestContext:
        return RequestContext(
            user=ctx.user,
            role=Role.ROOT,
        )

    async def save_upload(
        self,
        upload_file: Any,
        upload_mode: str,
        ctx: RequestContext,
    ) -> str:
        if upload_mode == "local":
            return await self._save_local(upload_file)
        if upload_mode == "shared":
            return await self._save_shared(upload_file, ctx)
        raise InvalidArgumentError("upload_mode must be 'local' or 'shared'.")

    async def resolve_for_consume(
        self,
        temp_file_id: str,
        ctx: RequestContext,
    ) -> ResolvedTempUpload:
        shared_id = _parse_shared_temp_file_id(temp_file_id)
        if shared_id is None:
            return await asyncio.to_thread(self._resolve_local, temp_file_id)
        return await self._resolve_shared(temp_file_id, shared_id, ctx)

    async def _save_local(self, upload_file: Any) -> str:
        config = get_openviking_config()
        temp_dir = config.storage.get_upload_temp_dir()
        await asyncio.to_thread(self._cleanup_local_temp_files, temp_dir)

        file_ext = Path(upload_file.filename).suffix if upload_file.filename else ".tmp"
        temp_filename = f"upload_{uuid.uuid4().hex}{file_ext}"
        temp_file_path = temp_dir / temp_filename

        total = 0
        f = await asyncio.to_thread(_open_binary_for_write, temp_file_path)
        try:
            while True:
                chunk = await upload_file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.temp_cfg.shared_max_size_bytes:
                    with suppress(FileNotFoundError):
                        await asyncio.to_thread(temp_file_path.unlink)
                    raise InvalidArgumentError(
                        f"Upload exceeds size limit ({self.temp_cfg.shared_max_size_bytes} bytes)."
                    )
                await asyncio.to_thread(f.write, chunk)
        finally:
            await asyncio.to_thread(_close_file, f)

        if upload_file.filename:
            meta_path = temp_dir / f"{temp_filename}.ov_upload.meta"
            meta = {
                "original_filename": upload_file.filename,
                "upload_time": time.time(),
            }
            await asyncio.to_thread(_write_json, meta_path, meta)

        return temp_filename

    async def _save_shared(self, upload_file: Any, ctx: RequestContext) -> str:
        temp_path, total_size = await _stream_upload_to_local_temp(
            upload_file, self.temp_cfg.shared_max_size_bytes
        )
        upload_id = _new_shared_upload_id()
        temp_file_id = f"shared_{upload_id}"
        bucket, leaf = _split_shared_upload_id(upload_id)
        vfs = get_viking_fs()
        internal_ctx = self._internal_ctx(ctx)
        content_uri = _shared_content_uri(bucket, leaf)
        meta_uri = _shared_meta_uri(bucket, leaf)
        meta = {
            "version": 2,
            "temp_file_id": temp_file_id,
            "account": ctx.account_id,
            "user": ctx.user.user_id,
            "original_filename": upload_file.filename or "",
            "content_type": getattr(upload_file, "content_type", None),
            "file_ext": Path(upload_file.filename or "").suffix,
            "size": total_size,
            "storage_uri": content_uri,
        }

        try:
            content = await asyncio.to_thread(Path(temp_path).read_bytes)
            await vfs.write_file_bytes(
                content_uri, content, ctx=internal_ctx, auto_pathlock=False
            )
            await vfs.write_file(
                meta_uri,
                json.dumps(meta, ensure_ascii=False),
                ctx=internal_ctx,
                auto_pathlock=False,
            )
            self._schedule_shared_cleanup(ctx)
            return temp_file_id
        except Exception:
            with suppress(Exception):
                await vfs.remove_files(
                    f"{_SHARED_UPLOAD_ROOT}/{bucket}/{leaf}",
                    recursive=True,
                    ctx=internal_ctx,
                    auto_pathlock=False,
                )
            raise
        finally:
            with suppress(FileNotFoundError):
                await asyncio.to_thread(os.unlink, temp_path)

    def _schedule_shared_cleanup(self, ctx: RequestContext) -> None:
        """Enqueue a best-effort shared-upload cleanup off the request path.

        Decisions are made under the state lock before touching the executor:
        skip when TTL is disabled, when a job for this account is already
        pending/running, or when the earliest known expiry (``due_at``) is still
        in the future (nothing new can have expired yet). Otherwise mark the
        account pending and enqueue exactly one job.
        """
        ttl_seconds = self.temp_cfg.ttl_seconds
        if ttl_seconds == 0:
            return

        account_id = ctx.account_id
        now = time.time()
        with _SHARED_CLEANUP_STATE_LOCK:
            if account_id in _SHARED_CLEANUP_PENDING:
                logger.debug(
                    "[TempUpload] Shared cleanup already pending account=%s", account_id
                )
                return
            due_at = _SHARED_CLEANUP_DUE_AT.get(account_id)
            if due_at is not None and now < due_at:
                logger.debug(
                    "[TempUpload] Shared cleanup not due account=%s due_at=%s now=%s",
                    account_id,
                    due_at,
                    now,
                )
                return
            _SHARED_CLEANUP_PENDING.add(account_id)
            try:
                _SHARED_CLEANUP_QUEUE.put_nowait((self, ctx, account_id))
            except Full:
                _SHARED_CLEANUP_PENDING.discard(account_id)
                logger.warning(
                    "[TempUpload] Shared cleanup queue full account=%s queue_max_size=%s",
                    account_id,
                    _SHARED_CLEANUP_QUEUE_MAX_SIZE,
                )
                return

        self._ensure_shared_cleanup_worker()

    @staticmethod
    def _ensure_shared_cleanup_worker() -> None:
        global _SHARED_CLEANUP_WORKER_STARTED
        with _SHARED_CLEANUP_WORKER_LOCK:
            if _SHARED_CLEANUP_WORKER_STARTED:
                return
            Thread(
                target=TempUploadStore._run_shared_cleanup_worker,
                name="ov-shared-upload-cleanup",
                daemon=True,
            ).start()
            _SHARED_CLEANUP_WORKER_STARTED = True

    @staticmethod
    def _run_shared_cleanup_worker() -> None:
        while True:
            store, ctx, account_id = _SHARED_CLEANUP_QUEUE.get()
            started_at = time.monotonic()
            try:
                listed_count, scanned_count, removed_count, next_due_at = asyncio.run(
                    store._cleanup_shared_uploads(ctx)
                )
                logger.info(
                    "[TempUpload] Shared cleanup completed account=%s elapsed_ms=%.1f "
                    "listed=%s scanned=%s removed=%s next_cleanup_at=%s",
                    account_id,
                    (time.monotonic() - started_at) * 1000.0,
                    listed_count,
                    scanned_count,
                    removed_count,
                    next_due_at,
                )
            except Exception:
                logger.warning(
                    "[TempUpload] Shared cleanup failed account=%s",
                    account_id,
                    exc_info=True,
                )
            finally:
                # Always release the pending slot so the next request for this
                # account can retry, regardless of list/parse/remove outcome.
                # due_at is owned by _cleanup_shared_uploads (updated on success,
                # cleared on failure), so the worker only manages pending here.
                with _SHARED_CLEANUP_STATE_LOCK:
                    _SHARED_CLEANUP_PENDING.discard(account_id)
                _SHARED_CLEANUP_QUEUE.task_done()

    def _resolve_local(self, temp_file_id: str) -> ResolvedTempUpload:
        upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
        if not temp_file_id or temp_file_id in {".", ".."}:
            raise PermissionDeniedError(
                "HTTP server only accepts regular files from the upload temp directory."
            )
        raw_name = Path(temp_file_id)
        if raw_name.name != temp_file_id or "/" in temp_file_id or "\\" in temp_file_id:
            raise PermissionDeniedError(
                "HTTP server only accepts temp_file_id values issued from the upload temp directory."
            )

        raw_path = upload_temp_dir / temp_file_id
        if raw_path.is_symlink():
            raise PermissionDeniedError(
                "HTTP server only accepts regular files from the upload temp directory."
            )

        try:
            resolved_path = raw_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise PermissionDeniedError(
                "HTTP server only accepts regular files from the upload temp directory."
            ) from exc

        upload_root = upload_temp_dir.resolve()
        try:
            resolved_path.relative_to(upload_root)
        except ValueError as exc:
            raise PermissionDeniedError(
                "HTTP server only accepts temp_file_id values issued from the upload temp directory."
            ) from exc

        if not resolved_path.is_file():
            raise PermissionDeniedError(
                "HTTP server only accepts regular files from the upload temp directory."
            )

        meta_path = upload_temp_dir / f"{temp_file_id}.ov_upload.meta"
        meta = _read_upload_meta(meta_path)
        original_filename = meta.get("original_filename") if meta else None
        return ResolvedTempUpload(
            mode="local",
            temp_file_id=temp_file_id,
            original_filename=original_filename,
            local_path=str(resolved_path),
        )

    async def _resolve_shared(
        self,
        temp_file_id: str,
        upload_id: str,
        ctx: RequestContext,
    ) -> ResolvedTempUpload:
        meta = await self._read_shared_meta(upload_id, ctx)
        self._validate_shared_meta(meta, temp_file_id, ctx)

        content_uri = meta["storage_uri"]
        vfs = get_viking_fs()
        internal_ctx = self._internal_ctx(ctx)
        if not await vfs.exists(content_uri, ctx=internal_ctx):
            raise PermissionDeniedError("Temporary upload is invalid: content missing.")

        file_ext = meta.get("file_ext") or ".tmp"
        temp_path = await asyncio.to_thread(
            _create_temp_file, prefix="ov_shared_upload_", suffix=file_ext
        )
        try:
            content = await vfs.read_file_bytes(content_uri, ctx=internal_ctx)
            await asyncio.to_thread(_write_bytes, temp_path, content)
        except Exception:
            with suppress(FileNotFoundError):
                await asyncio.to_thread(os.unlink, temp_path)
            raise
        return ResolvedTempUpload(
            mode="shared",
            temp_file_id=temp_file_id,
            original_filename=meta.get("original_filename") or None,
            local_path=temp_path,
        )

    async def _read_shared_meta(self, upload_id: str, ctx: RequestContext) -> dict[str, Any]:
        vfs = get_viking_fs()
        internal_ctx = self._internal_ctx(ctx)
        # The upload id itself tells us the layout: a new id carries a 10-digit
        # hour bucket prefix (stored under bucket/<uuid>/), while a legacy id
        # starts with a 13-digit ms timestamp and lives in the old flat path.
        # So we read exactly one path, no fallback.
        split = _split_shared_upload_id(upload_id)
        if split is not None:
            bucket, leaf = split
            meta_uri = _shared_meta_uri(bucket, leaf)
        else:
            meta_uri = _legacy_shared_meta_uri(upload_id)
        try:
            data = json.loads(await vfs.read_file(meta_uri, ctx=internal_ctx))
        except Exception as exc:
            raise PermissionDeniedError(
                "Temporary upload metadata is invalid or missing."
            ) from exc
        if not isinstance(data, dict):
            raise PermissionDeniedError("Temporary upload metadata is invalid or missing.")
        return data

    def _validate_shared_meta(
        self,
        meta: dict[str, Any],
        temp_file_id: str,
        ctx: RequestContext,
    ) -> None:
        if meta.get("temp_file_id") != temp_file_id:
            raise PermissionDeniedError("Invalid temp_file_id.")
        if meta.get("account") != ctx.account_id:
            raise PermissionDeniedError("Temporary upload does not belong to current account.")

    async def _cleanup_shared_uploads(
        self, ctx: RequestContext
    ) -> tuple[int, int, int, Optional[float]]:
        """Best-effort cleanup of the shared upload root.

        The upload root holds three kinds of top-level directories, and each kind
        is processed as its own independent oldest-first group so that a backlog
        of one kind never blocks another (e.g. a large legacy backlog must not
        starve bucket cleanup):

        - ``YYYYMMDDHH`` hour buckets (the current layout). A bucket expires at
          ``bucket_start + 3600 + ttl`` (once its newest possible upload has
          expired) and is removed whole with a single recursive ``rm``.
        - ``<13-digit-ms>-<uuid>`` legacy flat uploads (pre-bucket). Each expires
          at ``created_at + ttl`` and is removed individually.
        - anything else: malformed/foreign directories. These are left alone
          unless ``temp_upload.cleanup_invalid_dirs`` is enabled, in which case
          they are removed best-effort. Legacy flat uploads are never treated as
          invalid.

        Within each group, entries are visited oldest-first (name-ascending),
        expired ones are removed, and the walk stops at the first still-valid
        entry recording its expiry. The account ``due_at`` throttle is the
        earliest expiry across the bucket and legacy groups; a removal failure in
        either group clears ``due_at`` so the next request retries promptly.
        Every deletion logs its target and elapsed time.

        Returns ``(listed, scanned, removed, next_due_at)``.
        """
        account_id = ctx.account_id
        if self.temp_cfg.ttl_seconds == 0:
            self._clear_due_at(account_id)
            return 0, 0, 0, None
        vfs = get_viking_fs()
        internal_ctx = self._internal_ctx(ctx)
        try:
            entries = await vfs.ls(
                _SHARED_UPLOAD_ROOT,
                show_all_hidden=True,
                node_limit=LS_ALL_NODES,
                sort_by="name",
                sort_order="asc",
                ctx=internal_ctx,
            )
        except Exception:
            # Listing failed: drop due_at so the next request retries instead of
            # being throttled behind a stale expiry, then re-raise for logging.
            self._clear_due_at(account_id)
            logger.warning(
                "Shared temp upload cleanup list failed account=%s",
                account_id,
                exc_info=True,
            )
            raise

        listed_count = len(entries)
        now = time.time()
        ttl_seconds = self.temp_cfg.ttl_seconds
        cleanup_invalid = getattr(self.temp_cfg, "cleanup_invalid_dirs", False)
        logger.debug(
            "Shared temp upload cleanup account=%s ttl_seconds=%s listed_count=%s now=%s",
            account_id,
            ttl_seconds,
            listed_count,
            now,
        )

        # Classify the single listing into independent groups (order preserved,
        # so each group stays name-ascending / oldest-first).
        buckets: list[tuple[str, float]] = []   # (uri, bucket_expiry)
        legacy: list[tuple[str, float]] = []    # (uri, upload_expiry)
        invalid: list[str] = []                 # uri
        for entry in entries:
            if not entry.get("isDir"):
                continue
            uri = str(entry.get("uri") or "").rstrip("/")
            name = uri.removeprefix(f"{_SHARED_UPLOAD_ROOT}/")
            if not uri.startswith(f"{_SHARED_UPLOAD_ROOT}/") or "/" in name:
                continue
            bucket_start = _shared_bucket_start(name)
            if bucket_start is not None:
                buckets.append((uri, bucket_start + _SHARED_BUCKET_SECONDS + ttl_seconds))
                continue
            created_at = _shared_upload_created_at(name)
            if created_at is not None:
                legacy.append((uri, created_at + ttl_seconds))
                continue
            invalid.append(uri)

        # Process the bucket and legacy groups independently, oldest-first.
        bucket_scanned, bucket_removed, bucket_due, bucket_failed = (
            await self._cleanup_expiry_group(vfs, internal_ctx, buckets, now, kind="bucket")
        )
        legacy_scanned, legacy_removed, legacy_due, legacy_failed = (
            await self._cleanup_expiry_group(vfs, internal_ctx, legacy, now, kind="flat")
        )

        # Invalid/foreign dirs: only when explicitly enabled; never blocks the
        # expiry groups and failures are logged inside the helper.
        invalid_removed = 0
        if cleanup_invalid:
            for uri in invalid:
                if await self._remove_shared_dir(vfs, uri, internal_ctx, kind="invalid"):
                    invalid_removed += 1
        elif invalid:
            logger.debug(
                "Shared temp upload cleanup skipped %s malformed dirs account=%s",
                len(invalid),
                account_id,
            )

        scanned_count = bucket_scanned + legacy_scanned
        removed_count = bucket_removed + legacy_removed + invalid_removed

        # due_at is the earliest live expiry across both groups. A removal
        # failure in either group means there is still expired data we could not
        # delete, so clear due_at to let the next request retry promptly.
        dues = [d for d in (bucket_due, legacy_due) if d is not None]
        if bucket_failed or legacy_failed:
            self._clear_due_at(account_id)
            next_due_at: Optional[float] = None
        elif dues:
            next_due_at = min(dues)
            self._set_due_at(account_id, next_due_at)
        else:
            self._clear_due_at(account_id)
            next_due_at = None

        return listed_count, scanned_count, removed_count, next_due_at

    async def _cleanup_expiry_group(
        self,
        vfs: Any,
        internal_ctx: RequestContext,
        items: list[tuple[str, float]],
        now: float,
        *,
        kind: str,
    ) -> tuple[int, int, Optional[float], bool]:
        """Delete expired entries of one oldest-first group.

        ``items`` is ``(uri, expiry)`` in name-ascending (oldest-first) order.
        Deletes leading expired entries and stops at the first still-valid one,
        returning ``(scanned, removed, live_expiry, failed)`` where
        ``live_expiry`` is the expiry of the first still-valid entry (the group's
        contribution to ``due_at``) or None, and ``failed`` is True if a deletion
        failed (caller should not throttle so the next run retries).
        """
        scanned = 0
        removed = 0
        for uri, expiry in items:
            scanned += 1
            if expiry > now:
                logger.debug(
                    "Shared temp upload cleanup reached live %s uri=%s expires_at=%s",
                    kind,
                    uri,
                    expiry,
                )
                return scanned, removed, expiry, False
            if not await self._remove_shared_dir(vfs, uri, internal_ctx, kind=kind):
                return scanned, removed, None, True
            removed += 1
        return scanned, removed, None, False

    @staticmethod
    async def _remove_shared_dir(
        vfs: Any,
        uri: str,
        internal_ctx: RequestContext,
        *,
        kind: str,
    ) -> bool:
        """Recursively delete one shared-upload directory, logging elapsed time.

        Returns True on success, False on failure (logged as a warning). Uses
        ``auto_pathlock=False`` since shared uploads are never concurrently
        mutated and cleanup is best-effort. Deletes AGFS storage only via
        ``remove_files`` because raw temporary uploads never have vector-index
        records, so skipping vector-store cleanup avoids pointless round-trips.
        """
        started_at = time.monotonic()
        try:
            await vfs.remove_files(
                uri, recursive=True, ctx=internal_ctx, auto_pathlock=False
            )
        except Exception:
            logger.warning(
                "[TempUpload] cleanup remove failed kind=%s uri=%s elapsed_ms=%.1f",
                kind,
                uri,
                (time.monotonic() - started_at) * 1000.0,
                exc_info=True,
            )
            return False
        logger.info(
            "[TempUpload] cleanup removed kind=%s uri=%s elapsed_ms=%.1f",
            kind,
            uri,
            (time.monotonic() - started_at) * 1000.0,
        )
        return True

    @staticmethod
    def _set_due_at(account_id: str, due_at: float) -> None:
        with _SHARED_CLEANUP_STATE_LOCK:
            _SHARED_CLEANUP_DUE_AT[account_id] = due_at

    @staticmethod
    def _clear_due_at(account_id: str) -> None:
        with _SHARED_CLEANUP_STATE_LOCK:
            _SHARED_CLEANUP_DUE_AT.pop(account_id, None)

    def _cleanup_local_temp_files(self, temp_dir: Path) -> None:
        if self.temp_cfg.ttl_seconds == 0:
            return
        if not temp_dir.exists():
            return
        now = time.time()
        for file_path in temp_dir.iterdir():
            if not file_path.is_file():
                continue
            file_age = now - file_path.stat().st_mtime
            if file_age > self.temp_cfg.ttl_seconds:
                file_path.unlink(missing_ok=True)
                if not file_path.name.endswith(".ov_upload.meta"):
                    meta_path = temp_dir / f"{file_path.name}.ov_upload.meta"
                    if meta_path.exists():
                        meta_path.unlink(missing_ok=True)
