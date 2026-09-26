from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parser_router import ParserRouter
from openviking.storage.context_update_plan import ContextUpdatePlan
from openviking.utils.resource_processor import ResourceProcessor


class _FakeDB:
    def get_embedder(self):
        return None


@pytest.mark.parametrize("backend", [None, "understanding"])
def test_dingtalk_url_never_bypasses_its_accessor_for_direct_understanding(monkeypatch, backend):
    router = ParserRouter(SimpleNamespace())
    monkeypatch.setattr(router, "should_use_understanding_api", lambda *_: True)
    router._understanding_api = SimpleNamespace(can_submit_url_directly=lambda *a, **kw: True)
    assert not router.should_use_understanding_directly(
        "https://alidocs.dingtalk.com/i/nodes/NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN",
        parser_backend=backend,
    )


@pytest.mark.asyncio
async def test_dingtalk_local_parse_failures_are_reported_and_readable_content_is_kept(
    monkeypatch,
    tmp_path,
):
    store = LocalParseOutputStore(str(tmp_path / "parse-output"))
    artifact_ref = await store.create_artifact()
    await store.write_text(
        artifact_ref,
        "dingtalk_Source123/node-a.md",
        "readable content",
    )
    parse_result = ParseResult(
        root=ResourceNode(type=NodeType.ROOT),
        source_path="/staged/dingtalk_Source123",
        source_format="directory",
        temp_dir_path=artifact_ref.root,
        artifact_ref=artifact_ref,
        warnings=["Failed to parse broken.doc: parser error"],
        meta={
            "file_count": 1,
            "total_processable": 3,
            "processed_files": [{"path": "node-a.md"}],
            "failed_files": [{"path": "broken.doc", "error": "parser error"}],
            "unsupported_files": [{"path": "legacy.wps", "reason": "unsupported"}],
            "skipped_files": [],
            "dingtalk_manifest": [{"node_id": "node-a", "relative_path": "node-a.md"}],
            "dingtalk_report": {"success": 2, "failed": 0, "skipped": 0, "unsupported": 0},
            "dingtalk_limits": {"max_nodes": 100, "nodes": 2},
        },
    )
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
        bind_request_context=lambda _ctx: nullcontext(),
        delete_temp=AsyncMock(),
        exists=AsyncMock(return_value=False),
        persist_temp_tree=AsyncMock(),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    rewrite_image_uris = AsyncMock()
    monkeypatch.setattr(
        "openviking.utils.resource_processor.rewrite_image_uris", rewrite_image_uris
    )
    processor = ResourceProcessor(_FakeDB())
    processor._build_parse_output_store = Mock(return_value=store)
    processor._media_processor = SimpleNamespace(process=AsyncMock(return_value=parse_result))
    processor.tree_builder.finalize_from_temp = AsyncMock(
        return_value=SimpleNamespace(
            root=SimpleNamespace(
                uri="viking://resources/dingtalk_Source123",
                temp_uri="viking://temp/parse-1/dingtalk_Source123",
            ),
            _candidate_uri=None,
            _root_is_file=False,
        )
    )
    submitted = {}

    async def commit_artifact(**kwargs):
        submitted["content"] = await kwargs["output_store"].read_text(
            kwargs["artifact_ref"],
            "dingtalk_Source123/node-a.md",
        )
        return ContextUpdatePlan(
            root_uri="viking://resources/dingtalk_Source123",
            context_type="resource",
        )

    processor._commit_directory_artifact_with_plan = AsyncMock(side_effect=commit_artifact)

    ctx = SimpleNamespace(account_id="a")
    result = await processor.process_resource(
        "https://alidocs.dingtalk.com/i/nodes/Source123",
        ctx=ctx,
        to="viking://resources/dingtalk_Source123",
        defer_post_processing=True,
        resource_lock={"lease_ref": "lock-1"},
    )

    assert result["status"] == "success"
    assert result["meta"]["dingtalk_parse_skipped"] == {
        "failed": 1,
        "unsupported": 1,
        "total": 2,
    }
    assert result["meta"]["dingtalk_report"]["skipped"] == 2
    assert {item["path"] for item in result["meta"]["skipped_files"]} == {
        "broken.doc",
        "legacy.wps",
    }
    assert any("skipped 2 local file" in warning for warning in result["warnings"])
    processor.tree_builder.finalize_from_temp.assert_awaited_once()
    processor._commit_directory_artifact_with_plan.assert_awaited_once()
    assert submitted == {"content": "readable content"}
    viking_fs.persist_temp_tree.assert_not_awaited()
