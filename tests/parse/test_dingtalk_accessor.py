import hashlib
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from openviking.parse.accessors.base import SourceType
from openviking.parse.accessors.dingtalk_accessor import (
    DingTalkAccessor,
    DingTalkImportError,
)
from openviking.parse.accessors.dingtalk_client import DingTalkClient, DingTalkMCPError
from openviking.parse.accessors.registry import AccessorRegistry
from openviking.resource.staged_source import StagedSource
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.utils.config.dingtalk_config import (
    DingTalkConfig,
    DingTalkIdentityConfig,
    DingTalkMCPServerConfig,
)

NODE_ID = "N" * 32
DOC_URL = f"https://alidocs.dingtalk.com/i/nodes/{NODE_ID}"


def _config() -> DingTalkConfig:
    return DingTalkConfig(
        identities={
            "main": DingTalkIdentityConfig(
                label="Documentation",
                doc=DingTalkMCPServerConfig(
                    url="https://mcp.example.invalid/path?secret=value",
                    headers={"Authorization": "Bearer secret"},
                ),
            )
        }
    )


def _full_config() -> DingTalkConfig:
    endpoint = DingTalkMCPServerConfig(url="https://mcp.example.invalid")
    return DingTalkConfig(
        identities={
            "main": DingTalkIdentityConfig(
                doc=endpoint,
                sheets=endpoint,
                ai_table=endpoint,
            )
        }
    )


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def call(self, service, tool, arguments):
        self.calls.append((service, tool, arguments))
        result = self.handler(service, tool, arguments)
        if isinstance(result, Exception):
            raise result
        return result


def _document_handler(title="Original", markdown="Body"):
    def handler(service, tool, arguments):
        if (service, tool) == ("doc", "get_document_info"):
            return {
                "success": True,
                "nodeId": NODE_ID,
                "nodeType": "file",
                "contentType": "ALIDOC",
                "extension": "adoc",
                "name": title,
                "docUrl": DOC_URL,
                "updateTime": 123,
            }
        if (service, tool) == ("doc", "get_document_content"):
            return {"success": True, "markdown": markdown}
        if (service, tool) == ("doc", "list_document_blocks"):
            return {"success": True, "blocks": [], "hasMore": False}
        raise AssertionError((service, tool, arguments))

    return handler


def test_content_digest_ignores_only_generated_timestamp_header(tmp_path):
    document = tmp_path / "node.md"
    header = "# Title\n\n- DingTalk node ID: `node`\n- Source: https://example.invalid\n"
    document.write_text(header + "- DingTalk updated: 1\n\n- DingTalk updated: user text\n")
    original = DingTalkAccessor._tree_digest(tmp_path, document)
    document.write_text(header + "- DingTalk updated: 2\n\n- DingTalk updated: user text\n")
    assert DingTalkAccessor._tree_digest(tmp_path, document) == original
    document.write_text(header + "- DingTalk updated: 2\n\n- DingTalk updated: changed text\n")
    assert DingTalkAccessor._tree_digest(tmp_path, document) != original


def test_config_hides_credentials_and_exposes_only_identity_labels():
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    config = _config()

    assert "secret" not in repr(config)
    assert "mcp.example" not in repr(config)
    assert config.identity_options() == [{"name": "main", "label": "Documentation"}]

    with pytest.raises(ValidationError) as error:
        DingTalkMCPServerConfig(url="token-value", headers={})
    assert "token-value" not in str(error.value)
    with pytest.raises(ValidationError) as nested_error:
        OpenVikingConfig(dingtalk={"identities": {"main": {"doc": {"url": "token-value"}}}})
    assert "token-value" not in str(nested_error.value)


@pytest.mark.asyncio
async def test_client_rejects_write_tools_and_unconfigured_optional_service():
    identity = _config().identities["main"]
    client = DingTalkClient(identity)

    with pytest.raises(ValueError, match="not allowed"):
        await client.call("doc", "update_document", {})
    with pytest.raises(DingTalkMCPError) as error:
        await client.call("sheets", "get_all_sheets", {"nodeId": NODE_ID})
    assert "secret" not in str(error.value)


