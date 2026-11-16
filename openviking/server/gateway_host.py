# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Construction of the optional embedded gateway, and the route class /mcp shares."""

import os
from pathlib import Path

from starlette.routing import Match, Route

from openviking.server.config import get_server_url_from_server_data
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

LEGACY_STORAGE_PATH = "~/.openviking/gateway"


class ScopedRoute(Route):
    """Expose the selected route through ``scope["route"]``, matching
    ``APIRoute.matches``, so outer observability can resolve the static route template."""

    def matches(self, scope):
        match, child_scope = super().matches(scope)
        if match != Match.NONE:
            child_scope["route"] = self
        return match, child_scope


def default_storage_path(workspace: str) -> str:
    """Keep gateway state beside the server's data, unless only the legacy location has it."""
    current = Path(workspace).expanduser() / "gateway"
    legacy = Path(LEGACY_STORAGE_PATH).expanduser()
    if not (current / "kernel.sqlite3").exists() and (legacy / "kernel.sqlite3").exists():
        logger.warning(
            "Using legacy gateway storage %s; the default is now %s. Copy the directory there "
            "or set gateway.storage_path to keep this location.",
            legacy,
            current,
        )
        return str(legacy)
    return str(current)


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
    update = {"min_server_version": "0"}
    # Without its own public_url the gateway shares the server's public address, which
    # MCP upload links already use (env first, then ov.conf).
    if not config.public_url and (
        public := os.environ.get("OPENVIKING_PUBLIC_BASE_URL") or server_config.public_base_url
    ):
        update["public_url"] = normalize_url(public)
    if "storage_path" not in config.model_fields_set:
        update["storage_path"] = default_storage_path(openviking_config.storage.workspace)
    return create_app(
        config.model_copy(update=update),
        openviking_url=get_server_url_from_server_data(server_config),
        vlm_source=vlm_source,
        encryptor=_run_coro_blocking(bootstrap_encryption(encryption)),
    )
