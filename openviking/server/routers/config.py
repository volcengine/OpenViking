# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration management endpoints for OpenViking HTTP Server."""

import contextlib
import json
import os
import tempfile
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

from fastapi import APIRouter, Request
from pydantic import ValidationError

from openviking.server.auth import require_role
from openviking.server.config import ServerConfig, load_server_config
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.schemas import ExcludeNoneRoute
from openviking.server.schemas.config import ServerConfigView
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/config",
    tags=["config"],
    route_class=ExcludeNoneRoute,
)


@contextlib.contextmanager
def _file_lock(path: Path):
    """Acquire an exclusive file lock (no-op on platforms without fcntl).

    Lock is released implicitly when the fd is closed on block exit.
    """
    if fcntl is None:
        yield
        return
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield


def _sanitize_config(config: ServerConfig) -> dict:
    """Return config as dict with sensitive fields removed."""
    data = config.model_dump()
    data.pop("root_api_key", None)
    return data


@router.get("", response_model=Response[ServerConfigView])
async def get_config(
    request: Request,
    _ctx: RequestContext = require_role(Role.ROOT),
) -> Response[ServerConfigView]:
    """Return the persisted server configuration from ov.conf (sanitized)."""
    config = load_server_config()
    return Response(
        status="ok",
        result=ServerConfigView.model_validate(_sanitize_config(config)),
    )


_IMMUTABLE_FIELDS = {"root_api_key", "encryption_enabled"}


@router.put("", response_model=Response[ServerConfigView])
async def update_config(
    request: Request,
    body: dict,
    ctx: RequestContext = require_role(Role.ROOT),
) -> Response[ServerConfigView]:
    """Validate and persist server configuration to ov.conf.

    Changes are written to disk only. A server restart is required
    for any configuration change to take effect at runtime.
    """
    # Strip fields not managed by this endpoint from input
    for key in _IMMUTABLE_FIELDS:
        body.pop(key, None)
    path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if path is None:
        raise InvalidArgumentError("Configuration file not found")
    with _file_lock(path):
        # Read raw JSON without env var expansion to preserve $VAR references on write-back
        full = json.loads(path.read_text(encoding="utf-8-sig"))
        # Merge onto disk state so consecutive partial PUTs accumulate
        current_server = full.get("server") or {}
        merged = {**current_server, **body}
        try:
            config = ServerConfig.model_validate(merged)
        except ValidationError as e:
            raise InvalidArgumentError(
                "Invalid server config update", details={"errors": e.errors()}
            ) from None
        # Update only mutable fields, preserving immutable ones (e.g. root_api_key) on disk
        if full.get("server") is None:
            full["server"] = {}
        full["server"].update(config.model_dump(exclude=_IMMUTABLE_FIELDS))
        content = json.dumps(full, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content.encode())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except BaseException:
            os.unlink(tmp)
            raise
    logger.info(
        "Configuration updated by %s, fields changed: %s, path: %s",
        ctx.user,
        sorted(body.keys()),
        path,
    )
    return Response(
        status="ok",
        result=ServerConfigView.model_validate(_sanitize_config(load_server_config())),
    )
