# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import pytest
import pytest_asyncio

from openviking.extensions.periodic_report import (
    PeriodicReportArchiveProvider,
    PeriodicReportBundle,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.exceptions import AlreadyExistsError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig


def _activation_receipt() -> dict[str, Any]:
    profile = {
        "schema_version": "periodic_report_profile_v0",
        "enabled": True,
        "profile_id": "openviking_weekly",
        "profile_version": "v1",
        "sink_bindings": [
            {
                "schema_version": "periodic_report_sink_binding_v0",
                "sink_id": "project_archive",
                "sink_kind": "project_resource",
                "sink_role": "archive",
                "dependency_policy": "optional",
                "capability": {
                    "capability_id": "report.archive.write",
                    "capability_version": "v0",
                },
                "extension": {
                    "extension_id": "openviking.periodic-report.archive",
                    "extension_version": "0.1.0",
                    "protocol": "periodic_report_sink_v0",
                },
            }
        ],
    }
    profile_digest = sha256(
        json.dumps(
            profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "periodic_report_activation_v0",
        "status": "enabled",
        "active": True,
        "generation_allowed": True,
        "profile_digest": f"sha256:{profile_digest}",
        "profile": profile,
    }


class VikingFSArchiveClient:
    """Bind the provider to the real embedded Resource filesystem."""

    def __init__(self, viking_fs: VikingFS) -> None:
        self._fs = viking_fs
        self._ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        return await self._fs.read_file(uri, ctx=self._ctx, offset=offset, limit=limit)

    async def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: float | None = None,
        telemetry: bool = False,
    ) -> dict[str, Any]:
        del wait, timeout, telemetry
        if mode == "create" and await self._fs.exists(uri, ctx=self._ctx):
            raise AlreadyExistsError(uri, "file")
        await self._fs.write_file(uri, content, ctx=self._ctx)
        return {"uri": uri}

    async def glob(self, pattern: str, uri: str = "viking://") -> dict[str, Any]:
        return await self._fs.glob(pattern, ctx=self._ctx, uri=uri)


@pytest_asyncio.fixture
async def report_viking_fs(temp_dir, monkeypatch):
    """Create a real local RAGFS-backed VikingFS without vector/model services."""

    config_path = temp_dir / "ov.conf"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    agfs_config = AGFSConfig(path=str(temp_dir / "data"), backend="local")
    try:
        agfs_client = create_agfs_client(RagfsBindingConfig(agfs=agfs_config))
    except ImportError as exc:
        pytest.skip(f"RAGFS native binding is unavailable: {exc}")
    viking_fs = VikingFS(agfs=agfs_client)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await viking_fs.mkdir("viking://resources/", exist_ok=True, ctx=ctx)
    yield viking_fs


@pytest.mark.asyncio
async def test_periodic_report_resource_canary(report_viking_fs) -> None:
    client = VikingFSArchiveClient(report_viking_fs)
    provider = PeriodicReportArchiveProvider(client)
    bundle = PeriodicReportBundle(
        project_key="openviking",
        profile_id="openviking_weekly",
        profile_version="v1",
        report_id="weekly-2026-07-20",
        period_start="2026-07-13",
        period_end="2026-07-19",
        generated_at="2026-07-19T09:00:00+08:00",
        markdown="# OpenViking weekly report\n\n- Resource canary passed",
        html="<h1>OpenViking weekly report</h1><p>Resource canary passed</p>",
        metadata={"trigger": "cadence", "renderer": "ov-weekly"},
    )

    first_result = await provider.archive_sink(
        bundle,
        sink_id="project_archive",
        idempotency_key="report_sink_canary",
        activation_receipt=_activation_receipt(),
    )
    first = await provider.readback(first_result["receipt_ref"])
    second_result = await provider.archive_sink(
        bundle,
        sink_id="project_archive",
        idempotency_key="report_sink_canary",
        activation_receipt=_activation_receipt(),
    )
    records = await provider.query(project_key="openviking")

    assert first_result["write_status"] == "created"
    assert first.exact_readback_verified is True
    assert second_result["write_status"] == "already_present"
    assert second_result["result_id"] == first.result_id
    assert records[0]["receipt"]["result_id"] == first.result_id
    assert await client.read(first.markdown_uri) == bundle.markdown
