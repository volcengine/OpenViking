# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Item identity, resource lifecycle, and retrieval regressions."""

import math
import shutil
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclManager
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
from openviking_cli.session.user_id import UserIdentifier


def ctx(user="owner", role=Role.ROOT):
    return RequestContext(user=UserIdentifier.the_default_user(user), role=role)


async def stored_files(fs, root, owner):
    stat = await fs.stat(root, ctx=owner, skip_count=True)
    if not stat.get("isDir"):
        return [root]
    entries = await fs.tree(
        root, ctx=owner, show_all_hidden=True, node_limit=None, level_limit=None
    )
    return [entry["uri"] for entry in entries if not entry.get("isDir")]


@pytest.fixture
def local_adapter(tmp_path):
    adapter = LocalCollectionAdapter("context", str(tmp_path / "vectors"), "default")
    adapter.create_collection(
        "context",
        CollectionSchemas.context_collection("context", 4),
        distance="cosine",
        sparse_weight=0,
        index_name="default",
    )
    yield adapter
    adapter.close()


def declare(adapter, uri, uuid, attrs=None):
    adapter.catalog_execute(
        {"op": "upsert_nodes", "scope": "a", "nodes": [{"uri": uri, "node_type": "dir"}]}
    )
    adapter.catalog_execute(
        {
            "op": "declare_item",
            "scope": "a",
            "uri": uri,
            "item_uuid": uuid,
            "item_attrs": attrs or [],
        }
    )


def test_limit_does_not_change_top_item(local_adapter):
    adapter = local_adapter
    declare(adapter, "viking://resources/A", "uuid-a")
    declare(adapter, "viking://resources/B", "uuid-b")
    records = []
    for name, count, score in [("A", 60, 0.9), ("B", 80, 0.8)]:
        for i in range(count):
            records.append(
                {
                    "id": f"{name}-{i}",
                    "account_id": "a",
                    "uri": f"viking://resources/{name}/{i}.md",
                    "level": 2,
                    "vector": [score, math.sqrt(1 - score * score), 0, 0],
                }
            )
    adapter.upsert(records)
    results = {}
    for n in [1, 10]:
        result = adapter.search_item_groups(
            query_vector=[1, 0, 0, 0], filter=Eq("account_id", "a"), max_groups=n, aggregation="sum"
        )
        results[n] = [
            {"id": g["entity_id"], "score": g["entity_score"], "count": g["count"]}
            for g in result["groups"]
        ]
    assert results[1][0]["id"] == results[10][0]["id"]


class EnabledAcl:
    async def get_account(self, account_id, field):
        return SimpleNamespace(enabled=True)


@pytest.mark.asyncio
async def test_item_root_reference_does_not_authorize_root_content(service):
    fs = service.viking_fs
    backend = fs._get_vector_store()
    owner, reader = ctx("owner", Role.ADMIN), ctx("reader", Role.USER)
    root, leaf = "viking://resources/private-item", "viking://resources/private-item/public.md"
    await service.fs.mkdir(
        root, owner, as_item=True, item_attrs={"private_source": "owner-only-metadata"}
    )
    dim = backend.vector_dim
    await fs.write_file(leaf, "public text", ctx=owner)
    await backend._upsert_many_raw(
        [
            {
                "id": "root-private",
                "account_id": owner.account_id,
                "uri": root,
                "context_type": "resource",
                "level": 0,
                "vector": [0.1] * dim,
                "acl_mode": "restricted",
                "acl_direct_grants": ["7:user:owner"],
                "acl_inherited_grants": [],
            },
            {
                "id": "leaf-public",
                "account_id": owner.account_id,
                "uri": leaf,
                "context_type": "resource",
                "level": 2,
                "vector": [0.1] * dim,
                "acl_mode": "restricted",
                "acl_direct_grants": ["1:user:reader"],
                "acl_inherited_grants": [],
            },
        ],
        ctx=owner,
    )
    acl = AclManager(backend, EnabledAcl())
    fs.acl_manager = backend.acl_manager = acl
    access = await fs._can_access_many([root, leaf], reader)
    assert access[root] is False and access[leaf] is True, access
    result = await service.search.find(
        "public", ctx=reader, target_uri="viking://resources", level=[2], search_as_item={}
    )
    assert result.total == 1
    assert result.resources[0].uri == root
    from openviking_cli.exceptions import PermissionDeniedError

    with pytest.raises(PermissionDeniedError):
        await fs.overview(root, ctx=reader)


