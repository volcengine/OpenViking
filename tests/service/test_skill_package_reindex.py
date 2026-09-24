from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_vectors_only_reindexes_entire_skill_with_common_media_inputs(monkeypatch):
    root = "viking://agent/skills/demo"
    tree = {
        root: [
            {"name": "SKILL.md"},
            {"name": "reference", "isDir": True},
            {"name": ".source.json"},
        ],
        root + "/reference": [
            {"name": "image.png"},
            {"name": "video.mp4"},
            {"name": "nested", "isDir": True},
        ],
        root + "/reference/nested": [
            {"name": "api.md"},
            {"name": ".abstract.md"},
            {"name": "messages.jsonl"},
        ],
    }
    fs = SimpleNamespace(ls=AsyncMock(side_effect=lambda uri, **kw: tree[uri]))
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: fs)
    executor = ReindexExecutor()
    executor._read_directory_abstract = AsyncMock(return_value="name: demo\ndescription: Demo")
    executor._read_directory_overview = AsyncMock(return_value="overview")
    executor._fetch_existing_record = AsyncMock(
        side_effect=lambda **kw: {"abstract": "media summary", "meta": {"source_path": "/tmp/demo"}}
    )
    directories = AsyncMock()
    files = AsyncMock(return_value=True)
    monkeypatch.setattr("openviking.service.reindex_executor.vectorize_directory_meta", directories)
    monkeypatch.setattr("openviking.service.reindex_executor.vectorize_file", files)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.SemanticProcessor",
        lambda **kw: pytest.fail("vectors_only must not generate summaries"),
    )
    counters = _ReindexCounters()
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)
    await executor._reindex_skill_vectors(uri=root, ctx=ctx, counters=counters)
    assert {call.args[0] for call in directories.await_args_list} == set(tree)
    expected = {
        root + "/SKILL.md",
        root + "/reference/image.png",
        root + "/reference/video.mp4",
        root + "/reference/nested/api.md",
    }
    assert {call.kwargs["file_path"] for call in files.await_args_list} == expected
    assert all(call.kwargs["context_type"] == "skill" for call in files.await_args_list)
    assert all(
        call.kwargs["summary_dict"]["summary"] == "media summary" for call in files.await_args_list
    )
    assert directories.await_args_list[0].kwargs["meta"]["source_path"] == "/tmp/demo"
    assert counters.scanned_records == 7 and counters.rebuilt_records == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "abstract",
    [
        "Manage backups.\nUse when: a backup needs recovery.",
        "Use when: recovery is needed",
        "A plain description",
    ],
)
async def test_vectors_only_preserves_legacy_skill_descriptions(monkeypatch, abstract):
    executor = ReindexExecutor()
    executor._read_directory_abstract = AsyncMock(return_value=abstract)
    executor._read_directory_overview = AsyncMock(return_value="Overview")
    previous_meta = {
        "name": "demo",
        "tags": ["backup"],
        "allowed_tools": ["Read"],
        "source_path": "/demo",
    }
    executor._fetch_existing_record = AsyncMock(return_value={"meta": previous_meta})
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_viking_fs", lambda: SimpleNamespace()
    )
    vectorize = AsyncMock()
    monkeypatch.setattr("openviking.service.reindex_executor.vectorize_directory_meta", vectorize)
    counters = _ReindexCounters()
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)
    await executor._reindex_skill_vectors(
        uri="viking://agent/skills/demo", counters=counters, ctx=ctx, recursive=False
    )
    assert counters.failed_records == 0 and counters.rebuilt_records == 2
    assert vectorize.await_args.kwargs["meta"] == {**previous_meta, "description": abstract}