def test_registry_claims_supported_dingtalk_domain_before_http():
    registry = AccessorRegistry()

    assert isinstance(registry.get_accessor(DOC_URL), DingTalkAccessor)
    assert isinstance(registry.get_accessor("https://alidocs.dingtalk.com/login"), DingTalkAccessor)
    assert isinstance(
        registry.get_accessor("https://alidocs.dingtalk.com./login"), DingTalkAccessor
    )
    assert isinstance(
        registry.get_accessor("http://alidocs.dingtalk.com/i/nodes/bad"), DingTalkAccessor
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_denied", [False, True])
async def test_mcp_retries_transient_errors_but_never_permission_errors(
    monkeypatch, permission_denied
):
    client = DingTalkClient(_config().identities["main"])
    failure = DingTalkMCPError(
        "doc.get_document_info", permission_denied=permission_denied, retryable=True
    )
    client._call_once = AsyncMock(side_effect=[failure, failure, {"nodeId": NODE_ID}])
    monkeypatch.setattr("openviking.parse.accessors.dingtalk_client.asyncio.sleep", AsyncMock())
    if permission_denied:
        with pytest.raises(DingTalkMCPError):
            await client.call("doc", "get_document_info", {"nodeId": NODE_ID})
        assert client._call_once.await_count == 1
    else:
        assert await client.call("doc", "get_document_info", {"nodeId": NODE_ID}) == {
            "nodeId": NODE_ID
        }
        assert client._call_once.await_count == 3


@pytest.mark.asyncio
async def test_cloud_file_mcp_failure_identifies_operation_and_node():
    def handler(service, tool, arguments):
        if tool == "get_document_info":
            return {
                "nodeId": NODE_ID,
                "nodeType": "file",
                "contentType": "DOCUMENT",
                "extension": "docx",
                "name": "Guide",
            }
        return DingTalkMCPError("doc.download_file", reason_code="internalError", retryable=True)

    accessor = DingTalkAccessor(_config(), client_factory=lambda _: FakeClient(handler))

    with pytest.raises(
        DingTalkImportError,
        match=r"doc\.download_file failed \(internalError\); no partial content was kept",
    ) as error:
        await accessor.access(DOC_URL, dingtalk_identity="main")

    assert error.value.details["dingtalk_failure"] == {
        "node_id": NODE_ID,
        "operation": "doc.download_file",
        "reason_code": "internalError",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_folder_skips_unreadable_child_file_and_keeps_readable_content(monkeypatch):
    folder_id, readable_id, unreadable_id, blocked_id = (
        "F" * 32,
        "R" * 32,
        "U" * 32,
        "P" * 32,
    )

    def handler(service, tool, arguments):
        node_id = arguments.get("nodeId")
        if tool == "get_document_info":
            if node_id == folder_id:
                return {"nodeId": node_id, "nodeType": "folder", "name": "Folder"}
            if node_id == readable_id:
                return {
                    "nodeId": node_id,
                    "nodeType": "file",
                    "contentType": "ALIDOC",
                    "extension": "adoc",
                    "name": "Readable",
                }
            if node_id == blocked_id:
                return {
                    "nodeId": node_id,
                    "nodeType": "file",
                    "contentType": "ALIDOC",
                    "extension": "adoc",
                    "name": "Blocked asset",
                }
            return {
                "nodeId": node_id,
                "nodeType": "file",
                "contentType": "DOCUMENT",
                "extension": "docx",
                "name": "Unreadable",
            }
        if tool == "list_nodes":
            return {
                "nodes": [
                    {"nodeId": readable_id},
                    {"nodeId": unreadable_id},
                    {"nodeId": blocked_id},
                ]
            }
        if tool == "get_document_content":
            if node_id == blocked_id:
                return {"markdown": "![blocked](https://assets.example.invalid/image.png)"}
            return {"markdown": "Readable body"}
        if tool == "list_document_blocks":
            return {"blocks": [], "hasMore": False}
        return DingTalkMCPError("doc.download_file", reason_code="internalError", retryable=True)

    accessor = DingTalkAccessor(_config(), client_factory=lambda _: FakeClient(handler))

    async def blocked_download(state, url, *, headers):
        raise PermissionDeniedError("blocked remote asset")

    monkeypatch.setattr(accessor, "_download", blocked_download)
    source = f"https://alidocs.dingtalk.com/i/nodes/{folder_id}"
    resource = await accessor.access(source, dingtalk_identity="main")
    try:
        assert (resource.path / folder_id / f"{readable_id}.md").is_file()
        assert not (resource.path / folder_id / f"{unreadable_id}.docx").exists()
        assert resource.meta["dingtalk_report"]["skipped_nodes"] == [
            unreadable_id,
            blocked_id,
        ]
        assert resource.meta["dingtalk_report"]["unreadable_nodes"] == [
            {
                "node_id": unreadable_id,
                "operation": "doc.download_file",
                "reason_code": "internalError",
            },
            {
                "node_id": blocked_id,
                "operation": "document.read",
                "reason_code": "PERMISSION_DENIED",
            },
        ]
        unreadable = next(
            item for item in resource.meta["dingtalk_manifest"] if item["node_id"] == unreadable_id
        )
        assert unreadable["unreadable"] is True
        assert any("Skipped 2 unreadable" in item for item in resource.meta["dingtalk_limitations"])
    finally:
        resource.cleanup()

    previous_path = f"{folder_id}/{unreadable_id}.docx"
    previous = {
        "complete": True,
        "processing_key": "same-settings",
        "manifest": [
            {
                "node_id": unreadable_id,
                "relative_path": previous_path,
                "type": "file",
                "title": "Unreadable",
                "source_paths": [previous_path],
                "content_sha256": "a" * 64,
                "requires_content_check": True,
            }
        ],
    }
    retried = await accessor.access(
        source,
        dingtalk_identity="main",
        dingtalk_processing_key="same-settings",
        dingtalk_previous_state=previous,
    )
    try:
        retained = next(
            item for item in retried.meta["dingtalk_manifest"] if item["node_id"] == unreadable_id
        )
        assert retained["reused"] is True
        assert retained["reuse_mode"] == "unreadable"
        assert retained["source_paths"] == [previous_path]
        assert retried.meta["dingtalk_report"]["unreadable_reused_nodes"] == [unreadable_id]
    finally:
        retried.cleanup()


@pytest.mark.asyncio
async def test_moved_unreadable_document_is_retried_at_its_new_path():
    root_id, old_parent_id, new_parent_id, document_id = (
        "F" * 32,
        "O" * 32,
        "N" * 32,
        "D" * 32,
    )
    source = f"https://alidocs.dingtalk.com/i/nodes/{root_id}"

    def handler(parent_id, update_time, *, fail_content=False, content_calls=None):
        def respond(service, tool, arguments):
            node_id = arguments.get("nodeId")
            if tool == "get_document_info":
                if node_id in {root_id, parent_id}:
                    return {"nodeId": node_id, "nodeType": "folder", "name": node_id}
                return {
                    "nodeId": document_id,
                    "nodeType": "file",
                    "contentType": "ALIDOC",
                    "extension": "adoc",
                    "name": "Moved document",
                    "updateTime": update_time,
                }
            if tool == "list_nodes":
                folder_id = arguments["folderId"]
                return {"nodes": [{"nodeId": parent_id if folder_id == root_id else document_id}]}
            if tool == "get_document_content":
                if content_calls is not None:
                    content_calls.append(document_id)
                if fail_content:
                    return DingTalkMCPError("doc.get_document_content", retryable=True)
                return {"markdown": "Body"}
            if tool == "list_document_blocks":
                return {"blocks": [], "hasMore": False}
            raise AssertionError((service, tool, arguments))

        return respond

    initial = await DingTalkAccessor(
        _config(), client_factory=lambda _: FakeClient(handler(old_parent_id, 1))
    ).access(source, dingtalk_identity="main", dingtalk_processing_key="same-settings")
    first_manifest = initial.meta["dingtalk_manifest"]
    initial.cleanup()

    failed_refresh = await DingTalkAccessor(
        _config(),
        client_factory=lambda _: FakeClient(handler(new_parent_id, 2, fail_content=True)),
    ).access(
        source,
        dingtalk_identity="main",
        dingtalk_processing_key="same-settings",
        dingtalk_previous_state={
            "complete": True,
            "processing_key": "same-settings",
            "manifest": first_manifest,
        },
    )
    try:
        retained = next(
            item
            for item in failed_refresh.meta["dingtalk_manifest"]
            if item["node_id"] == document_id
        )
        old_path = f"{root_id}/{old_parent_id}/{document_id}.md"
        assert retained["relative_path"] == old_path
        assert retained["source_paths"] == [old_path]
        assert retained["source_marker"] == {"field": "updateTime", "value": 1}
        assert retained["requires_content_check"] is True
        failed_manifest = failed_refresh.meta["dingtalk_manifest"]
    finally:
        failed_refresh.cleanup()

    content_calls = []
    successful_refresh = await DingTalkAccessor(
        _config(),
        client_factory=lambda _: FakeClient(handler(new_parent_id, 2, content_calls=content_calls)),
    ).access(
        source,
        dingtalk_identity="main",
        dingtalk_processing_key="same-settings",
        dingtalk_previous_state={
            "complete": True,
            "processing_key": "same-settings",
            "manifest": failed_manifest,
        },
    )
    try:
        new_path = f"{root_id}/{new_parent_id}/{document_id}.md"
        refreshed = next(
            item
            for item in successful_refresh.meta["dingtalk_manifest"]
            if item["node_id"] == document_id
        )
        assert content_calls == [document_id]
        assert (successful_refresh.path / new_path).is_file()
        assert refreshed["relative_path"] == new_path
        assert refreshed["source_paths"] == [new_path]
        assert refreshed["reused"] is False
    finally:
        successful_refresh.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 410])
