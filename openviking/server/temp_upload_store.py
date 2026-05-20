# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Temporary upload storage backends for HTTP server uploads."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from openviking.server.config import ServerConfig, TempUploadConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.local_input_guard import _read_upload_meta
from openviking.storage.transaction import get_lock_manager
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.utils.config.open_viking_config import get_openviking_config

_CHUNK_SIZE = 1024 * 1024


@dataclass
class ResolvedTempUpload:
    mode: str
    temp_file_id: str
    original_filename: Optional[str]
    local_path: str
    lock_handle: Any = None

    async def cleanup(self) -> None:
        if self.lock_handle is not None:
            with suppress(Exception):
                await get_lock_manager().release(self.lock_handle)
            self.lock_handle = None

        if self.mode == "shared" and self.local_path:
            with suppress(FileNotFoundError):
                os.unlink(self.local_path)


def get_temp_upload_config(server_config: ServerConfig) -> TempUploadConfig:
    return server_config.temp_upload


def _shared_prefix(cfg: TempUploadConfig) -> str:
    return cfg.shared_prefix.rstrip("/")


def _shared_upload_uri(cfg: TempUploadConfig, ctx: RequestContext, upload_id: str) -> str:
    return f"{_shared_prefix(cfg)}/{upload_id}"


def _shared_content_uri(cfg: TempUploadConfig, ctx: RequestContext, upload_id: str) -> str:
    return f"{_shared_upload_uri(cfg, ctx, upload_id)}/content"


def _shared_meta_uri(cfg: TempUploadConfig, ctx: RequestContext, upload_id: str) -> str:
    return f"{_shared_upload_uri(cfg, ctx, upload_id)}/meta.json"


def _parse_shared_temp_file_id(temp_file_id: str) -> Optional[str]:
    if not temp_file_id.startswith("shared_"):
        return None
    upload_id = temp_file_id[len("shared_") :].strip()
    if not upload_id or "/" in upload_id or "\\" in upload_id:
        return None
    return upload_id


