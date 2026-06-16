# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource endpoints for OpenViking HTTP Server."""

import json as _json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.local_input_guard import require_remote_resource_source
from openviking.server.responses import response_from_result
from openviking.server.skill_source_metadata import persist_skill_source_metadata
from openviking.server.telemetry import run_operation
from openviking.server.temp_upload_store import TempUploadStore
from openviking.server.upload_token_store import UploadTokenError, upload_token_store
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier

router = APIRouter(prefix="/api/v1", tags=["resources"])

# Maximum reassembled response size in bytes for GET /resources/full.
# Hard-coded so callers can rely on a stable contract; tune later via PR.
_MAX_REASSEMBLY_BYTES = 5 * 1024 * 1024
_CHUNKS_SIDECAR_NAME = ".chunks.json"
_RESERVED_FILENAMES = {".abstract.md", ".overview.md", _CHUNKS_SIDECAR_NAME}


class AddResourceRequest(BaseModel):
    """Request model for add_resource.

    Attributes:
        path: Remote resource source such as an HTTP(S) URL or repository URL.
            Either path or temp_file_id must be provided.
        temp_file_id: Temporary upload id returned by /api/v1/resources/temp_upload.
            Either path or temp_file_id must be provided.
        to: Target URI for the resource (e.g., "viking://resources/my_resource").
            If not specified, an auto-generated URI will be used.
        parent: Parent URI under which the resource will be stored.
            Cannot be used together with 'to'.
        create_parent: Whether to automatically create the parent directory if it doesn't exist.
            Default is False.
        reason: Reason for adding the resource. Used for documentation and monitoring.
        instruction: Processing instruction for semantic extraction.
            Provides hints for how the resource should be processed.
        wait: Whether to wait for semantic extraction and vectorization to complete.
            Default is False (async processing).
        timeout: Timeout in seconds when wait=True. None means no timeout.
        strict: Whether to use strict mode for processing. Default is True.
        ignore_dirs: Comma-separated list of directory names to ignore during parsing.
        include: Glob pattern for files to include during parsing.
        exclude: Glob pattern for files to exclude during parsing.
        directly_upload_media: Whether to directly upload media files. Default is True.
        preserve_structure: Whether to preserve directory structure when adding directories.
        args: Parser-specific import options. For Feishu one-time user-token imports,
            pass {"feishu_access_token": "..."}. For Feishu user-token watches,
            pass {"feishu_access_token": "...", "feishu_refresh_token": "..."}.
        watch_interval: Watch interval in minutes for automatic resource monitoring.
            - watch_interval > 0: Creates or updates a watch task. The resource will be
              automatically re-processed at the specified interval.
            - watch_interval = 0: No watch task is created. If a watch task exists for
              this resource, it will be cancelled (deactivated).
            - watch_interval < 0: Same as watch_interval = 0, cancels any existing watch task.
            Default is 0 (no monitoring).

            Note: If the target URI already has an active watch task, a ConflictError will be
            raised. You must first cancel the existing watch (set watch_interval <= 0) before
            creating a new one.
    """

    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = None
    temp_file_id: Optional[str] = None
    to: Optional[str] = None
    parent: Optional[str] = None
    create_parent: bool = False
    reason: str = ""
    instruction: str = ""
    wait: bool = False
    timeout: Optional[float] = None
    strict: bool = False
    source_name: Optional[str] = None
    ignore_dirs: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    directly_upload_media: bool = True
    preserve_structure: Optional[bool] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    telemetry: TelemetryRequest = False
    watch_interval: float = 0

    @model_validator(mode="after")
    def check_path_or_temp_file_id(self):
        if not self.path and not self.temp_file_id:
            raise ValueError("Either 'path' or 'temp_file_id' must be provided")
        return self


