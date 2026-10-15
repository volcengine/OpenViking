# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Construction of the optional embedded gateway, and the route class /mcp shares."""

import os
from pathlib import Path

from starlette.routing import Match, Route

from openviking.server.config import get_server_url_from_server_data
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Where the gateway stored its databases before it followed the server's workspace.
LEGACY_STORAGE_PATH = "~/.openviking/gateway"
GATEWAY_DATABASES = ("management.sqlite3", "kernel.sqlite3")


class ScopedRoute(Route):
    """Expose the selected route through ``scope["route"]``, matching
    ``APIRoute.matches``, so outer observability can resolve the static route template."""

    def matches(self, scope):
        match, child_scope = super().matches(scope)
        if match != Match.NONE:
            child_scope["route"] = self
        return match, child_scope


def embedded_storage_path(config, workspace):
    """Directory for the embedded gateway's databases.

    An explicit ``storage_path`` (ov.conf or ``OPENVIKING_GATEWAY_STORAGE_PATH``) always wins.
    Otherwise the data lives under the server's ``storage.workspace``, so it persists wherever
    OpenViking's own data persists, for example on a container volume that does not cover
    ``$HOME``. A server whose gateway already wrote to the old home-directory default keeps
    using that directory, so an upgrade never hides existing upstreams, profiles or keys.
    """
    if "storage_path" in config.model_fields_set or not workspace:
        return config.storage_path
    legacy = Path(LEGACY_STORAGE_PATH).expanduser()
    if any((legacy / name).exists() for name in GATEWAY_DATABASES):
        logger.warning(
            "OpenViking Gateway data is in %s, outside storage.workspace. Set gateway.storage_path "
            "if that directory is not persistent; see docs/en/guides/22-gateway-operations.md",
            legacy,
        )
        return str(legacy)
    return str(Path(workspace).expanduser() / "gateway")


def create_embedded_gateway(openviking_config, server_config, service_source):
    config = openviking_config.gateway
    if not config.enabled:
        return None
    from openviking.crypto.config import bootstrap_encryption
    from openviking.utils.agfs_utils import _run_coro_blocking
    from openviking_gateway.app import create_app
    from openviking_gateway.config import normalize_url

    async def vlm_source(account_id):
        from openviking.server.gateway_vlm import vlm_upstreams

        resolver = service_source().vlm_resolver
        if resolver is None:
            raise RuntimeError("Account VLM configuration is not initialized")
        return vlm_upstreams(await resolver.effective_vlm_config(account_id))

    # The gateway shares the server's encryption settings; None when OV encryption is disabled.
    encryption = {"encryption": openviking_config.encryption.model_dump()}
    # The embedded gateway ships with this server, so the minimum-version gate never applies.
    workspace = getattr(getattr(openviking_config, "storage", None), "workspace", None)
    update = {
        "min_server_version": "0",
        "storage_path": embedded_storage_path(config, workspace),
    }
    # Without its own public_url the gateway shares the server's public address, which
    # MCP upload links already use (env first, then ov.conf).
    if not config.public_url and (
        public := os.environ.get("OPENVIKING_PUBLIC_BASE_URL") or server_config.public_base_url
    ):
        update["public_url"] = normalize_url(public)
    return create_app(
        config.model_copy(update=update),
        openviking_url=get_server_url_from_server_data(server_config),
        vlm_source=vlm_source,
        encryptor=_run_coro_blocking(bootstrap_encryption(encryption)),
    )