@pytest.mark.asyncio
async def test_rejected_attribute_change_preserves_existing_content(service, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    doc = source / "keep.md"
    doc.write_text("# Original\nold content")
    root = "viking://resources/reimport-attrs"
    owner = ctx()
    first = await service.resources.add_resource(
        str(source), to=root, ctx=owner, wait=True, as_item=True, item_attrs={"asset_id": "V1"}
    )
    assert first.get("root_uri") == root, first
    file_uris = await stored_files(service.viking_fs, root, owner)
    content_uri = next(uri for uri in file_uris if uri.endswith("/keep.md"))
    before = await service.viking_fs.read_file(content_uri, ctx=owner)
    doc.write_text("# Rejected\nnew content from invalid request")
    result = await service.resources.add_resource(
        str(source), to=root, ctx=owner, wait=True, as_item=True, item_attrs={"asset_id": "V2"}
    )
    assert result["status"] == "error", result
    after = await service.viking_fs.read_file(content_uri, ctx=owner)
    assert after == before


@pytest.mark.asyncio
async def test_parent_reimport_does_not_delete_independent_child_item(service, tmp_path):
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "keep.md").write_text("# Parent\nkeep")
    (source / "nested" / "child.md").write_text("# Child\nindependent child")
    root = "viking://resources/parent-import"
    child = root + "/independent"
    owner = ctx()
    await service.resources.add_resource(str(source), to=root, ctx=owner, wait=True, as_item=True)
    child_declaration = await service.fs.mkdir(
        child, owner, as_item=True, item_attrs={"asset_id": "child"}
    )
    await service.viking_fs.write_file(child + "/child.md", "Independent child", ctx=owner)
    child_files = await stored_files(service.viking_fs, child, owner)
    child_content_uri = next(uri for uri in child_files if uri.endswith("/child.md"))
    assert await service.viking_fs.exists(child_content_uri, ctx=owner)
    shutil.rmtree(source / "nested")
    result = await service.resources.add_resource(
        str(source), to=root, ctx=owner, wait=True, as_item=True
    )
    assert result["status"] == "error", result
    exists = await service.viking_fs.exists(child_content_uri, ctx=owner)
    resolved = await service.viking_fs._get_vector_store().catalog_execute(
        {"op": "resolve", "uris": [child]}, ctx=owner
    )
    assert resolved["data"][0]["item_uuid"] == child_declaration["item_uuid"]
    assert exists, "Parent reimport deleted content of an independent child Item"


@pytest.mark.asyncio
async def test_add_skill_establishes_item_declaration(client, service):
    md = "---\nname: review-probe\ndescription: Review probe skill\n---\n# Review\nUse this skill for the review probe.\n"
    response = await client.post("/api/v1/skills", json={"data": md, "wait": True})
    assert response.status_code == 200, response.text
    uri = response.json()["result"]["uri"]
    ordinary = await client.post(
        "/api/v1/search/find", json={"query": "Review probe", "context_type": "skill"}
    )
    item_mode = await client.post(
        "/api/v1/search/find",
        json={"query": "Review probe", "context_type": "skill", "search_as_item": {}},
    )
    assert ordinary.status_code == item_mode.status_code == 200
    resolved = await service.viking_fs._get_vector_store().catalog_execute(
        {"op": "resolve", "uris": [uri]}, ctx=ctx()
    )
    assert resolved["data"][0].get("item_uuid"), "add_skill succeeded without declaring an Item"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_skill_replacement_preserves_item_identity_or_legacy_absence(client, service, legacy):
    name = "skill-identity-legacy" if legacy else "skill-identity-item"
    content = f"---\nname: {name}\ndescription: Identity test\n---\n# Original\n"
    added = await client.post("/api/v1/skills", json={"data": content, "wait": True})
    assert added.status_code == 200, added.text
    root = added.json()["result"]["uri"]
    storage = service.viking_fs._get_vector_store()
    before = await storage.catalog_execute({"op": "resolve", "uris": [root]}, ctx=ctx())
    uuid = before["data"][0]["item_uuid"]
    if legacy:
        # Simulate an installed package from before Item declarations existed.
        await storage.catalog_execute({"op": "delete_subtree", "uri": root}, ctx=ctx())
    updated = await client.put(
        f"/api/v1/skills/{name}",
        json={"data": content.replace("# Original", "# Replacement"), "wait": True},
    )
    assert updated.status_code == 200, updated.text
    after = await storage.catalog_execute({"op": "resolve", "uris": [root]}, ctx=ctx())
    assert after["data"][0].get("item_uuid") == (None if legacy else uuid)


