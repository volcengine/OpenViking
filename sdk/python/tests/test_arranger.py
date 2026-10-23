# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from typing import Any

import openviking_sdk
import pytest
from openviking_sdk import AsyncContextArranger, ContextArranger, SyncHTTPClient
from openviking_sdk.arranger import (
    ContextApplyResult,
    ContextChange,
    ContextChangeAction,
    ContextPlan,
    ContextResourceKind,
    LiveContext,
)
from openviking_sdk.errors import FailedPreconditionError, NotFoundError


class FakeClient:
    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[Any, ...]] = []

    async def ls(self, _uri: str, **kwargs: Any) -> list[dict[str, Any]]:
        owner_tag = kwargs["tags"][0]
        return [
            {"uri": uri, **entry}
            for uri, entry in self.entries.items()
            if owner_tag in entry.get("tags", [])
        ]

    async def stat(self, uri: str) -> dict[str, Any]:
        if uri not in self.entries:
            raise NotFoundError(uri, "file")
        return self.entries[uri]

    async def mount_mcp_tool(
        self,
        tool_uri: str,
        node_uri: str,
        title: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("mount", tool_uri, node_uri, title, kwargs))
        self.entries[node_uri] = {
            "isDir": False,
            "tags": list(kwargs.get("tags") or []),
            "tool_uri": tool_uri,
            "arguments": dict(kwargs["arguments"]),
        }
        return {}

    async def batch_mount_mcp_tools(
        self,
        mounts: list[dict[str, Any]],
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(("batch_mount", mounts, wait))
        for mount in mounts:
            self.entries[mount["node_uri"]] = {
                "isDir": False,
                "tags": list(mount.get("tags") or []),
                "tool_uri": mount["tool_uri"],
                "arguments": dict(mount["arguments"]),
            }
        return {"mounts": mounts, "total": len(mounts)}

    async def list_mcp_mounts(self, _mcp: str, *, tool: str) -> dict[str, Any]:
        mounts = [
            {
                "node_uri": uri,
                "tool_uri": entry["tool_uri"],
                "arguments": entry["arguments"],
                "tool": tool,
            }
            for uri, entry in self.entries.items()
            if "tool_uri" in entry
        ]
        return {"mounts": mounts, "total": len(mounts)}

    async def set_tags(
        self,
        uri: str,
        tags: list[str],
        *,
        mode: str,
    ) -> dict[str, Any]:
        self.calls.append(("set_tags", uri, tags, mode))
        self.entries[uri]["tags"] = list(tags)
        return {}

    async def unmount_mcp_tool(
        self,
        uri: str,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(("unmount", uri, wait))
        if uri not in self.entries:
            raise NotFoundError(uri, "live context mount")
        del self.entries[uri]
        return {}

    async def batch_unmount_mcp_tools(
        self,
        node_uris: list[str],
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(("batch_unmount", node_uris, wait))
        for uri in node_uris:
            if uri not in self.entries:
                raise NotFoundError(uri, "live context mount")
        for uri in node_uris:
            del self.entries[uri]
        return {"unmounts": node_uris, "total": len(node_uris)}

    async def rm(self, uri: str, *, wait: bool) -> None:
        self.calls.append(("rm", uri, wait))
        self.entries.pop(uri, None)


def declare_alerts(project: Any, *, service: str = "api") -> None:
    project.live_context(
        "alerts.md",
        "agent/tools/monitor/alerts.json",
        "Current alerts",
        arguments={"service": service},
    )


def test_top_level_package_only_exports_context_arranger_entry_points() -> None:
    assert openviking_sdk.ContextArranger is ContextArranger
    assert openviking_sdk.AsyncContextArranger is AsyncContextArranger
    for internal_type in (
        ContextApplyResult,
        ContextChange,
        ContextChangeAction,
        ContextPlan,
        ContextResourceKind,
        LiveContext,
    ):
        assert internal_type.__name__ not in openviking_sdk.__all__
        assert not hasattr(openviking_sdk, internal_type.__name__)


@pytest.mark.asyncio
async def test_async_project_converges_live_context() -> None:
    client = FakeClient()
    project = AsyncContextArranger(client, "incident-context", "resources/ops")
    declare_alerts(project)

    plan = await project.plan()
    assert plan.changes[0].action == ContextChangeAction.CREATE

    result = await project.apply(wait=True)
    assert len(result.applied) == 1
    assert [call[0] for call in client.calls] == ["batch_mount"]
    assert client.calls[0][2] is True
    assert project.owner_tag in client.entries["viking://resources/ops/alerts.md"]["tags"]

    converged = await project.plan()
    assert converged.has_changes is False
    assert converged.changes[0].action == ContextChangeAction.UNCHANGED


@pytest.mark.asyncio
async def test_async_project_converges_in_user_resources() -> None:
    client = FakeClient()
    project = AsyncContextArranger(
        client,
        "private-context",
        "user/alice/resources/ops",
    )
    declare_alerts(project)

    plan = await project.plan()
    assert plan.changes[0].action == ContextChangeAction.CREATE

    result = await project.apply(wait=True)
    assert len(result.applied) == 1
    node_uri = "viking://user/alice/resources/ops/alerts.md"
    assert node_uri in client.entries

    converged = await project.plan()
    assert converged.has_changes is False
    assert converged.changes[0].action == ContextChangeAction.UNCHANGED


@pytest.mark.asyncio
async def test_dry_run_and_prune_only_delete_owned_live_contexts() -> None:
    client = FakeClient()
    first = AsyncContextArranger(client, "demo", "resources/demo")
    declare_alerts(first)
    first.live_context(
        "weather.md",
        "agent/tools/weather/current.json",
        "Current weather",
    )
    await first.apply()
    client.entries["viking://resources/demo/manual.md"] = {
        "isDir": False,
        "tags": ["team=ops"],
    }
    client.calls.clear()

    desired = AsyncContextArranger(client, "demo", "resources/demo")
    declare_alerts(desired)
    plan = await desired.plan()
    deleted = [change.uri for change in plan.changes if change.action == ContextChangeAction.DELETE]
    assert deleted == ["viking://resources/demo/weather.md"]

    without_prune = await desired.apply()
    assert without_prune.prune is False
    assert "viking://resources/demo/weather.md" in client.entries
    assert all(change.action != ContextChangeAction.DELETE for change in without_prune.applied)

    dry_run = await desired.apply(prune=True, dry_run=True)

    assert dry_run.dry_run is True
    assert dry_run.prune is True
    dry_run_deletes = [
        change.uri for change in dry_run.plan.changes if change.action == ContextChangeAction.DELETE
    ]
    assert dry_run_deletes == ["viking://resources/demo/weather.md"]

    await desired.apply(prune=True, wait=True)
    assert "viking://resources/demo/weather.md" not in client.entries
    assert "viking://resources/demo/manual.md" in client.entries
    assert client.calls[-1][0] == "batch_unmount"
    assert client.calls[-1][2] is True


@pytest.mark.asyncio
async def test_prune_rejects_owned_node_with_unsupported_kind() -> None:
    client = FakeClient()
    uri = "viking://resources/demo/legacy.md"
    client.entries[uri] = {
        "isDir": False,
        "tags": ["ov-cac-owner=demo", "ov-cac-kind=document"],
    }
    project = AsyncContextArranger(client, "demo", "resources/demo")

    plan = await project.plan()

    assert plan.has_conflicts is True
    assert plan.changes[0].action == ContextChangeAction.CONFLICT
    with pytest.raises(FailedPreconditionError, match="contains conflicts"):
        await project.apply(prune=True)
    assert uri in client.entries


@pytest.mark.asyncio
async def test_project_rejects_unmanaged_target_collision() -> None:
    client = FakeClient()
    uri = "viking://resources/demo/alerts.md"
    client.entries[uri] = {"isDir": False, "tags": ["team=ops"]}
    project = AsyncContextArranger(client, "demo", "resources/demo")
    declare_alerts(project)

    plan = await project.plan()
    assert plan.has_conflicts is True
    assert plan.changes[0].action == ContextChangeAction.CONFLICT
    with pytest.raises(FailedPreconditionError, match="contains conflicts"):
        await project.apply()


@pytest.mark.asyncio
async def test_changed_live_context_declaration_produces_update() -> None:
    client = FakeClient()
    project = AsyncContextArranger(client, "demo", "resources/demo")
    declare_alerts(project)
    await project.apply()

    updated = AsyncContextArranger(client, "demo", "resources/demo")
    declare_alerts(updated, service="worker")
    plan = await updated.plan()

    assert plan.changes[0].action == ContextChangeAction.UPDATE
    await updated.apply()
    assert client.entries["viking://resources/demo/alerts.md"]["arguments"] == {"service": "worker"}


@pytest.mark.asyncio
async def test_plan_detects_live_mount_drift() -> None:
    client = FakeClient()
    project = AsyncContextArranger(client, "demo", "resources/demo")
    declare_alerts(project)
    await project.apply()
    client.entries["viking://resources/demo/alerts.md"]["arguments"] = {"service": "worker"}

    plan = await project.plan()

    assert plan.changes[0].action == ContextChangeAction.UPDATE


@pytest.mark.asyncio
async def test_new_live_context_mounts_with_ownership_tags() -> None:
    client = FakeClient()
    project = AsyncContextArranger(client, "demo", "resources/demo")
    declare_alerts(project)

    await project.apply()

    mount_call = client.calls[-1]
    assert mount_call[0] == "batch_mount"
    assert project.owner_tag in mount_call[1][0]["tags"]
    assert "ov-cac-kind=live_context" in mount_call[1][0]["tags"]


def test_sync_project_uses_the_same_declaration_and_plan_model() -> None:
    client = SyncHTTPClient(url="http://localhost:1933")
    backend = FakeClient()
    client._async_client = backend
    project = ContextArranger(client, "demo", "resources/demo")
    declare_alerts(project)

    result = project.apply(dry_run=True)

    assert result.plan.changes[0].kind == ContextResourceKind.LIVE_CONTEXT
    assert result.plan.changes[0].action == ContextChangeAction.CREATE
    assert backend.calls == []


def test_project_main_plans_and_applies_from_cli_arguments(capsys: Any) -> None:
    client = SyncHTTPClient(url="http://localhost:1933")
    backend = FakeClient()
    client._async_client = backend
    project = ContextArranger(client, "demo", "resources/demo")
    declare_alerts(project)

    assert project.main(["plan"]) == 0
    table_output = capsys.readouterr().out
    assert "OpenViking Context Plan" in table_output
    assert "Arrangement: demo" in table_output
    assert "1 create, 0 update, 0 delete" in table_output
    assert "ACTION" in table_output
    assert "viking://resources/demo/alerts.md" in table_output
    assert "target does not exist" in table_output

    assert project.main(["plan", "--output", "json"]) == 0
    plan_output = json.loads(capsys.readouterr().out)
    assert plan_output["project"] == "demo"
    assert "prune" not in plan_output
    assert plan_output["counts"]["create"] == 1

    assert project.main(["apply", "--dry-run", "--output", "json"]) == 0
    apply_output = json.loads(capsys.readouterr().out)
    assert apply_output["prune"] is False
    assert apply_output["dry_run"] is True
    assert apply_output["plan"]["counts"]["create"] == 1
    assert backend.calls == []

    assert project.main(["apply", "--wait"]) == 0
    apply_table = capsys.readouterr().out
    assert "OpenViking Context Apply Result" in apply_table
    assert "1 created, 0 updated, 0 pending deletion" in apply_table
    assert "RESULT" in apply_table
    assert "CREATED" in apply_table
    assert "created successfully" in apply_table
    assert "target does not exist" not in apply_table
    assert backend.calls[-1][2] is True

    empty = ContextArranger(client, "demo", "resources/demo")
    assert empty.main(["apply"]) == 0
    pending_table = capsys.readouterr().out
    assert "0 created, 0 updated, 1 pending deletion" in pending_table
    assert "PENDING DELETE" in pending_table
    assert "run apply --prune to delete" in pending_table
    assert "viking://resources/demo/alerts.md" in backend.entries

    assert empty.main(["apply", "--prune"]) == 0
    pruned_table = capsys.readouterr().out
    assert "0 created, 0 updated, 1 deleted" in pruned_table
    assert "DELETED" in pruned_table
    assert "viking://resources/demo/alerts.md" not in backend.entries


def test_project_main_provides_generated_help(capsys: Any) -> None:
    project = ContextArranger(FakeClient(), "demo", "resources/demo")

    with pytest.raises(SystemExit) as exc_info:
        project.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "plan" in output
    assert "apply" in output
    assert "resources/demo" in output

    with pytest.raises(SystemExit) as exc_info:
        project.main(["plan", "--help"])
    assert exc_info.value.code == 0
    assert "--prune" not in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc_info:
        project.main(["apply", "--help"])
    assert exc_info.value.code == 0
    apply_help = capsys.readouterr().out
    assert "--prune" in apply_help
    assert "-w, --wait" in apply_help
    assert "Wait until processing is complete." in apply_help


def test_declarations_validate_scope_tags_and_live_context_fields() -> None:
    project = ContextArranger(FakeClient(), "demo", "resources/demo")

    for root_uri in (
        "agent",
        "~/resources/demo",
        "user/alice/memories/demo",
        "user//resources/demo",
        "user/alice/resources/../demo",
    ):
        with pytest.raises(ValueError, match="within viking://resources"):
            ContextArranger(FakeClient(), "demo", root_uri)
    with pytest.raises(ValueError, match="below"):
        project.live_context(
            "viking://resources/other/live.md",
            "agent/tools/demo/current.json",
            "Current",
        )
    with pytest.raises(ValueError, match="reserved"):
        project.live_context(
            "live.md",
            "agent/tools/demo/current.json",
            "Current",
            tags=["ov-cac-owner=other"],
        )
    with pytest.raises(ValueError, match="must end with .md"):
        project.live_context(
            "live.txt",
            "agent/tools/demo/current.json",
            "Current",
        )
