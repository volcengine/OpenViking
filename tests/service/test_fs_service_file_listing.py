# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""File target compatibility belongs to the public filesystem service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSNotADirectoryError, AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


class _DummyAgfs:
    pass


def _default_ctx():
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


@pytest.fixture
def file_listing(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    uri = "viking://resources/docs/.notes.md"
    metadata = {
        "name": ".notes.md",
        "size": 1234,
        "mode": 0o644,
        "modTime": "2026-06-11T00:30:17+08:00",
        "isDir": False,
        "meta": {"Name": "localfs"},
    }
    reads = {
        method: AsyncMock(side_effect=AGFSNotADirectoryError(uri)) for method in ("ls", "tree")
    }
    for method, reader in reads.items():
        monkeypatch.setattr(fs, f"_{method}_original", reader)
    stat = AsyncMock(side_effect=lambda _path: dict(metadata))
    monkeypatch.setattr(fs._async_agfs, "stat", stat)
    return FSService(viking_fs=fs), uri, metadata, reads, stat


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
@pytest.mark.parametrize("output", ["original", "agent"])
@pytest.mark.parametrize("error_type", [AGFSNotADirectoryError, NotADirectoryError])
async def test_file_listing_preserves_output_shape(file_listing, method, output, error_type):
    fs, uri, metadata, reads, _stat = file_listing
    reads[method].side_effect = error_type(uri)

    entries = (await getattr(fs, method)(uri, output=output, ctx=_default_ctx())).entries

    expected = {**metadata, "uri": uri, "access": "manage"}
    if output == "agent":
        expected = {
            "uri": uri,
            "size": metadata["size"],
            "isDir": False,
            "modTime": "2026-06-10T16:30:17.000Z",
            "access": "manage",
            "abstract": "",
        }
    if method == "tree":
        expected["rel_path"] = metadata["name"]
    assert entries == [expected]
    assert reads[method].await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
@pytest.mark.parametrize("offset", [0, 1, 10])
async def test_file_listing_paginates_one_entry(file_listing, method, offset):
    fs, uri, _metadata, _reads, _stat = file_listing

    entries = (
        await getattr(fs, method)(uri, offset=offset, node_limit=1, ctx=_default_ctx())
    ).entries

    assert [entry["uri"] for entry in entries] == ([uri] if offset == 0 else [])


@pytest.mark.asyncio
async def test_file_listing_tree_directories_only_is_empty(file_listing):
    fs, uri, _metadata, _reads, _stat = file_listing

    assert (await fs.tree(uri, directories_only=True, ctx=_default_ctx())).entries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_extra_fields_are_opt_in(file_listing, monkeypatch, method):
    from openviking.storage.vector_ids import vector_record_id

    fs, uri, _metadata, _reads, _stat = file_listing
    count = AsyncMock()
    monkeypatch.setattr(fs._viking_fs, "_get_vector_store", lambda: SimpleNamespace(count=count))
    entries = (
        await getattr(fs, method)(uri, extra_fields=["id", "count"], ctx=_default_ctx())
    ).entries

    assert entries[0]["id"] == vector_record_id(_default_ctx().account_id, uri)
    assert "count" not in entries[0]
    count.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
@pytest.mark.parametrize("output", ["original", "agent"])
async def test_file_listing_summary_controls_do_not_read_parent(
    file_listing, monkeypatch, method, output
):
    service, uri, _metadata, _reads, _stat = file_listing
    summaries = AsyncMock()
    monkeypatch.setattr(service._viking_fs, "_read_directory_summary", summaries)

    page = await getattr(service, method)(
        uri,
        ctx=_default_ctx(),
        output=output,
        include_abstract=False,
        include_overview=True,
    )

    assert page.entries[0]["overview"] == ""
    assert "abstract" not in page.entries[0]
    summaries.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_retries_once_if_target_becomes_directory(file_listing, method):
    fs, uri, metadata, reads, stat = file_listing
    directory_entries = [{"uri": f"{uri}/child.md", "name": "child.md", "access": "manage"}]
    reads[method].side_effect = [AGFSNotADirectoryError(uri), directory_entries]
    stat.side_effect = None
    stat.return_value = {**metadata, "isDir": True}

    entries = (await getattr(fs, method)(uri, ctx=_default_ctx())).entries

    assert entries == directory_entries
    assert reads[method].await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_propagates_retry_failure(file_listing, method):
    fs, uri, metadata, reads, stat = file_listing
    error = AGFSNotADirectoryError("child changed type")
    reads[method].side_effect = [AGFSNotADirectoryError(uri), error]
    stat.side_effect = None
    stat.return_value = {**metadata, "isDir": True}

    with pytest.raises(AGFSNotADirectoryError) as caught:
        await getattr(fs, method)(uri, ctx=_default_ctx())

    assert caught.value is error
    assert reads[method].await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
@pytest.mark.parametrize("error", [RuntimeError("backend failed"), AGFSNotFoundError("missing")])
async def test_file_listing_does_not_fallback_on_other_errors(file_listing, method, error):
    fs, uri, _metadata, reads, stat = file_listing
    reads[method].side_effect = error

    with pytest.raises(type(error)) as caught:
        await getattr(fs, method)(uri, ctx=_default_ctx())

    assert caught.value is error
    stat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_preserves_missing_target_error(file_listing, method):
    fs, uri, _metadata, _reads, stat = file_listing
    stat.side_effect = AGFSNotFoundError(uri)

    with pytest.raises(NotFoundError):
        await getattr(fs, method)(uri, ctx=_default_ctx())


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_preserves_access_denial(file_listing, monkeypatch, method):
    fs, uri, _metadata, reads, stat = file_listing
    error = PermissionDeniedError("read permission required", resource=uri)
    monkeypatch.setattr(fs._viking_fs, "_ensure_traversable", AsyncMock(side_effect=error))

    with pytest.raises(PermissionDeniedError) as caught:
        await getattr(fs, method)(uri, ctx=_default_ctx())

    assert caught.value is error
    reads[method].assert_not_awaited()
    stat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_directory_success_skips_fallback(file_listing, method):
    fs, uri, _metadata, reads, stat = file_listing
    reads[method].side_effect = None
    entries = [{"uri": f"{uri}/child.md", "name": "child.md", "access": "manage"}]
    reads[method].return_value = entries

    assert (await getattr(fs, method)(uri, ctx=_default_ctx())).entries == entries
    stat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_handles_backend_empty_result(file_listing, method):
    fs, uri, _metadata, reads, _stat = file_listing
    reads[method].side_effect = None
    reads[method].return_value = []

    entries = (await getattr(fs, method)(uri, ctx=_default_ctx())).entries

    assert [entry["uri"] for entry in entries] == [uri]
    assert reads[method].await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_empty_directory_stays_empty(file_listing, method):
    fs, uri, metadata, reads, stat = file_listing
    reads[method].side_effect = None
    reads[method].return_value = []
    stat.side_effect = None
    stat.return_value = {**metadata, "isDir": True}

    assert (await getattr(fs, method)(uri, ctx=_default_ctx())).entries == []
    assert reads[method].await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["ls", "tree"])
