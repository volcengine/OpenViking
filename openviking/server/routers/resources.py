# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.server.auth import get_request_context, get_upload_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import require_remote_resource_source
from openviking.server.resource_ingest import ingest_temp_upload
from openviking.server.responses import response_from_result
from openviking.server.skill_source_metadata import persist_skill_source_metadata
from openviking.server.telemetry import run_operation
from openviking.server.temp_upload_store import TempUploadStore
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError

router = APIRouter(prefix="/api/v1", tags=["resources"])


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
    target_uri: Optional[str] = None
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
    _ctx: RequestContext = Depends(get_upload_request_context),
):
    """Upload a temporary file for add_resource or import_ovpack.

    Two auth layers (see :func:`get_upload_request_context`): with an API key the file is
    stored and its ``temp_file_id`` returned (used by the CLI and ``import_ovpack``). With a
    signed ``?token=`` — minted by the MCP ``add_resource`` tool for local-file paths — the
    server additionally finishes ingestion in-request: it resolves the upload, calls
    ``add_resource`` with the token-bound ``to``/``reason``, and returns the final result, so
    the agent never needs a second call. The ``?token=`` query param is consumed by the auth
    dependency.
    """
    signed = getattr(request.state, "signed_upload", None)

    async def _upload() -> dict[str, Any]:
        store = TempUploadStore.build(request.app.state.config)
        temp_file_id = await store.save_upload(file, upload_mode, _ctx)
        if signed is None:
            return {"temp_file_id": temp_file_id}
        return await ingest_temp_upload(
            store, temp_file_id, _ctx, to=signed.to, reason=signed.reason
        )

    try:
        execution = await run_operation(
            operation="resources.temp_upload",
            telemetry=telemetry,
            fn=_upload,
        )
    except InvalidArgumentError as exc:
        if signed is None:
            raise
        # save_upload raises InvalidArgumentError for both bad mode and oversize. The signed
        # route mapped oversize to 413 and the rest to 400 before the routes merged; preserve
        # that contract for the token path.
        msg = str(exc)
        status = 413 if "exceeds size limit" in msg else 400
        raise HTTPException(status_code=status, detail=msg) from exc
    return response_from_result(execution.result, telemetry=execution.telemetry)


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
                target_uri=request.target_uri,
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


class UrlImageIndexRequest(BaseModel):
    """Request model for the URL-image vector index endpoint.

    Index a remote image as a multimodal vector under ``target_uri`` without
    persisting the image bytes in OpenViking's storage layer. The configured
    multimodal embedder is given the URL directly and is expected to fetch
    the image itself (Volcengine / Doubao multimodal endpoints accept
    ``image_url`` natively).
    """

    model_config = ConfigDict(extra="forbid")

    target_uri: str = Field(
        ...,
        description=(
            "Viking URI to index the resulting vector at, e.g. "
            "``viking://resources/products/M14253/images/0.embed``. The URI's "
            "scope must match the request's ``X-OpenViking-Account``."
        ),
    )
    image_url: str = Field(
        ...,
        description=(
            "Remote ``https://`` URL of the image to embed. Forwarded to the "
            "multimodal embedder as-is; the embedder (or its upstream provider) "
            "fetches the URL. The URL is also recorded in the vectordb scalar "
            "fields for later lookup."
        ),
    )
    summary: Optional[str] = Field(
        default=None,
        description=(
            "Optional text accompanying the image (mirrors the "
            "``image_and_summary`` ingest mode). When set, the multimodal "
            "embedding is computed over both the text and the image."
        ),
    )
    name: Optional[str] = Field(
        default=None,
        description="Optional display name for the indexed record. Defaults to the URI tail.",
    )
    context_type: str = Field(
        default="resource",
        description="Vectordb scalar field 'context_type'. One of 'resource', 'memory', 'skill'.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extra scalar fields to attach to the indexed record (e.g. sku, "
            "image_idx). Reserved keys (uri, account_id, vector, id, level, "
            "context_type, abstract, content, name) are stripped — those are "
            "set by the server."
        ),
    )
    telemetry: TelemetryRequest = False


_URL_IMAGE_RESERVED_KEYS = {
    "id",
    "uri",
    "account_id",
    "owner_user_id",
    "vector",
    "sparse_vector",
    "abstract",
    "overview",
    "content",
    "name",
    "level",
    "context_type",
    "created_at",
    "updated_at",
}


