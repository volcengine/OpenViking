# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared resource-ingestion request contract and helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_content_target_uri
from openviking.parse.mode import ParseMode
from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import require_remote_resource_source
from openviking.server.temp_upload_store import TempUploadStore
from openviking.storage.acl import AclSpec
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError


def get_service() -> Any:
    """Resolve the active service lazily to avoid a service-core import cycle."""
    from openviking.server.dependencies import get_service as resolve_service

    return resolve_service()


class AddResourceRequest(BaseModel):
    """Request model shared by resource and rule-source ingestion.

    ``path`` accepts remote resources; local files must first be uploaded and
    referenced by ``temp_file_id``. ``add_type`` explicitly selects a Connector
    source type and requires ``path`` plus an exact ``to`` target. It cannot be
    combined with ``temp_file_id`` or ``parent``.

    ``args`` carries parser or Connector-specific options. ``watch_interval``
    creates a Watch when positive; ``is_active=False`` creates that Watch paused
    and therefore requires both a positive interval and an explicit target.
    ``wait`` and ``internal_task`` are transport defaults that callers may
    override when the resource is imported as part of another operation.

    ``connector_auth_config`` is an OpenViking-managed private credential channel
    injected by the data plane for saved-credential OAuth imports. It carries only
    ``openviking_credential_ref``, a secret-free reference resolved into the current
    Connector ``auth_config`` for each import or watch round.
    """

    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = None
    temp_file_id: Optional[str] = None
    add_type: Optional[str] = None
    to: Optional[str] = None
    parent: Optional[str] = None
    create_parent: bool = False
    reason: str = ""
    instruction: str = ""
    wait: bool = False
    timeout: float | None = Field(
        default=None,
        deprecated=True,
        description="Deprecated compatibility parameter; accepted and ignored.",
    )
    strict: bool = False
    internal_task: bool = False
    source_name: Optional[str] = None
    ignore_dirs: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    directly_upload_media: bool = True
    preserve_structure: Optional[bool] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    connector_auth_config: Optional[Dict[str, Any]] = None
    telemetry: TelemetryRequest = False
    watch_interval: float = 0
    is_active: bool = True
    processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE
    tags: Optional[list[str]] = None
    tag_mode: str = "replace"
    acl: AclSpec | None = None

    @model_validator(mode="after")
    def check_path_or_temp_file_id(self):
        if not self.path and not self.temp_file_id:
            raise ValueError("Either 'path' or 'temp_file_id' must be provided")
        return self

    @model_validator(mode="after")
    def check_add_type(self):
        if self.add_type is not None:
            self.add_type = self.add_type.strip() or None
        if self.add_type and self.temp_file_id:
            raise ValueError("'add_type' cannot be combined with 'temp_file_id'")
        if self.add_type and not self.path:
            raise ValueError("'add_type' requires 'path'")
        if self.add_type and self.parent:
            raise ValueError("'add_type' cannot be combined with 'parent'")
        if self.add_type and not self.to:
            raise ValueError("'add_type' requires an exact 'to' target")
        return self

    @model_validator(mode="after")
    def check_paused_watch(self):
        has_target = bool((self.to or "").strip() or (self.parent or "").strip())
        if self.is_active is False and (self.watch_interval <= 0 or not has_target):
            raise ValueError(
                "is_active=false requires watch_interval > 0 and either 'to' or 'parent'"
            )
        return self