class AddSkillRequest(BaseModel):
    """Request model for add_skill.

    Attributes:
        data: Inline skill content or structured skill data. HTTP requests do not treat
            string values as host filesystem paths.
        temp_file_id: Temporary upload id returned by /api/v1/resources/temp_upload.
        wait: Whether to wait for skill processing to complete.
        timeout: Timeout in seconds when wait=True.
    """

    model_config = ConfigDict(extra="forbid")

    data: Any = None
    temp_file_id: Optional[str] = None
    wait: bool = False
    timeout: Optional[float] = None
    source_metadata: Optional[Dict[str, Any]] = None
    telemetry: TelemetryRequest = False

    @model_validator(mode="after")
    def check_data_or_temp_file_id(self):
        if self.data is None and not self.temp_file_id:
            raise ValueError("Either 'data' or 'temp_file_id' must be provided")
        return self


@router.post("/resources/temp_upload")
async def temp_upload(
    request: Request,
    file: UploadFile = File(...),
    telemetry: bool = Form(False),
    upload_mode: str = Form("local"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Upload a temporary file for add_resource or import_ovpack."""

    async def _upload() -> dict[str, str]:
        store = TempUploadStore.build(request.app.state.config)
        temp_file_id = await store.save_upload(file, upload_mode, _ctx)
        return {"temp_file_id": temp_file_id}

    execution = await run_operation(
        operation="resources.temp_upload",
        telemetry=telemetry,
        fn=_upload,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)


@router.post("/resources/temp_upload_signed")
async def temp_upload_signed(
    request: Request,
    file: UploadFile = File(...),
    token: str = Query(..., min_length=1),
    upload_mode: str = Query("local"),
):
    """Upload via short-lived signed token. Used by the MCP progressive-upload flow.

    No identity headers required — the token (issued by ``add_resource`` MCP for local-file
    paths) carries the bound (account_id, user_id). The token is consumed on first
    use; subsequent attempts return 401. The server mints the ``temp_file_id`` at write time
    and returns it in the response body; the caller then calls ``add_resource`` with that id.

    Persistence flows through :class:`TempUploadStore`, so the same local/shared upload modes
    and size limit (``temp_upload.shared_max_size_bytes``) as the auth'd ``/temp_upload`` route
    apply here too.
    """
    try:
        account_id, user_id = upload_token_store.consume(token)
    except UploadTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        ctx = RequestContext(
            user=UserIdentifier(account_id, user_id),
            role=Role.USER,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid identity in token: {exc}") from exc

    store = TempUploadStore.build(request.app.state.config)
    try:
        temp_file_id = await store.save_upload(file, upload_mode, ctx)
    except InvalidArgumentError as exc:
        # save_upload raises InvalidArgumentError for both bad mode and oversize.
        # Map oversize specifically to 413; the rest stay 400.
        msg = str(exc)
        status = 413 if "exceeds size limit" in msg else 400
        raise HTTPException(status_code=status, detail=msg) from exc

    return {"temp_file_id": temp_file_id}


@router.post("/resources")
async def add_resource(
    http_request: Request,
    request: AddResourceRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add resource to OpenViking."""
    service = get_service()

    path = request.path
    allow_local_path_resolution = False
    original_filename = None
    resolved = None
    store = None
    if request.temp_file_id:
        store = TempUploadStore.build(http_request.app.state.config)
        resolved = await store.resolve_for_consume(request.temp_file_id, _ctx)
        path = resolved.local_path
        original_filename = resolved.original_filename
        allow_local_path_resolution = True
    elif path is not None:
        path = require_remote_resource_source(path)
    if path is None:
        raise InvalidArgumentError("Either 'path' or 'temp_file_id' must be provided.")

    # Use original_filename from upload if source_name not explicitly provided
    source_name = request.source_name
    if source_name is None and original_filename is not None:
        source_name = original_filename

    kwargs = {
        "strict": request.strict,
        "source_name": source_name,
        "ignore_dirs": request.ignore_dirs,
        "include": request.include,
        "exclude": request.exclude,
        "directly_upload_media": request.directly_upload_media,
        "watch_interval": request.watch_interval,
        "create_parent": request.create_parent,
    }
    if request.temp_file_id:
        kwargs["temp_file_id"] = request.temp_file_id
    if request.preserve_structure is not None:
        kwargs["preserve_structure"] = request.preserve_structure

    async def _add() -> dict[str, Any]:
        try:
            result = await service.resources.add_resource(
                path=path,
                ctx=_ctx,
                to=request.to,
                parent=request.parent,
                reason=request.reason,
                instruction=request.instruction,
                wait=request.wait,
                timeout=request.timeout,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=True,
                args=request.args,
                **kwargs,
            )
        except Exception:
            if resolved and store:
                await store.mark_failed(resolved, _ctx)
            raise
        else:
            if resolved and store:
                await store.mark_consumed(resolved, _ctx)
            return result
        finally:
            if resolved:
                await resolved.cleanup()

    execution = await run_operation(
        operation="resources.add_resource",
        telemetry=request.telemetry,
        fn=_add,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)


@router.post("/skills")
async def add_skill(
    http_request: Request,
    request: AddSkillRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add skill to OpenViking."""
    service = get_service()
    data = request.data
    allow_local_path_resolution = False
    resolved = None
    source_metadata = request.source_metadata or {
        "type": "api",
        "source": "inline_content",
        "operation": "add",
    }
    if request.temp_file_id:
        store = TempUploadStore.build(http_request.app.state.config)
        resolved = await store.resolve_for_consume(request.temp_file_id, _ctx)
        data = resolved.local_path
        allow_local_path_resolution = True
        if request.source_metadata is None:
            source_metadata = {
                "type": "api",
                "source": "temp_upload",
                "operation": "add",
                "upload_mode": resolved.mode,
            }
        if resolved.original_filename and request.source_metadata is None:
            source_metadata["original_filename"] = resolved.original_filename

    source_path_hint = resolved.original_filename if resolved else None
    store = TempUploadStore.build(http_request.app.state.config) if resolved else None

    async def _add() -> dict[str, Any]:
        try:
            result = await service.resources.add_skill(
                data=data,
                ctx=_ctx,
                wait=request.wait,
                timeout=request.timeout,
                allow_local_path_resolution=allow_local_path_resolution,
                source_path_hint=source_path_hint,
            )
            await persist_skill_source_metadata(service, _ctx, result, source_metadata)
        except Exception:
            if resolved and store:
                await store.mark_failed(resolved, _ctx)
            raise
        else:
            if resolved and store:
                await store.mark_consumed(resolved, _ctx)
            return result
        finally:
            if resolved:
                await resolved.cleanup()

    execution = await run_operation(
        operation="resources.add_skill",
        telemetry=request.telemetry,
        fn=_add,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)


class ReassembledResource(BaseModel):
    uri: str
    kind: str  # "file" | "directory"
    content: str
    chunk_count: int
    is_complete: bool


def _is_reassemblable_md(name: str) -> bool:
    return name.endswith(".md") and name not in _RESERVED_FILENAMES


async def _collect_chunks(
    dir_uri: str,
    ctx: RequestContext,
) -> Tuple[List[Tuple[Optional[int], Optional[int], str, str]], bool]:
    """Walk a chunked resource directory depth-first, returning chunks as
    (chunk_index, chunk_total, sort_path, content_uri) tuples plus a hint
    whether sidecar metadata was found anywhere in the tree.

    ``sort_path`` is a stable filename-based key used as fallback when
    ``chunk_index`` is missing. Only ``.md`` leaves that aren't ``.abstract.md``
    or ``.overview.md`` are returned.
    """
    viking_fs = get_viking_fs()
    out: List[Tuple[Optional[int], Optional[int], str, str]] = []
    saw_metadata = False

    async def walk(uri: str, rel: str) -> None:
        nonlocal saw_metadata
        try:
            entries = await viking_fs.ls(uri, ctx=ctx, show_all_hidden=True)
        except FileNotFoundError:
            return
        sidecar: Dict[str, Dict[str, int]] = {}
        for entry in entries:
            if entry.get("name") == _CHUNKS_SIDECAR_NAME and not entry.get("isDir"):
                try:
                    raw = await viking_fs.read_file(entry.get("uri") or f"{uri}/{_CHUNKS_SIDECAR_NAME}", ctx=ctx)
                    parsed = _json.loads(raw)
                    chunks = parsed.get("chunks") if isinstance(parsed, dict) else None
                    if isinstance(chunks, dict):
                        sidecar = {k: v for k, v in chunks.items() if isinstance(v, dict)}
                        saw_metadata = True
                except Exception:
                    pass
                break
        for entry in entries:
            name = entry.get("name", "")
            if name in (".", ".."):
                continue
            entry_uri = entry.get("uri") or f"{uri}/{name}"
            if entry.get("isDir"):
                await walk(entry_uri, f"{rel}/{name}" if rel else name)
            else:
                if not _is_reassemblable_md(name):
                    continue
                meta = sidecar.get(name) or {}
                idx = meta.get("chunk_index") if isinstance(meta.get("chunk_index"), int) else None
                total = meta.get("chunk_total") if isinstance(meta.get("chunk_total"), int) else None
                sort_path = f"{rel}/{name}" if rel else name
                out.append((idx, total, sort_path, entry_uri))

    await walk(dir_uri, "")
    return out, saw_metadata


@router.get("/resources/full", response_model=ReassembledResource)
async def get_full_resource(
    uri: str = Query(..., min_length=1),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Return the reassembled original content for a file or chunked resource directory."""
    viking_fs = get_viking_fs()
    try:
        info = await viking_fs.stat(uri, ctx=_ctx)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not info.get("isDir", False):
        try:
            content = await viking_fs.read_file(uri, ctx=_ctx)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if len(content.encode("utf-8")) > _MAX_REASSEMBLY_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Resource exceeds {_MAX_REASSEMBLY_BYTES}-byte reassembly cap.",
            )
        return ReassembledResource(
            uri=uri, kind="file", content=content, chunk_count=1, is_complete=True
        )

    chunks, saw_metadata = await _collect_chunks(uri, _ctx)
    is_complete = True
    if saw_metadata and chunks:
        # Use indexed sort when any sidecar was found, but flag incompleteness if
        # any chunk lacks an index, indices collide, or the set isn't 0..N-1.
        indexed = [c for c in chunks if c[0] is not None]
        if len(indexed) != len(chunks):
            is_complete = False
        seen: Dict[int, int] = {}
        totals = {c[1] for c in indexed if c[1] is not None}
        for idx, _total, _sp, _u in indexed:
            seen[idx] = seen.get(idx, 0) + 1
        if any(v > 1 for v in seen.values()):
            is_complete = False
        if len(totals) == 1:
            expected = next(iter(totals))
            if expected != len(chunks) or set(seen) != set(range(expected)):
                is_complete = False
        chunks.sort(key=lambda c: (c[0] if c[0] is not None else 1 << 30, c[2]))
    else:
        chunks.sort(key=lambda c: c[2])

    parts: List[str] = []
    total_bytes = 0
    for _idx, _total, _sp, content_uri in chunks:
        try:
            text = await viking_fs.read_file(content_uri, ctx=_ctx)
        except FileNotFoundError:
            is_complete = False
            continue
        encoded_len = len(text.encode("utf-8"))
        if total_bytes + encoded_len > _MAX_REASSEMBLY_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Reassembled resource exceeds {_MAX_REASSEMBLY_BYTES}-byte cap.",
            )
        total_bytes += encoded_len
        parts.append(text)

    content = "\n\n".join(parts)
    return ReassembledResource(
        uri=uri,
        kind="directory",
        content=content,
        chunk_count=len(chunks),
        is_complete=is_complete,
    )