@pytest.mark.asyncio
async def test_new_skill_declaration_failure_is_retryable(client, service, monkeypatch):
    storage = service.viking_fs._get_vector_store()
    execute = storage.catalog_execute
    roots = []

    async def fail_first_declaration(request, *, ctx):
        if request["op"] == "declare_item" and not roots:
            roots.append(request["uri"])
            raise OSError("synthetic declaration failure")
        return await execute(request, ctx=ctx)

    monkeypatch.setattr(storage, "catalog_execute", fail_first_declaration)
    md = "---\nname: declaration-retry\ndescription: Retry declaration\n---\n# Retry\n"
    failed = await client.post("/api/v1/skills", json={"data": md, "wait": True})
    assert failed.status_code >= 400, failed.text
    assert roots
    assert not await service.viking_fs.exists(roots[0] + "/SKILL.md", ctx=ctx())
    retried = await client.post("/api/v1/skills", json={"data": md, "wait": True})
    assert retried.status_code == 200, retried.text
    resolved = await storage.catalog_execute({"op": "resolve", "uris": roots}, ctx=ctx())
    assert resolved["data"][0]["item_uuid"]


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_mkdir_rejects_promoting_existing_ordinary_directory(service, nested):
    from openviking_cli.exceptions import FailedPreconditionError

    owner = ctx()
    parent = "viking://resources/no-item-promotion"
    await service.fs.mkdir(parent, owner, as_item=nested)
    root = parent + "/ordinary" if nested else parent
    if nested:
        await service.fs.mkdir(root, owner)
    with pytest.raises(FailedPreconditionError, match="cannot be promoted"):
        await service.fs.mkdir(root, owner, as_item=True)
    resolved = await service.viking_fs._get_vector_store().catalog_execute(
        {"op": "resolve", "uris": [root]}, ctx=owner
    )
    assert resolved["data"][0].get("item_root_uri") != root.removeprefix("viking:/")


@pytest.mark.asyncio
async def test_add_resource_rejects_promoting_existing_resource_before_commit(service, tmp_path):
    source = tmp_path / "legacy.md"
    source.write_text("# Legacy\noriginal body")
    owner = ctx()
    root = "viking://resources/no-resource-promotion"
    initial = await service.resources.add_resource(str(source), to=root, ctx=owner, wait=True)
    assert initial["status"] == "success", initial
    before = {
        uri: await service.viking_fs.read_file_bytes(uri, ctx=owner)
        for uri in await stored_files(service.viking_fs, root, owner)
    }
    source.write_text("# Replacement\nrejected body")
    result = await service.resources.add_resource(
        str(source), to=root, ctx=owner, wait=True, as_item=True
    )
    assert result["status"] == "error", result
    assert result["code"] == "FAILED_PRECONDITION"
    for uri, data in before.items():
        assert await service.viking_fs.read_file_bytes(uri, ctx=owner) == data
    resolved = await service.viking_fs._get_vector_store().catalog_execute(
        {"op": "resolve", "uris": [root]}, ctx=owner
    )
    assert not resolved["data"][0].get("item_uuid")


