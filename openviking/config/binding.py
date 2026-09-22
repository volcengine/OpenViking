# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bind the generic runtime-config machinery to concrete OpenViking models.

The manager receives hooks for cluster publication, model construction and
request validation. This module binds those hooks to ``OpenVikingConfig`` and
``AccountConfig``; service startup supplies the source selection and only the
kernel-owned file source receives its AGFS client.
"""

from __future__ import annotations

from typing import Optional

from openviking.config.account_config import AccountConfig
from openviking.config.assembly import build_config_source, resolve_config_source_settings
from openviking.config.manager import RuntimeConfigManager
from openviking.config.merge import apply_three_state_patch
from openviking.config.source.base import ConfigSource
from openviking.config.source.file_source import FileConfigSource
from openviking.config.validate import validate_patch
from openviking.pyagfs import AsyncAGFSClient
from openviking_cli.utils.config import get_openviking_config, set_openviking_config
from openviking_cli.utils.config.config_utils import warn_unknown_config_fields
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig, RuntimeConfigSettings
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# The concrete manager type this module hands back.
OpenVikingRuntimeConfigManager = RuntimeConfigManager[OpenVikingConfig, AccountConfig]


def _build_cluster(old: OpenVikingConfig, override: dict) -> OpenVikingConfig:
    """Rebuild the cluster config from its startup baseline plus an override.

    Apply the complete stored override to the immutable startup model, then run
    ``from_dict`` validation. Reusing the previously published effective model
    would retain values after their override is deleted.

    ``by_alias=True`` is required for the dump→``from_dict`` round-trip: several
    fields (e.g. ``storage.vectordb.project_name`` aliased to ``project``) are
    only accepted by ``from_dict`` under their alias, while the bare
    ``model_dump()`` emits the field name and would be rejected as unknown. A
    stored override may contain top-level fields from a newer binary, so that
    runtime-only rebuild intentionally ignores those fields.
    """
    merged = apply_three_state_patch(
        old.model_dump(by_alias=True, exclude_unset=True),
        override or {},
    )
    return OpenVikingConfig.from_dict(merged)


def _build_account(override: Optional[dict]) -> AccountConfig:
    """Construct (and thereby validate) an account config from its sparse override.

    A persisted account override may carry fields this binary does not declare --
    a retired ``namespace`` block, or a section written by a newer binary. They
    stay ignored so legacy settings keep loading, and the warning keeps the drop
    visible. Only field names are logged, never values.
    """
    sparse = override or {}
    warn_unknown_config_fields(data=sparse, model=AccountConfig, logger=logger)
    return AccountConfig.model_validate(sparse)


def _validate_request(patch: dict, is_account: bool, creating: bool) -> None:
    """Structural gate: which model's RuntimeField surface a patch may touch."""
    model = AccountConfig if is_account else OpenVikingConfig
    validate_patch(model, patch, creating=creating)


def build_runtime_config_manager(
    agfs_client: AsyncAGFSClient,
    *,
    settings: RuntimeConfigSettings | dict | None = None,
    base_config: OpenVikingConfig | None = None,
) -> OpenVikingRuntimeConfigManager:
    """Construct a fully-wired manager over the selected config source.

    The built-in ``file`` source receives the service-owned ``agfs_client`` in
    this kernel-only branch. Every other source is built by the plugin registry
    with its configured ``params`` only, so plugins never receive AGFS.
    """
    resolved = resolve_config_source_settings(settings)
    if resolved.source.strip().lower() == "file":
        source = FileConfigSource(agfs_client)
    else:
        source = build_config_source(resolved)
    return manager_over_source(source, base_config=base_config)


def manager_over_source(
    source: ConfigSource,
    *,
    base_config: OpenVikingConfig | None = None,
) -> OpenVikingRuntimeConfigManager:
    """Wire the concrete OpenViking hooks onto an arbitrary :class:`ConfigSource`.

    Split out from :func:`build_runtime_config_manager` so callers that already
    hold a source (e.g. tests using an in-memory source) reuse the same
    cluster/account binding instead of duplicating the hook set. When omitted,
    ``base_config`` is captured from the current singleton at construction time.
    """
    base = base_config or get_openviking_config()
    return RuntimeConfigManager(
        source,
        base_config=base,
        get_config=get_openviking_config,
        set_config=set_openviking_config,
        build_config=_build_cluster,
        build_account=_build_account,
        validate_request=_validate_request,
    )