async def test_file_listing_keeps_storage_internal_files_hidden(file_listing, method):
    fs, _uri, metadata, _reads, stat = file_listing
    uri = "viking://resources/docs/.path.ovlock"
    stat.side_effect = None
    stat.return_value = {**metadata, "name": ".path.ovlock"}

    assert (await getattr(fs, method)(uri, ctx=_default_ctx())).entries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method,recursive", [("ls", False), ("ls", True), ("tree", False)])
@pytest.mark.parametrize(
    "tags,offset,expected_count",
    [([], 0, 1), ([], 1, 0), (["env=prod"], 0, 1), (["env=dev"], 0, 0)],
)
async def test_file_listing_service_pagination_and_tags(
    file_listing, method, recursive, tags, offset, expected_count
):
    from openviking.service.fs_service import FSService

    fs, uri, _metadata, _reads, _stat = file_listing
    store = SimpleNamespace(
        filter=AsyncMock(return_value=[{"uri": uri, "level": 2, "search_tags": ["env=prod"]}])
    )
    service = FSService(viking_fs=fs._viking_fs, vikingdb=store)
    kwargs = {"recursive": recursive} if method == "ls" else {}

    page = await getattr(service, method)(
        uri, ctx=_default_ctx(), node_limit=1, offset=offset, tags=tags, **kwargs
    )

    assert len(page.entries) == expected_count
    assert page.has_more is False
    if page.entries:
        assert page.entries[0]["uri"] == uri
        assert page.entries[0].get("rel_path") == (
            ".notes.md" if method == "tree" or recursive else None
        )
        if tags:
            assert page.entries[0]["tags"] == ["env=prod"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recursive", [False, True])
async def test_file_listing_service_simple_returns_uri(file_listing, recursive):

    fs, uri, _metadata, _reads, _stat = file_listing
    page = await fs.ls(uri, ctx=_default_ctx(), simple=True, recursive=recursive)

    assert page.entries == [uri]
    assert page.has_more is False