def test_filter_only_respects_bounded_candidate_evidence(local_adapter):
    adapter = local_adapter
    root = "viking://resources/one-large-item"
    declare(adapter, root, "uuid-large", ["kind=asset"])
    adapter.upsert(
        [
            {
                "id": f"member-{i}",
                "account_id": "a",
                "uri": f"{root}/{i}.md",
                "level": 2,
                "vector": [1, 0, 0, 0],
            }
            for i in range(2000)
        ]
    )
    result = adapter.search_item_groups(
        filter=And([Eq("account_id", "a"), Eq("item_attrs", "kind=asset")]),
        max_groups=1,
        aggregation="max",
        filter_only=True,
    )
    group = result["groups"][0]
    assert len(group["candidate_members_uri"]) <= 1000
    assert group["candidate_members_count"] == 2000
    assert group["candidate_members_truncated"] is True


def test_union_filters_members_then_returns_all_ancestor_items(local_adapter):
    adapter = local_adapter
    root = "viking://resources/A"
    declare(adapter, root, "uuid-a", ["project=P1"])
    declare(adapter, root + "/B", "uuid-b", ["asset=V1"])
    adapter.upsert(
        [
            {
                "id": "a",
                "account_id": "a",
                "uri": root + "/a.md",
                "level": 2,
                "vector": [1, 0, 0, 0],
            },
            {
                "id": "b",
                "account_id": "a",
                "uri": root + "/B/b.md",
                "level": 2,
                "vector": [1, 0, 0, 0],
            },
        ]
    )
    adapter.catalog_execute({"op": "configure", "scope": "a", "item_identity_mode": "union"})
    result = adapter.search_item_groups(
        filter=And([Eq("account_id", "a"), Eq("item_attrs", "asset=V1")]),
        max_groups=10,
        aggregation="sum",
        filter_only=True,
    )
    groups = result["groups"]
    assert [(g["entity_id"], g["entity_attrs"]) for g in groups] == [
        ("uuid-a", ["project=P1"]),
        ("uuid-b", ["asset=V1"]),
    ]
    assert all(g["candidate_members_uri"] == [root + "/B/b.md"] for g in groups)
    parent_filter = adapter.search_item_groups(
        filter=And([Eq("account_id", "a"), Eq("item_attrs", "project=P1")]),
        max_groups=10,
        filter_only=True,
    )
    assert [g["candidate_members_count"] for g in parent_filter["groups"]] == [2, 1]


@pytest.mark.asyncio
async def test_inherited_item_import_reuses_source_cleanup(service, tmp_path):
    from openviking.parse.accessors.base import LocalResource, SourceType

    source = tmp_path / "temporary-source.md"
    original = "# Temporary source\n\n" + "\n\n".join(
        f"## Section {i}\n\n" + (f"Source paragraph {i}. " * 150) for i in range(8)
    )
    source.write_text(original)
    owner = ctx()
    parent = "viking://resources/inherited-original"
    await service.fs.mkdir(parent, owner, as_item=True)
    prepared = LocalResource(
        source, SourceType.HTTP, "https://example.com/source.md", is_temporary=True
    )
    root = parent + "/member"
    result = await service.resources._resource_processor.process_resource(
        str(source), to=root, ctx=owner, prepared_resource=prepared
    )
    assert result["status"] == "success", result
    files = await stored_files(service.viking_fs, root, owner)
    assert files
    assert not source.exists(), "The existing parser lifecycle must clean temporary inputs"
    assert not any("/.original/" in uri for uri in files)
    resolved = await service.viking_fs._get_vector_store().catalog_execute(
        {"op": "resolve", "uris": [root]}, ctx=owner
    )
    assert resolved["data"][0]["item_root_uri"] == "/resources/inherited-original"