@router.post("/resources/url_image_index")
async def url_image_index(
    http_request: Request,
    request: UrlImageIndexRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Embed a remote image URL and insert the vector at ``target_uri`` without
    persisting any bytes in agfs.

    This is the "bring your own image hosting" entrypoint: the caller's image
    lives in some external bucket (tenant-owned S3, public CDN, etc.) and only
    the resulting vector is stored locally. Compared to the standard ingest
    pipeline (``temp_upload`` + ``/resources``) this endpoint:

    * Does **not** write any file to ``agfs`` (no temp upload, no resource tree).
    * Does **not** spawn semantic / abstract-generation work — there is no
      ``.abstract.md`` / ``.overview.md`` produced for the indexed URI.
    * Does **not** go through the embedding queue. The embedder is called
      synchronously and the vector is upserted before the response returns.

    The configured multimodal embedder is reused as-is: the same provider /
    failover / circuit-breaker / dim-validation enforcement applies as the
    normal ingest path. A text-only embedder (``supports_multimodal`` is
    ``False``) is rejected with HTTP 400.
    """
    from openviking.core.namespace import canonicalize_uri
    from openviking.core.uri_validation import validate_viking_uri
    from openviking.models.embedder.base import embed_compat
    from openviking_cli.utils.config import get_openviking_config
    import hashlib

    service = get_service()
    vikingdb = service.vikingdb_manager
    if vikingdb is None:
        raise HTTPException(status_code=503, detail="vectordb not initialized")

    # Rejects malformed URIs (wrong scheme, internal scopes, etc.) — surfaced
    # as InvalidURIError → 400 by the standard error middleware.
    raw_uri = validate_viking_uri(request.target_uri, field_name="target_uri")

    image_url = (request.image_url or "").strip()
    if not image_url or not (
        image_url.startswith("http://")
        or image_url.startswith("https://")
        or image_url.startswith("data:image/")
    ):
        raise InvalidArgumentError(
            "image_url must be a remote http(s) URL or a data:image/* URI"
        )

    if request.context_type not in {"resource", "memory", "skill"}:
        raise InvalidArgumentError(
            "context_type must be one of: resource, memory, skill"
        )

    canonical_uri = canonicalize_uri(raw_uri, _ctx)

    config = get_openviking_config()
    embedder = config.embedding.get_embedder()
    if embedder is None:
        raise HTTPException(status_code=503, detail="embedder not initialized")
    if not getattr(embedder, "supports_multimodal", False):
        raise InvalidArgumentError(
            "configured embedder does not support multimodal input — "
            "this endpoint requires a multimodal embedder (e.g. Volcengine "
            "with input='multimodal')"
        )

    # Build the multimodal embedding input directly. The volcengine embedder's
    # `to_multimodal_input` passes each entry's `image_url.url` straight through
    # to the upstream Ark / DashScope API — both accept remote https URLs.
    parts: list[Dict[str, Any]] = []
    summary_text = (request.summary or "").strip()
    if summary_text:
        parts.append({"type": "text", "text": summary_text})
    parts.append({"type": "image_url", "image_url": {"url": image_url}})

    async def _embed_and_upsert() -> Dict[str, Any]:
        result = await embed_compat(embedder, parts, is_query=False)
        vector = result.dense_vector
        if not vector:
            raise HTTPException(status_code=502, detail="embedder returned empty vector")
        expected_dim = config.embedding.dimension
        if expected_dim and len(vector) != expected_dim:
            raise InvalidArgumentError(
                f"embedder returned dim {len(vector)}, expected {expected_dim}"
            )

        # Deterministic ID matching the convention used by TextEmbeddingHandler
        # so that repeat calls with the same (account, uri) upsert in place.
        record_id = hashlib.md5(
            f"{_ctx.account_id}:{canonical_uri}".encode("utf-8")
        ).hexdigest()

        record_name = request.name or canonical_uri.rsplit("/", 1)[-1] or canonical_uri

        # Strip reserved keys from caller metadata to avoid spoofing of fields
        # the server controls (id, vector, account_id, ...).
        extra_meta = {
            k: v
            for k, v in (request.metadata or {}).items()
            if k not in _URL_IMAGE_RESERVED_KEYS
        }

        inserted_data: Dict[str, Any] = {
            "id": record_id,
            "uri": canonical_uri,
            "account_id": _ctx.account_id,
            "context_type": request.context_type,
            "level": 2,
            "name": record_name,
            "abstract": summary_text,
            "content": summary_text,
            "vector": vector,
            "image_url": image_url,
            **extra_meta,
        }

        if result.sparse_vector is not None:
            inserted_data["sparse_vector"] = result.sparse_vector

        await vikingdb.upsert(inserted_data, ctx=_ctx, partial_update=True)
        return {"status": "ok", "uri": canonical_uri, "id": record_id}

    execution = await run_operation(
        operation="resources.url_image_index",
        telemetry=request.telemetry,
        fn=_embed_and_upsert,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)