async def _stream_upload_to_local_temp(upload_file: Any, max_size_bytes: int) -> tuple[str, int]:
    suffix = Path(upload_file.filename or "upload.tmp").suffix or ".tmp"
    fd, temp_path = tempfile.mkstemp(prefix="ov_http_upload_", suffix=suffix)
    os.close(fd)
    total = 0
    try:
        with open(temp_path, "wb") as f:
            while True:
                chunk = await upload_file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size_bytes:
                    raise InvalidArgumentError(
                        f"Upload exceeds size limit ({max_size_bytes} bytes)."
                    )
                f.write(chunk)
        return temp_path, total
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise


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
            namespace_policy=ctx.namespace_policy,
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
            return self._resolve_local(temp_file_id)
        return await self._resolve_shared(temp_file_id, shared_id, ctx)

    async def mark_failed(self, resolved: ResolvedTempUpload, ctx: RequestContext) -> None:
        shared_id = _parse_shared_temp_file_id(resolved.temp_file_id)
        if shared_id is None or resolved.lock_handle is None:
            return
        meta = await self._read_shared_meta(shared_id, ctx)
        meta["state"] = "uploaded"
        meta["updated_at"] = int(time.time())
        await self._write_shared_meta(shared_id, ctx, meta)

    async def mark_consumed(self, resolved: ResolvedTempUpload, ctx: RequestContext) -> None:
        shared_id = _parse_shared_temp_file_id(resolved.temp_file_id)
        if shared_id is None:
            return
        uri = _shared_upload_uri(self.temp_cfg, ctx, shared_id)
        await get_viking_fs().rm(
            uri,
            recursive=True,
            ctx=self._internal_ctx(ctx),
            lock_handle=resolved.lock_handle,
        )

    async def try_cleanup_invalid_or_expired(
        self,
        temp_file_id: str,
        ctx: RequestContext,
    ) -> None:
        shared_id = _parse_shared_temp_file_id(temp_file_id)
        if shared_id is None:
            return
        uri = _shared_upload_uri(self.temp_cfg, ctx, shared_id)
        with suppress(Exception):
            await get_viking_fs().rm(uri, recursive=True, ctx=self._internal_ctx(ctx))

    async def _save_local(self, upload_file: Any) -> str:
        config = get_openviking_config()
        temp_dir = config.storage.get_upload_temp_dir()
        self._cleanup_local_temp_files(temp_dir)

        file_ext = Path(upload_file.filename).suffix if upload_file.filename else ".tmp"
        temp_filename = f"upload_{uuid.uuid4().hex}{file_ext}"
        temp_file_path = temp_dir / temp_filename

        total = 0
        with open(temp_file_path, "wb") as f:
            while True:
                chunk = await upload_file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.temp_cfg.shared_max_size_bytes:
                    f.close()
                    with suppress(FileNotFoundError):
                        temp_file_path.unlink()
                    raise InvalidArgumentError(
                        f"Upload exceeds size limit ({self.temp_cfg.shared_max_size_bytes} bytes)."
                    )
                f.write(chunk)

        if upload_file.filename:
            meta_path = temp_dir / f"{temp_filename}.ov_upload.meta"
            meta = {
                "original_filename": upload_file.filename,
                "upload_time": time.time(),
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f)

        return temp_filename

    async def _save_shared(self, upload_file: Any, ctx: RequestContext) -> str:
        temp_path, total_size = await _stream_upload_to_local_temp(
            upload_file, self.temp_cfg.shared_max_size_bytes
        )
        upload_id = uuid.uuid4().hex
        temp_file_id = f"shared_{upload_id}"
        vfs = get_viking_fs()
        internal_ctx = self._internal_ctx(ctx)
        content_uri = _shared_content_uri(self.temp_cfg, ctx, upload_id)
        meta_uri = _shared_meta_uri(self.temp_cfg, ctx, upload_id)
        now = int(time.time())
        meta = {
            "version": 1,
            "upload_mode": "shared",
            "upload_id": upload_id,
            "temp_file_id": temp_file_id,
            "account": ctx.account_id,
            "user": ctx.user.user_id,
            "agent": ctx.user.agent_id,
            "original_filename": upload_file.filename or "",
            "content_type": getattr(upload_file, "content_type", None),
            "file_ext": Path(upload_file.filename or "").suffix,
            "size": total_size,
            "sha256": None,
            "storage_uri": content_uri,
            "state": "uploaded",
            "created_at": now,
            "updated_at": now,
        }

        try:
            with open(temp_path, "rb") as f:
                content = f.read()
            await vfs.write_file_bytes(content_uri, content, ctx=internal_ctx)
            await vfs.write_file(meta_uri, json.dumps(meta, ensure_ascii=False), ctx=internal_ctx)
            return temp_file_id
        except Exception:
            with suppress(Exception):
                await self.try_cleanup_invalid_or_expired(temp_file_id, ctx)
            raise
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temp_path)

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
            await self.try_cleanup_invalid_or_expired(temp_file_id, ctx)
            raise PermissionDeniedError("Temporary upload is invalid: content missing.")

        lock_path = vfs._uri_to_path(
            _shared_upload_uri(self.temp_cfg, ctx, upload_id),
            ctx=internal_ctx,
        )
        handle = get_lock_manager().create_handle()
        acquired = await get_lock_manager().acquire_tree(handle, lock_path, timeout=0.0)
        if not acquired:
            raise PermissionDeniedError("Temporary upload is being consumed.")

        try:
            meta = await self._read_shared_meta(upload_id, ctx)
            now = int(time.time())
            if meta.get("state") != "uploaded":
                raise PermissionDeniedError("Temporary upload is being consumed.")

            meta["state"] = "consuming"
            meta["updated_at"] = now
            await self._write_shared_meta(upload_id, ctx, meta)

            file_ext = meta.get("file_ext") or ".tmp"
            fd, temp_path = tempfile.mkstemp(prefix="ov_shared_upload_", suffix=file_ext)
            os.close(fd)
            try:
                content = await vfs.read_file_bytes(content_uri, ctx=internal_ctx)
                with open(temp_path, "wb") as f:
                    f.write(content)
            except Exception:
                with suppress(FileNotFoundError):
                    os.unlink(temp_path)
                raise

            return ResolvedTempUpload(
                mode="shared",
                temp_file_id=temp_file_id,
                original_filename=meta.get("original_filename") or None,
                local_path=temp_path,
                lock_handle=handle,
            )
        except Exception:
            with suppress(Exception):
                await get_lock_manager().release(handle)
            raise

    async def _read_shared_meta(self, upload_id: str, ctx: RequestContext) -> dict[str, Any]:
        meta_uri = _shared_meta_uri(self.temp_cfg, ctx, upload_id)
        try:
            raw = await get_viking_fs().read_file(meta_uri, ctx=self._internal_ctx(ctx))
            data = json.loads(raw)
        except Exception as exc:
            raise PermissionDeniedError("Temporary upload metadata is invalid or missing.") from exc
        if not isinstance(data, dict):
            raise PermissionDeniedError("Temporary upload metadata is invalid or missing.")
        return data

    async def _write_shared_meta(
        self,
        upload_id: str,
        ctx: RequestContext,
        meta: dict[str, Any],
    ) -> None:
        meta_uri = _shared_meta_uri(self.temp_cfg, ctx, upload_id)
        await get_viking_fs().write_file(
            meta_uri,
            json.dumps(meta, ensure_ascii=False),
            ctx=self._internal_ctx(ctx),
        )

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

    @staticmethod
    def _cleanup_local_temp_files(temp_dir: Path, max_age_hours: int = 1) -> None:
        if not temp_dir.exists():
            return
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        for file_path in temp_dir.iterdir():
            if not file_path.is_file():
                continue
            file_age = now - file_path.stat().st_mtime
            if file_age > max_age_seconds:
                file_path.unlink(missing_ok=True)
                if not file_path.name.endswith(".ov_upload.meta"):
                    meta_path = temp_dir / f"{file_path.name}.ov_upload.meta"
                    if meta_path.exists():
                        meta_path.unlink(missing_ok=True)