@pytest.mark.asyncio
async def test_item_reimport_replaces_resource_snapshot_and_preserves_uuid(service, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "old.md"
    first.write_text(
        "# Old\n\n" + "\n\n".join(f"## Part {i}\n\n" + ("Old source. " * 150) for i in range(6))
    )
    owner = ctx()
    root = "viking://resources/original-snapshot"
    result = await service.resources.add_resource(
        str(source), to=root, ctx=owner, wait=True, as_item=True
    )
    assert result["status"] == "success", result
    storage = service.viking_fs._get_vector_store()
    uuid = (await storage.catalog_execute({"op": "resolve", "uris": [root]}, ctx=owner))["data"][0][
        "item_uuid"
    ]
    assert not await service.viking_fs.exists(root + "/.original", ctx=owner)
    first.unlink()
    (source / "new.md").write_text("# New\nreplacement source")
    result = await service.resources.add_resource(str(source), to=root, ctx=owner, wait=True)
    assert result["status"] == "success", result
    assert not await service.viking_fs.exists(root + "/.original", ctx=owner)
    assert (await storage.catalog_execute({"op": "resolve", "uris": [root]}, ctx=owner))["data"][0][
        "item_uuid"
    ] == uuid
    assert not any("/old/" in uri for uri in await stored_files(service.viking_fs, root, owner))


@pytest.mark.asyncio
@pytest.mark.parametrize("occupant", ["file", "directory", "marker-directory"])
async def test_item_import_does_not_reserve_original_paths(
    service, tmp_path, occupant, monkeypatch
):
    from unittest.mock import AsyncMock

    from openviking.storage.catalog import delete_catalog_subtree

    fs, owner = service.viking_fs, ctx()
    root = "viking://resources/original-target-conflict"
    await service.fs.mkdir(root, owner, as_item=True)
    original_uri = root + "/.original"
    if occupant != "file":
        await fs.mkdir(original_uri, ctx=owner)
        if occupant == "marker-directory":
            await fs.mkdir(original_uri + "/.ov-originals", ctx=owner)
        original_uri += "/legacy.bin"
    await fs.write_file_bytes(original_uri, b"Legacy original must remain unchanged.", ctx=owner)
    backend = fs._get_vector_store()
    identity = await backend.catalog_execute({"op": "resolve", "uris": [root]}, ctx=owner)
    source = tmp_path / "split.md"
    source.write_text(
        "# Input\n\n"
        + "\n\n".join(f"## Part {i}\n\n" + ("New source bytes. " * 200) for i in range(4))
    )
    cleanup = AsyncMock(wraps=delete_catalog_subtree)
    monkeypatch.setattr("openviking.storage.catalog.delete_catalog_subtree", cleanup)

    result = await service.resources.add_resource(
        str(source),
        to=root,
        ctx=owner,
        wait=True,
        as_item=True,
        build_index=False,
        summarize=False,
    )
    assert result["status"] == "success", result
    assert not await fs.exists(root + "/.original", ctx=owner)
    cleanup.assert_awaited_once()
    current = await backend.catalog_execute({"op": "resolve", "uris": [root]}, ctx=owner)
    assert current["data"][0]["item_uuid"] == identity["data"][0]["item_uuid"]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_is_directory", [False, True])
@pytest.mark.parametrize("fail_first_delete", [False, True])
async def test_item_reimport_reconciles_file_directory_replacements(
    service, tmp_path, monkeypatch, first_is_directory, fail_first_delete
):
    source = tmp_path / "source"
    source.mkdir()
    entry = source / "swap.py"

    def write_entry(directory):
        if directory:
            entry.mkdir()
            (entry / "inner.py").write_text('print("nested")\n')
        else:
            entry.write_text('print("flat")\n')

    write_entry(first_is_directory)
    owner = ctx()
    root = "viking://resources/reimport-kind"
    result = await service.resources.add_resource(
        str(source), to=root, ctx=owner, as_item=True, wait=True, summarize=False
    )
    assert result["status"] == "success", result
    storage = service.viking_fs._get_vector_store()
    original = (await storage.catalog_execute({"op": "resolve", "uris": [root]}, ctx=owner))[
        "data"
    ][0]["item_uuid"]
    if first_is_directory:
        shutil.rmtree(entry)
    else:
        entry.unlink()
    write_entry(not first_is_directory)
    requests = []
    execute = storage.catalog_execute

    async def track_catalog(request, **kwargs):
        if request.get("preserve_item_root"):
            requests.append(dict(request))
        return await execute(request, **kwargs)

    monkeypatch.setattr(storage, "catalog_execute", track_catalog)
    if fail_first_delete:
        remove = service.viking_fs.remove_files
        failed = False

        async def fail_once(uri, *args, **kwargs):
            nonlocal failed
            if not failed and uri.startswith(root + "/"):
                failed = True
                raise OSError("transient content delete failure")
            return await remove(uri, *args, **kwargs)

        monkeypatch.setattr(service.viking_fs, "remove_files", fail_once)
        result = await service.resources.add_resource(
            str(source), to=root, ctx=owner, wait=True, summarize=False
        )
        assert result["status"] == "error", result
    result = await service.resources.add_resource(
        str(source), to=root, ctx=owner, wait=True, summarize=False
    )
    assert result["status"] == "success", result
    resolved = await storage.catalog_execute({"op": "resolve", "uris": [root]}, ctx=owner)
    assert resolved["data"][0]["item_uuid"] == original
    assert len(requests) == (2 if fail_first_delete else 1)
    stat = await service.viking_fs.stat(root + "/swap.py", ctx=owner, skip_count=True)
    assert stat["isDir"] is (not first_is_directory)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_has_original", [False, True])
async def test_inherited_import_needs_no_source_preservation_lookup(
    service, tmp_path, monkeypatch, source_has_original
):
    owner = ctx()
    parent = "viking://resources/ownership-transition"
    await service.fs.mkdir(parent, owner, as_item=True)
    source = tmp_path / ("source.png" if source_has_original else "source.md")
    if source_has_original:
        from PIL import Image

        Image.new("RGB", (10, 10), color="white").save(source)
    else:
        source.write_text("# Source\nbody")
    storage = service.viking_fs._get_vector_store()
    execute = storage.catalog_execute

    async def no_source_ownership_lookup(request, *, ctx):
        assert request["op"] != "resolve", "Import must not probe ownership to retain source bytes"
        return await execute(request, ctx=ctx)

    monkeypatch.setattr(storage, "catalog_execute", no_source_ownership_lookup)
    result = await service.resources.add_resource(
        str(source),
        to=parent + "/child",
        ctx=owner,
        wait=True,
        summarize=False,
        build_index=False,
    )
    assert result["status"] == "success", result
    files = await stored_files(service.viking_fs, parent + "/child", owner)
    assert not any("/.original/" in uri for uri in files)
    if source_has_original:
        saved = [await service.viking_fs.read_file_bytes(uri, ctx=owner) for uri in files]
        assert source.read_bytes() in saved


def test_noop_node_updates_do_not_invalidate_every_item_recall(local_adapter, monkeypatch):
    adapter = local_adapter
    root = "viking://resources/read-item"
    declare(adapter, root, "uuid-read")
    declare(adapter, "viking://resources/unrelated", "uuid-unrelated")
    adapter.upsert(
        [
            {
                "id": "hit",
                "account_id": "a",
                "uri": root + "/hit.md",
                "level": 2,
                "vector": [1, 0, 0, 0],
            }
        ]
    )
    collection = adapter.get_collection()._Collection__collection
    original_search = collection.search_by_vector
    recalls = []

    def search_then_noop_upsert(*args, **kwargs):
        recalled = original_search(*args, **kwargs)
        before = recalled.catalog_revision
        after = collection.catalog_execute(
            {
                "op": "upsert_nodes",
                "scope": "a",
                "nodes": [{"uri": "/resources/unrelated", "node_type": "dir"}],
            }
        )["revision"]
        recalls.append({"before": before, "after": after})
        return recalled

    monkeypatch.setattr(collection, "search_by_vector", search_then_noop_upsert)
    result = adapter.search_item_groups(
        query_vector=[1, 0, 0, 0], filter=Eq("account_id", "a"), max_groups=1
    )
    assert result["groups"][0]["entity_id"] == "uuid-read"
    assert len(recalls) == 1
    assert len(recalls) == 1
