from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.resource.dingtalk_import import (
    DINGTALK_SYNC_SIDECAR,
    persist_sync_sidecar,
    prepare_dingtalk_artifact,
)
from openviking.storage.context_update_plan import (
    ContentTreeOperation,
    build_context_update_plan_from_snapshot,
)
from openviking.storage.resource_diff import (
    build_rnfv_snapshot,
    prepare_artifact_inventory,
)
from openviking.storage.resource_rnfv import RequestIntent
from openviking_cli.exceptions import InvalidArgumentError


class _MemoryVikingFS:
    """Small VikingFS implementation for formal DingTalk resource trees."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any] | bytes] = {}

    async def exists(self, uri, ctx=None):
        return uri.rstrip("/") in self.nodes

    async def mkdir(self, uri, exist_ok=False, ctx=None, lease_ref=None):
        parts = uri.rstrip("/").split("/")
        for index in range(3, len(parts) + 1):
            self.nodes.setdefault("/".join(parts[:index]), {})

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        await self.mkdir(uri.rsplit("/", 1)[0], exist_ok=True)
        self.nodes[uri] = content.encode() if isinstance(content, str) else content

    async def read_file(self, uri, ctx=None):
        return (await self.read_file_bytes(uri, ctx=ctx)).decode()

    async def read_file_bytes(self, uri, ctx=None):
        value = self.nodes[uri]
        assert isinstance(value, bytes)
        return value

    async def stat(self, uri, ctx=None, skip_count=False):
        value = self.nodes[uri]
        return {
            "isDir": isinstance(value, dict),
            "size": 0 if isinstance(value, dict) else len(value),
        }

    async def tree(self, uri, **kwargs):
        prefix = uri.rstrip("/") + "/"
        return [
            {
                "rel_path": candidate[len(prefix) :],
                "isDir": isinstance(value, dict),
                "uri": candidate,
            }
            for candidate, value in sorted(self.nodes.items())
            if candidate.startswith(prefix)
        ]


def _manifest(*entries: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"node_id": node_id, "relative_path": path, "type": "document", "title": node_id}
        for node_id, path in entries
    ]


async def _artifact(tmp_path, files: dict[str, str | bytes]):
    store = LocalParseOutputStore(str(tmp_path / "parse-output"))
    ref = await store.create_artifact()
    for path, content in files.items():
        data = content.encode() if isinstance(content, str) else content
        await store.write_bytes(ref, f"dingtalk_source/{path}", data)
    return store, ref


async def _seed_sidecar(fs, target: str, *, manifest, **extra):
    payload = {"manifest": manifest, "retained_paths": [], "missing_nodes": [], **extra}
    await fs.write_file(f"{target}/{DINGTALK_SYNC_SIDECAR}", json.dumps(payload))
    return payload


async def _prepare(fs, store, ref, target, meta):
    return await prepare_dingtalk_artifact(
        fs,
        store=store,
        artifact_ref=ref,
        doc_rel="dingtalk_source",
        target_uri=target,
        meta=meta,
        ctx=None,
    )


@pytest.mark.asyncio
async def test_refresh_updates_existing_adds_new_and_retains_missing_node(tmp_path):
    fs = _MemoryVikingFS()
    target = "viking://resources/dingtalk_source"
    await fs.write_file(f"{target}/A/old.md", "old A")
    await fs.write_file(f"{target}/B/content.md", "old B")
    await _seed_sidecar(fs, target, manifest=_manifest(("A", "A"), ("B", "B")))
    store, ref = await _artifact(
        tmp_path,
        {"A/new.md": "new A", "C/content.md": "new C"},
    )

    sidecar, warnings = await _prepare(
        fs,
        store,
        ref,
        target,
        {"dingtalk_manifest": _manifest(("A", "A"), ("C", "C"))},
    )

    assert warnings == [
        "DingTalk sync retained 1 source node(s) missing from this refresh; "
        "they may have been deleted, moved, or become inaccessible."
    ]
    assert await store.read_text(ref, "dingtalk_source/B/content.md") == "old B"
    assert await store.read_text(ref, "dingtalk_source/A/new.md") == "new A"
    assert json.loads(sidecar)["missing_nodes"] == ["B"]

    # Run the canonical snapshot and planner used by ResourceProcessor.
    inventory = await prepare_artifact_inventory(
        store, ref, doc_rel="dingtalk_source", target_root_uri=target
    )
    snapshot = await build_rnfv_snapshot(
        viking_fs=fs,
        vikingdb=AsyncMock(),
        store=store,
        artifact_ref=ref,
        target_uri=target,
        ctx=None,
        doc_rel="dingtalk_source",
        request_intent=RequestIntent(target, "vectors_only", vectorize=False),
        artifact_inventory=inventory,
    )
    _, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=store,
        artifact_ref=ref,
        target=AsyncMock(),
        vikingdb=AsyncMock(),
        context_type="resource",
        is_code_repo=False,
        account_id="acct",
        ctx=None,
        root_preexisting=True,
        artifact_paths=inventory.artifact_paths,
    )
    deleted = {
        action.relative_path
        for action in plan.content_tree_actions
        if action.operation is ContentTreeOperation.DELETE
    }
    assert "B/content.md" not in deleted
    assert "A/old.md" in deleted
    assert {"A/new.md", "C/content.md"} <= {
        action.relative_path for action in plan.content_tree_actions
    }


@pytest.mark.asyncio
async def test_missing_node_retains_only_its_own_shared_assets(tmp_path):
    fs = _MemoryVikingFS()
    target = "viking://resources/dingtalk_source"
    await fs.write_file(f"{target}/A/content.md", "old A")
    await fs.write_file(f"{target}/B/content.md", "old B")
    await fs.write_file(f"{target}/assets/a-old.png", b"old A image")
    await fs.write_file(f"{target}/assets/b.png", b"B image")
    old = _manifest(("A", "A"), ("B", "B"))
    old[0]["source_paths"] = ["A/content.md", "assets/a-old.png"]
    old[1]["source_paths"] = ["B/content.md", "assets/b.png"]
    await _seed_sidecar(fs, target, manifest=old)
    store, ref = await _artifact(
        tmp_path,
        {"A/content.md": "new A", "assets/a-new.png": b"new A image"},
    )
    current = _manifest(("A", "A"))
    current[0]["source_paths"] = ["A/content.md", "assets/a-new.png"]

    await _prepare(fs, store, ref, target, {"dingtalk_manifest": current})

    assert await store.read_text(ref, "dingtalk_source/B/content.md") == "old B"
    assert await store.read_bytes(ref, "dingtalk_source/assets/b.png") == b"B image"
    with pytest.raises(FileNotFoundError):
        await store.read_bytes(ref, "dingtalk_source/assets/a-old.png")


@pytest.mark.asyncio
async def test_reused_and_failed_nodes_copy_previous_output_without_overwriting_new_files(tmp_path):
    fs = _MemoryVikingFS()
    target = "viking://resources/dingtalk_source"
    await fs.write_file(f"{target}/A/body.md", "old A")
    await fs.write_file(f"{target}/A/extra.md", "old extra")
    await fs.write_file(f"{target}/B/body.md", "old B")
    await fs.write_file(f"{target}/.abstract.md", "control")
    old = _manifest(("A", "A"), ("B", "B"))
    old[0]["source_paths"] = ["A/body.md", "A/extra.md"]
    old[1]["source_paths"] = ["B/body.md"]
    await _seed_sidecar(fs, target, manifest=old)
    store, ref = await _artifact(tmp_path, {"A/body.md": "fresh A"})
    current = _manifest(("A", "A"), ("B", "B"))
    current[0]["reused"] = True

    sidecar, _ = await _prepare(
        fs,
        store,
        ref,
        target,
        {
            "dingtalk_manifest": current,
            "failed_files": [{"path": "B/body.md", "error": "parser error"}],
        },
    )

    assert await store.read_text(ref, "dingtalk_source/A/body.md") == "fresh A"
    assert await store.read_text(ref, "dingtalk_source/A/extra.md") == "old extra"
    assert await store.read_text(ref, "dingtalk_source/B/body.md") == "old B"
    assert ".abstract.md" not in {entry.name for entry in await store.list(ref, "dingtalk_source")}
    retained = set(json.loads(sidecar)["retained_paths"])
    assert {"A/body.md", "A/extra.md", "B/body.md"} <= retained


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_state",
    [
        {"complete": False, "processing_key": "config-new"},
        {"complete": True, "processing_key": "config-old"},
    ],
    ids=["incomplete-sidecar", "stale-processing-key"],
)
async def test_unreadable_node_retains_old_output_without_incremental_reuse_state(
    tmp_path,
    old_state,
):
    fs = _MemoryVikingFS()
    target = "viking://resources/dingtalk_source"
    await fs.write_file(f"{target}/Source123/body.md", "last readable content")
    old_manifest = _manifest(("Source123", "Source123"))
    old_manifest[0]["source_paths"] = ["Source123/body.md"]
    await _seed_sidecar(fs, target, manifest=old_manifest, **old_state)
    store, ref = await _artifact(tmp_path, {})
    current = _manifest(("Source123", "Source123"))
    current[0].update(
        {
            "source_paths": ["Source123/body.md"],
            "unreadable": True,
            "reused": False,
        }
    )

    sidecar, _ = await _prepare(
        fs,
        store,
        ref,
        target,
        {
            "dingtalk_manifest": current,
            "dingtalk_processing_key": "config-new",
        },
    )

    assert (
        await store.read_text(ref, "dingtalk_source/Source123/body.md") == "last readable content"
    )
    assert "Source123/body.md" in json.loads(sidecar)["retained_paths"]


@pytest.mark.asyncio
async def test_corrupt_stored_metadata_aborts_without_changing_artifact(tmp_path):
    fs = _MemoryVikingFS()
    target = "viking://resources/dingtalk_source"
    await fs.write_file(f"{target}/{DINGTALK_SYNC_SIDECAR}", "not-json")
    store, ref = await _artifact(tmp_path, {"A/body.md": "new A"})

    with pytest.raises(InvalidArgumentError, match="refusing to replace"):
        await _prepare(fs, store, ref, target, {"dingtalk_manifest": _manifest(("A", "A"))})

    assert await store.read_text(ref, "dingtalk_source/A/body.md") == "new A"


@pytest.mark.asyncio
async def test_previous_digest_mismatch_aborts_without_changing_artifact(tmp_path):
    fs = _MemoryVikingFS()
    target = "viking://resources/dingtalk_source"
    await fs.write_file(f"{target}/A/body.md", "old A")
    await _seed_sidecar(fs, target, manifest=_manifest(("A", "A")))
    store, ref = await _artifact(tmp_path, {"A/body.md": "new A"})

    with pytest.raises(InvalidArgumentError, match="target changed"):
        await _prepare(
            fs,
            store,
            ref,
            target,
            {
                "dingtalk_manifest": _manifest(("A", "A")),
                "dingtalk_previous_digest": "0" * 64,
            },
        )

    assert await store.read_text(ref, "dingtalk_source/A/body.md") == "new A"


@pytest.mark.asyncio
async def test_persist_sync_sidecar_writes_planned_state():
    fs = _MemoryVikingFS()
    target = "viking://resources/dingtalk_source"
    content = json.dumps({"manifest": _manifest(("Source123", "Source123"))})

    await persist_sync_sidecar(
        fs, target_uri=target, content=content, ctx=None, lease_ref={"lease_id": "1"}
    )

    assert await fs.read_file(f"{target}/{DINGTALK_SYNC_SIDECAR}") == content