async def ingest_resource_request(
    request: AddResourceRequest,
    ctx: RequestContext,
    *,
    resources: Any,
    config: Any,
    wait: bool,
    internal_task: bool,
) -> dict[str, Any]:
    """Validate, resolve, and import one resource request."""
    to_uri = resolve_path_variables(request.to).strip() if request.to else ""
    if to_uri:
        to_uri = validate_content_target_uri(to_uri, ctx, kind="resource", field_name="to")
    parent_uri = resolve_path_variables(request.parent).strip() if request.parent else ""
    if parent_uri:
        parent_uri = validate_content_target_uri(
            parent_uri,
            ctx,
            kind="resource",
            field_name="parent",
        )

    path = request.path
    allow_local_path_resolution = False
    original_filename = None
    resolved = None
    shared_source_ref = None
    if request.temp_file_id:
        if request.watch_interval > 0:
            raise InvalidArgumentError(
                "watch_interval > 0 is not supported for uploaded content: an "
                "upload is a static snapshot, so the watch would re-process "
                "stale content forever. Watch a URL / "
                "sitemap / RSS source instead, or re-add the resource when the "
                "source changes."
            )
        server_config = config
        if not hasattr(server_config, "temp_upload"):
            from openviking.server.dependencies import get_server_config

            server_config = get_server_config()
        if server_config is None:
            raise RuntimeError("Server configuration is unavailable for temporary upload")
        store = TempUploadStore.build(server_config)
        # Shared uploads already live in durable storage, so workers can consume
        # the reference directly instead of staging a second API-side copy.
        shared_source_ref = await store.resolve_shared_reference(request.temp_file_id, ctx)
        if shared_source_ref is not None:
            path = shared_source_ref.original_filename or request.temp_file_id
            original_filename = shared_source_ref.original_filename or None
            allow_local_path_resolution = True
        else:
            resolved = await store.resolve_for_consume(request.temp_file_id, ctx)
            path = resolved.local_path
            original_filename = resolved.original_filename
            allow_local_path_resolution = True
    elif path is not None:
        path = require_remote_resource_source(
            path,
            declared_connector_add_type=request.add_type,
        )
    if path is None:
        raise InvalidArgumentError("Either 'path' or 'temp_file_id' must be provided.")

    source_name = request.source_name or original_filename
    kwargs = {
        "strict": request.strict,
        "source_name": source_name,
        "ignore_dirs": request.ignore_dirs,
        "include": request.include,
        "exclude": request.exclude,
        "directly_upload_media": request.directly_upload_media,
        "watch_interval": request.watch_interval,
        "processing_mode": request.processing_mode,
    }
    # Connector routing distinguishes an omitted create_parent from explicit false.
    if "create_parent" in request.model_fields_set:
        kwargs["create_parent"] = request.create_parent
    if request.temp_file_id and request.watch_interval <= 0:
        kwargs["temp_file_id"] = request.temp_file_id
    if request.preserve_structure is not None:
        kwargs["preserve_structure"] = request.preserve_structure

    try:
        return await resources.add_resource(
            path=path,
            ctx=ctx,
            add_type=request.add_type,
            to=to_uri,
            parent=parent_uri,
            reason=request.reason,
            instruction=request.instruction,
            wait=wait,
            acl=request.acl,
            tags=request.tags,
            tag_mode=request.tag_mode,
            allow_local_path_resolution=allow_local_path_resolution,
            enforce_public_remote_targets=True,
            internal_task=internal_task,
            is_active=request.is_active,
            args=request.args,
            connector_auth_config=request.connector_auth_config,
            shared_source=shared_source_ref,
            **kwargs,
        )
    finally:
        if resolved:
            await resolved.cleanup()


async def ingest_temp_upload(
    store: TempUploadStore,
    temp_file_id: str,
    ctx: RequestContext,
    *,
    to: str = "",
    parent: str = "",
    reason: str = "",
    args: Optional[dict[str, Any]] = None,
    processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
    tags: Optional[list[str]] = None,
    tag_mode: str = "replace",
    parse_mode: ParseMode | str = ParseMode.DEFAULT,
    acl: AclSpec | None = None,
) -> dict[str, Any]:
    """Resolve a temp upload and ingest it as a resource; return the raw add_resource result.

    The return value is the service's own dict — either a success payload (containing
    ``root_uri``) or a business-error dict (``{"status": "error", ...}``) that ``add_resource``
    returns WITHOUT raising. Callers MUST inspect ``status``: HTTP callers pass it through
    ``response_from_result`` (which maps errors to the right status code); the MCP tool formats
    it — so an ingestion failure is never reported as success. ``resolve_for_consume`` may
    raise (PermissionDenied / InvalidArgument) before anything is resolved — the caller
    surfaces that.
    """
    resolved = await store.resolve_for_consume(temp_file_id, ctx)
    try:
        try:
            ingest_args = dict(args or {})
            if parse_mode != ParseMode.DEFAULT and parse_mode != ParseMode.DEFAULT.value:
                ingest_args.setdefault(
                    "parse_mode",
                    str(parse_mode.value if isinstance(parse_mode, ParseMode) else parse_mode),
                )
            result = await get_service().resources.add_resource(
                path=resolved.local_path,
                ctx=ctx,
                to=to or None,
                parent=parent or None,
                create_parent=bool(parent),
                reason=reason,
                source_name=resolved.original_filename,
                wait=False,
                processing_mode=processing_mode,
                allow_local_path_resolution=True,
                enforce_public_remote_targets=True,
                temp_file_id=temp_file_id,
                args=ingest_args,
                tags=tags,
                tag_mode=tag_mode,
                acl=acl,
            )
        except Exception:
            raise
    finally:
        await resolved.cleanup()

    return result