async def test_folder_skips_missing_download_and_keeps_readable_sibling(monkeypatch, status_code):
    folder_id, readable_id, missing_id = "F" * 32, "R" * 32, "M" * 32
    source = f"https://alidocs.dingtalk.com/i/nodes/{folder_id}"

    def handler(service, tool, arguments):
        node_id = arguments.get("nodeId")
        if tool == "get_document_info":
            if node_id == folder_id:
                return {"nodeId": node_id, "nodeType": "folder", "name": "Folder"}
            if node_id == readable_id:
                return {
                    "nodeId": node_id,
                    "nodeType": "file",
                    "contentType": "ALIDOC",
                    "extension": "adoc",
                    "name": "Readable",
                }
            return {
                "nodeId": node_id,
                "nodeType": "file",
                "contentType": "DOCUMENT",
                "extension": "pdf",
                "name": "Missing",
            }
        if tool == "list_nodes":
            return {"nodes": [{"nodeId": readable_id}, {"nodeId": missing_id}]}
        if tool == "get_document_content":
            return {"markdown": "Readable body"}
        if tool == "list_document_blocks":
            return {"blocks": [], "hasMore": False}
        if tool == "download_file":
            return {"resourceUrl": "https://download.example.invalid/missing.pdf"}
        raise AssertionError((service, tool, arguments))

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code, request=request))

    def mock_async_client(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(
        "openviking.parse.accessors.dingtalk_accessor.httpx.AsyncClient",
        mock_async_client,
    )
    monkeypatch.setattr(
        "openviking.parse.accessors.dingtalk_accessor.asyncio.sleep",
        AsyncMock(),
    )
    resource = await DingTalkAccessor(
        _config(), client_factory=lambda _: FakeClient(handler)
    ).access(source, dingtalk_identity="main")
    try:
        assert (resource.path / folder_id / f"{readable_id}.md").is_file()
        assert not (resource.path / folder_id / f"{missing_id}.pdf").exists()
        assert resource.meta["dingtalk_report"]["skipped_nodes"] == [missing_id]
        assert resource.meta["dingtalk_report"]["unreadable_nodes"] == [
            {
                "node_id": missing_id,
                "operation": "file.read",
                "reason_code": "NOT_FOUND",
            }
        ]
    finally:
        resource.cleanup()


@pytest.mark.asyncio
async def test_document_path_is_stable_across_rename_and_manifest_has_source_fields():
    paths = []
    for title in ("Before rename", "After rename"):
        fake = FakeClient(_document_handler(title=title))
        accessor = DingTalkAccessor(_config(), client_factory=lambda identity, fake=fake: fake)
        resource = await accessor.access(
            DOC_URL,
            dingtalk_identity="main",
            source_name="ignored common option",
            parse_mode="default",
        )
        try:
            files = [
                path.relative_to(resource.path).as_posix() for path in resource.path.rglob("*")
            ]
            paths.append(files)
            markdown = (resource.path / f"{NODE_ID}.md").read_text()
            assert f"# {title}" in markdown
            assert f"DingTalk node ID: `{NODE_ID}`" in markdown
            entry = resource.meta["dingtalk_manifest"][0]
            expected = {
                "node_id": NODE_ID,
                "relative_path": f"{NODE_ID}.md",
                "type": "document",
                "title": title,
                "source_url": DOC_URL,
                "updated_at": 123,
                "source_marker": {"field": "updateTime", "value": 123},
                "requires_content_check": False,
                "reused": False,
            }
            assert {key: entry[key] for key in expected} == expected
            assert len(entry["content_sha256"]) == 64
            assert entry["source_paths"] == [f"{NODE_ID}.md"]
        finally:
            resource.cleanup()

    assert paths[0] == paths[1] == [f"{NODE_ID}.md"]


@pytest.mark.asyncio
async def test_complete_matching_document_reuses_by_metadata_without_reading_content():
    first = DingTalkAccessor(
        _config(), client_factory=lambda _: FakeClient(_document_handler(markdown="Body"))
    )
    resource = await first.access(
        DOC_URL,
        dingtalk_identity="main",
        dingtalk_processing_key="config-a",
    )
    previous = {
        "manifest": resource.meta["dingtalk_manifest"],
        "processing_key": "config-a",
        "complete": True,
    }
    resource.cleanup()

    def metadata_only(service, tool, arguments):
        assert tool == "get_document_info"
        return _document_handler()(service, tool, arguments)

    fake = FakeClient(metadata_only)
    second = DingTalkAccessor(_config(), client_factory=lambda _: fake)
    reused = await second.access(
        DOC_URL,
        dingtalk_identity="main",
        dingtalk_processing_key="config-a",
        dingtalk_previous_state=previous,
        dingtalk_previous_digest="a" * 64,
    )
    try:
        assert list(reused.path.rglob("*")) == []
        assert reused.meta["dingtalk_manifest"][0]["reused"] is True
        assert reused.meta["dingtalk_manifest"][0]["reuse_mode"] == "metadata"
        assert reused.meta["dingtalk_report"]["metadata_reused_nodes"] == [NODE_ID]
        assert reused.meta["dingtalk_previous_digest"] == "a" * 64
        assert len(reused.meta["dingtalk_run_id"].split("-")) == 5
        assert [tool for _, tool, _ in fake.calls] == ["get_document_info"]
    finally:
        reused.cleanup()


@pytest.mark.asyncio
async def test_document_with_asset_reads_and_hashes_asset_before_content_reuse(monkeypatch):
    remote = "https://alidocs.oss-cn-test.aliyuncs.com/x/res/image.png"

    async def fake_download(state, url, *, headers):
        state.limits.add_bytes(3)
        return b"img", "image/png"

    first_fake = FakeClient(_document_handler(markdown=f"![image]({remote})"))
    first = DingTalkAccessor(_config(), client_factory=lambda _: first_fake)
    monkeypatch.setattr(first, "_download", fake_download)
    initial = await first.access(
        DOC_URL,
        dingtalk_identity="main",
        dingtalk_processing_key="config-a",
    )
    previous = {
        "manifest": initial.meta["dingtalk_manifest"],
        "processing_key": "config-a",
        "complete": True,
    }
    assert previous["manifest"][0]["requires_content_check"] is True
    initial.cleanup()

    second_fake = FakeClient(_document_handler(markdown=f"![image]({remote})"))
    second = DingTalkAccessor(_config(), client_factory=lambda _: second_fake)
    monkeypatch.setattr(second, "_download", fake_download)
    reused = await second.access(
        DOC_URL,
        dingtalk_identity="main",
        dingtalk_processing_key="config-a",
        dingtalk_previous_state=previous,
    )
    try:
        assert list(reused.path.rglob("*")) == []
        assert reused.meta["dingtalk_manifest"][0]["reuse_mode"] == "content"
        assert "get_document_content" in [tool for _, tool, _ in second_fake.calls]
    finally:
        reused.cleanup()


@pytest.mark.asyncio
async def test_mixed_folder_enumerates_all_nodes_but_materializes_only_changed_document():
    folder_id, unchanged_id, changed_id, new_id = (
        "F" * 32,
        "U" * 32,
        "C" * 32,
        "N" * 32,
    )

    def handler(changed_body, *, include_new=False):
        def respond(service, tool, arguments):
            node_id = arguments.get("nodeId")
            if tool == "get_document_info":
                if node_id == folder_id:
                    return {"nodeId": node_id, "nodeType": "folder", "name": "Folder"}
                return {
                    "nodeId": node_id,
                    "nodeType": "file",
                    "extension": "adoc",
                    "name": node_id,
                    "updateTime": 1 if node_id == unchanged_id else changed_body,
                }
            if tool == "list_nodes":
                nodes = [{"nodeId": unchanged_id}, {"nodeId": changed_id}]
                if include_new:
                    nodes.append({"nodeId": new_id})
                return {
                    "nodes": nodes,
                    "nextPageToken": "",
                }
            if tool == "get_document_content":
                if node_id == unchanged_id:
                    return {"markdown": unchanged_id}
                if node_id == new_id:
                    return {"markdown": "newly added"}
                return {"markdown": str(changed_body)}
            if tool == "list_document_blocks":
                return {"blocks": [], "hasMore": False}
            raise AssertionError((service, tool, arguments))

        return respond

    source = f"https://alidocs.dingtalk.com/i/nodes/{folder_id}"
    initial = await DingTalkAccessor(
        _config(), client_factory=lambda _: FakeClient(handler("old"))
    ).access(source, dingtalk_identity="main", dingtalk_processing_key="config-a")
    previous = {
        "manifest": initial.meta["dingtalk_manifest"],
        "processing_key": "config-a",
        "complete": True,
    }
    initial.cleanup()

    fake = FakeClient(handler("new", include_new=True))
    refreshed = await DingTalkAccessor(_config(), client_factory=lambda _: fake).access(
        source,
        dingtalk_identity="main",
        dingtalk_processing_key="config-a",
        dingtalk_previous_state=previous,
    )
    try:
        folder = refreshed.path / folder_id
        assert not (folder / f"{unchanged_id}.md").exists()
        assert (folder / f"{changed_id}.md").is_file()
        assert (folder / f"{new_id}.md").is_file()
        manifest = {entry["node_id"]: entry for entry in refreshed.meta["dingtalk_manifest"]}
        assert manifest[unchanged_id]["reused"] is True
        assert manifest[changed_id]["reused"] is False
        assert manifest[new_id]["reused"] is False
        assert len([call for call in fake.calls if call[1] == "get_document_info"]) == 4
        assert len([call for call in fake.calls if call[1] == "list_nodes"]) == 1
        assert [call[2]["nodeId"] for call in fake.calls if call[1] == "get_document_content"] == [
            changed_id,
            new_id,
        ]
        assert refreshed.meta["dingtalk_report"]["changed_nodes"] == [changed_id, new_id]
    finally:
        refreshed.cleanup()


@pytest.mark.asyncio
async def test_folder_reads_every_page_and_preserves_id_hierarchy():
    folder_id = "A" * 32
    first_id = "B" * 32
    second_id = "C" * 32

    def handler(service, tool, arguments):
        if tool == "get_document_info":
            node_id = arguments["nodeId"]
            if node_id == folder_id:
                return {"nodeId": node_id, "nodeType": "folder", "name": "Folder"}
            return {
                "nodeId": node_id,
                "nodeType": "file",
                "contentType": "ALIDOC",
                "extension": "adoc",
                "name": "Same title",
            }
        if tool == "list_nodes":
            if "pageToken" not in arguments:
                return {"nodes": [{"nodeId": first_id}], "nextPageToken": "page-2"}
            return {"nodes": [{"nodeId": second_id}], "nextPageToken": ""}
        if tool == "get_document_content":
            return {"markdown": arguments["nodeId"]}
        if tool == "list_document_blocks":
            return {"blocks": [], "hasMore": False}
        raise AssertionError((service, tool, arguments))

    fake = FakeClient(handler)
    accessor = DingTalkAccessor(_config(), client_factory=lambda identity: fake)
    resource = await accessor.access(
        f"https://alidocs.dingtalk.com/i/nodes/{folder_id}",
        dingtalk_identity="main",
    )
    try:
        folder = resource.path / folder_id
        assert (folder / f"{first_id}.md").is_file()
        assert (folder / f"{second_id}.md").is_file()
        list_calls = [call for call in fake.calls if call[1] == "list_nodes"]
        assert len(list_calls) == 2
    finally:
        resource.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("limits", [{}, {"dingtalk_max_depth": 1}, {"dingtalk_max_nodes": 2}])
async def test_nested_paginated_folders_resolve_shortcuts_and_enforce_limits(limits):
    root, folder, alias, document, sibling = [char * 32 for char in "RSZXY"]

    def handler(service, tool, arguments):
        if tool == "get_document_info":
            node = arguments["nodeId"]
            if node in (root, folder):
                return {"nodeId": node, "nodeType": "folder", "name": "Folder"}
            if node == alias:
                return {"nodeId": node, "linkSourceInfo": {"nodeId": document}}
            return {"nodeId": node, "nodeType": "file", "extension": "adoc", "name": "Same title"}
        if tool == "list_nodes":
            if arguments["folderId"] == folder:
                return {"nodes": [{"nodeId": alias}], "nextPageToken": ""}
            if "pageToken" not in arguments:
                return {"nodes": [{"nodeId": folder}], "nextPageToken": "second"}
            return {"nodes": [{"nodeId": document}, {"nodeId": sibling}], "nextPageToken": ""}
        if tool == "get_document_content":
            return {"markdown": arguments["nodeId"]}
        if tool == "list_document_blocks":
            return {"blocks": [], "hasMore": False}
        raise AssertionError(tool)

    accessor = DingTalkAccessor(_config(), client_factory=lambda _: FakeClient(handler))
    source = f"https://alidocs.dingtalk.com/i/nodes/{root}"
    if limits:
        with pytest.raises(DingTalkImportError) as failure:
            await accessor.access(source, dingtalk_identity="main", **limits)
        assert failure.value.code == "RESOURCE_EXHAUSTED"
        return
    resource = await accessor.access(source, dingtalk_identity="main")
    try:
        assert (resource.path / root / folder / f"{document}.md").is_file()
        assert (resource.path / root / f"{sibling}.md").is_file()
        assert len(list(resource.path.rglob("*.md"))) == 2
        assert resource.meta["dingtalk_report"]["skipped_nodes"] == [document]
    finally:
        resource.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", [None, "row_mapping", "range", "truncated"])
async def test_sheet_preserves_sparse_positions_and_rejects_incomplete_cells(problem):
    def handler(service, tool, arguments):
        if tool == "get_document_info":
            return {"nodeId": NODE_ID, "nodeType": "file", "extension": "axls"}
        if tool == "get_all_sheets":
            return {"sheets": [{"id": "sheet", "name": "Sparse"}]}
        if tool == "get_sheet":
            return {
                "rowCount": 10000,
                "columnCount": 1000,
                "nonEmptyRange": {"lastRow": 1, "lastColumn": "C"},
            }
        if tool == "get_range_as_csv":
            response = {
                "csv": "value\n",
                "hasMore": False,
                "returnedRange": arguments["range"],
                "rowIndices": [1],
                "colIndices": ["C"],
                "truncationReasons": [],
            }
            if problem == "row_mapping":
                response["rowIndices"] = [2]
            elif problem == "range":
                response["returnedRange"] = "Z99:Z99"
            elif problem == "truncated":
                response["truncationReasons"] = ["max_chars"]
            return response
        raise AssertionError(tool)

    fake = FakeClient(handler)
    accessor = DingTalkAccessor(_full_config(), client_factory=lambda identity: fake)
    if problem:
        with pytest.raises(DingTalkImportError):
            await accessor.access(DOC_URL, dingtalk_identity="main")
        return
    resource = await accessor.access(DOC_URL, dingtalk_identity="main")
    try:
        content = (resource.path / f"{NODE_ID}.md").read_text()
        assert 'Columns: ["C"]' in content
        assert "Range: A1:C1" in content
        assert [args["range"] for _, tool, args in fake.calls if tool == "get_range_as_csv"] == [
            "A1:C1"
        ]
    finally:
        resource.cleanup()


@pytest.mark.asyncio
async def test_ai_table_cannot_finish_without_continuation_cursor():
    def handler(service, tool, arguments):
        return {
            "get_document_info": {"nodeId": NODE_ID, "nodeType": "file", "extension": "able"},
            "get_base": {},
            "get_tables": {"tables": [{"id": "table"}]},
            "get_fields": {"fields": []},
            "query_records": {"records": [], "hasMore": True},
        }[tool]

    accessor = DingTalkAccessor(_full_config(), client_factory=lambda _: FakeClient(handler))
    with pytest.raises(DingTalkImportError, match="without a cursor"):
        await accessor.access(DOC_URL, dingtalk_identity="main")


@pytest.mark.asyncio
async def test_repeated_folder_cursor_fails_and_removes_temporary_tree(monkeypatch, tmp_path):
    staging = tmp_path / "staging"

    def make_staging(prefix):
        staging.mkdir()
        return str(staging)

    monkeypatch.setattr(
        "openviking.parse.accessors.dingtalk_accessor.tempfile.mkdtemp",
        make_staging,
    )

    def handler(service, tool, arguments):
        if tool == "list_nodes":
            return {"nodes": [], "nextPageToken": "same"}
        raise AssertionError((service, tool, arguments))

    accessor = DingTalkAccessor(_config(), client_factory=lambda identity: FakeClient(handler))
    with pytest.raises(DingTalkImportError, match="repeated cursor"):
        await accessor.access(
            "https://alidocs.dingtalk.com/i/spaces/Workspace123/overview",
            dingtalk_identity="main",
        )
    assert not staging.exists()


@pytest.mark.asyncio
async def test_shortcut_ancestor_cycle_fails():
    target = "D" * 32

    def handler(service, tool, arguments):
        node_id = arguments["nodeId"]
        return {
            "nodeId": node_id,
            "nodeType": "file",
            "linkSourceInfo": {"nodeId": target if node_id == NODE_ID else NODE_ID},
        }

    accessor = DingTalkAccessor(_config(), client_factory=lambda identity: FakeClient(handler))
    with pytest.raises(DingTalkImportError, match="cycle"):
        await accessor.access(DOC_URL, dingtalk_identity="main")


@pytest.mark.asyncio
async def test_byte_limit_fails_and_cleans_up(monkeypatch, tmp_path):
    staging = tmp_path / "staging"

    def make_staging(prefix):
        staging.mkdir()
        return str(staging)

    monkeypatch.setattr(
        "openviking.parse.accessors.dingtalk_accessor.tempfile.mkdtemp",
        make_staging,
    )
    fake = FakeClient(_document_handler(markdown="too large"))
    accessor = DingTalkAccessor(_config(), client_factory=lambda identity: fake)

    with pytest.raises(DingTalkImportError, match="byte limit"):
        await accessor.access(DOC_URL, dingtalk_identity="main", dingtalk_max_bytes=4)
    assert not staging.exists()


@pytest.mark.asyncio
async def test_missing_sheet_endpoint_is_a_safe_failed_precondition():
    def handler(service, tool, arguments):
        return {
            "nodeId": NODE_ID,
            "nodeType": "file",
            "contentType": "ALIDOC",
            "extension": "axls",
            "name": "Sheet",
        }

    accessor = DingTalkAccessor(_config(), client_factory=lambda identity: FakeClient(handler))
    with pytest.raises(DingTalkImportError) as error:
        await accessor.access(DOC_URL, dingtalk_identity="main")
    assert error.value.code == "FAILED_PRECONDITION"
    assert error.value.details["dingtalk_report"]["failed"] == 1
    assert error.value.details["dingtalk_report"]["failed_nodes"] == [NODE_ID]
    assert error.value.details["dingtalk_limits"]["max_nodes"] == 1000
    assert "endpoint" in str(error.value)


@pytest.mark.asyncio
async def test_markdown_asset_link_is_downloaded_without_an_attachment_block(monkeypatch):
    remote = "https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/x/res/file.pdf?signature=x"
    fake = FakeClient(_document_handler(markdown=f"[attachment]({remote})"))
    accessor = DingTalkAccessor(_config(), client_factory=lambda identity: fake)

    async def fake_download(state, url, *, headers):
        assert url == remote
        data = b"pdf"
        state.limits.add_bytes(len(data))
        return data, "application/pdf"

    monkeypatch.setattr(accessor, "_download", fake_download)
    resource = await accessor.access(DOC_URL, dingtalk_identity="main")
    try:
        asset = resource.path / "attachments" / f"{hashlib.sha256(b'pdf').hexdigest()}.pdf"
        assert asset.read_bytes() == b"pdf"
        markdown = (resource.path / f"{NODE_ID}.md").read_text()
        assert "[attachment](attachments/" in markdown
        assert remote not in markdown
    finally:
        resource.cleanup()


def test_staged_source_accepts_dingtalk():
    staged = StagedSource.from_dict(
        {
            "temp_uri": "viking://temp/task",
            "source_uri": "viking://temp/task/source/dingtalk_node",
            "source_type": SourceType.DINGTALK,
            "original_source": DOC_URL,
            "meta": {},
        }
    )
    assert staged.source_type == SourceType.DINGTALK


@pytest.mark.asyncio
async def test_unsupported_dingtalk_path_never_falls_back_to_login_page():
    accessor = DingTalkAccessor(_config())
    assert accessor.can_handle("https://alidocs.dingtalk.com/login")
    with pytest.raises(InvalidArgumentError, match="Unsupported DingTalk URL path"):
        await accessor.access("https://alidocs.dingtalk.com/login", dingtalk_identity="main")


@pytest.mark.asyncio
async def test_mixed_workspace_materializes_sheet_ai_file_images_and_attachments(monkeypatch):
    doc_id = "E" * 32
    sheet_id = "F" * 32
    ai_id = "G" * 32
    file_id = "H" * 32
    image_url = "https://alidocs.oss-cn-test.aliyuncs.com/x/res/image.png"
    attachment_url = "https://alidocs.oss-cn-test.aliyuncs.com/x/res/file.pdf?signature=x"
    ai_restarted = False

    def handler(service, tool, arguments):
        nonlocal ai_restarted
        if (service, tool) == ("doc", "list_nodes"):
            return {
                "nodes": [
                    {"nodeId": doc_id},
                    {"nodeId": sheet_id},
                    {"nodeId": ai_id},
                    {"nodeId": file_id},
                ],
                "nextPageToken": "",
            }
        if (service, tool) == ("doc", "get_document_info"):
            node_id = arguments["nodeId"]
            common = {"nodeId": node_id, "nodeType": "file", "name": node_id}
            if node_id == doc_id:
                return {**common, "contentType": "ALIDOC", "extension": "adoc"}
            if node_id == sheet_id:
                return {**common, "contentType": "ALIDOC", "extension": "axls"}
            if node_id == ai_id:
                return {**common, "contentType": "ALIDOC", "extension": "able"}
            return {**common, "contentType": "FILE", "extension": "pdf"}
        if (service, tool) == ("doc", "get_document_content"):
            return {"markdown": f"![image]({image_url})\n[attachment]({attachment_url})"}
        if (service, tool) == ("doc", "list_document_blocks"):
            if arguments["startIndex"] == 0:
                return {"blocks": [], "hasMore": True}
            assert arguments["startIndex"] == 100
            return {
                "blocks": [{"resourceId": "attachment-1", "url": attachment_url}],
                "hasMore": False,
            }
        if (service, tool) == ("doc", "download_doc_attachment"):
            return {"downloadUrl": attachment_url}
        if (service, tool) == ("doc", "download_file"):
            return {
                "resourceUrl": ["https://download.example.invalid/file.pdf"],
                "headers": {"Authorization": "secret"},
            }
        if (service, tool) == ("sheets", "get_all_sheets"):
            return {"sheets": [{"id": "sheet-1", "name": "Sheet"}]}
        if (service, tool) == ("sheets", "get_sheet"):
            return {"nonEmptyRange": {"lastRow": 2, "lastColumn": "B"}}
        if (service, tool) == ("sheets", "get_range_as_csv"):
            if arguments["range"] == "A1:B2":
                return {"csv": "", "hasMore": True}
            row = int(arguments["range"].split(":")[0][1:])
            return {
                "csv": "a,b\n",
                "hasMore": False,
                "returnedRange": arguments["range"],
                "rowIndices": [row],
                "colIndices": ["A", "B"],
                "truncationReasons": [],
            }
        if (service, tool) == ("ai_table", "get_base"):
            return {"baseName": "Base"}
        if (service, tool) == ("ai_table", "get_tables"):
            return {"tables": [{"id": "table-1", "name": "Table"}]}
        if (service, tool) == ("ai_table", "get_fields"):
            return {"fields": [{"id": "field-1", "name": "Name"}]}
        if (service, tool) == ("ai_table", "query_records"):
            cursor = arguments.get("cursor")
            if cursor == "stale":
                ai_restarted = True
                return DingTalkMCPError(
                    "ai_table.query_records", reason_code="CURSOR_SNAPSHOT_CHANGED"
                )
            if cursor == "last":
                return {"records": [{"id": "new-2"}], "nextCursor": ""}
            if ai_restarted:
                return {"records": [{"id": "new-1"}], "nextCursor": "last"}
            return {"records": [{"id": "old"}], "nextCursor": "stale"}
        raise AssertionError((service, tool, arguments))

    fake = FakeClient(handler)
    accessor = DingTalkAccessor(_full_config(), client_factory=lambda identity: fake)

    async def fake_download(state, url, *, headers):
        data = b"image" if url == image_url else b"file"
        state.limits.add_bytes(len(data))
        content_type = "image/png" if url == image_url else "application/pdf"
        return data, content_type

    monkeypatch.setattr(accessor, "_download", fake_download)
    resource = await accessor.access(
        "https://alidocs.dingtalk.com/i/spaces/Workspace123/overview",
        dingtalk_identity="main",
    )
    try:
        doc = (resource.path / f"{doc_id}.md").read_text()
        assert "aliyuncs.com" not in doc
        assert list((resource.path / "assets").glob("*.png"))
        assert (resource.path / "attachments" / "attachment-1.pdf").is_file()
        sheet = (resource.path / f"{sheet_id}.md").read_text()
        assert sheet.count("a,b") == 2
        ai_table = (resource.path / f"{ai_id}.md").read_text()
        assert "new-1" in ai_table and "new-2" in ai_table and '"old"' not in ai_table
        assert (resource.path / f"{file_id}.pdf").read_bytes() == b"file"
        assert {item["type"] for item in resource.meta["dingtalk_manifest"]} == {
            "workspace",
            "document",
            "sheet",
            "ai_table",
            "file",
        }
    finally:
        resource.cleanup()


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "https://user:secret@example.test/mcp",
        "https://example.test/#secret",
        "https://example.test:invalid/mcp",
        "file:///private/token",
    ],
)
def test_invalid_mcp_endpoint_is_rejected_without_exposing_its_value(url):
    with pytest.raises(ValidationError) as error:
        DingTalkMCPServerConfig(url=url)
    assert "input_value" not in str(error.value)
    if url != "https://":
        assert url not in str(error.value)
