import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import DagStats, SemanticDagExecutor
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry import get_current_telemetry, register_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking_cli.session.user_id import UserIdentifier
from tests.storage.test_semantic_dag_skip_files import _FakeVikingFS


@pytest.mark.asyncio
async def test_skill_package_indexes_all_layers_without_replacing_root(monkeypatch):
    root = "viking://agent/skills/demo"
    tree = {
        root: [
            {"name": "SKILL.md"},
            {"name": "reference", "isDir": True},
            {"name": ".source.json"},
        ],
        f"{root}/reference": [{"name": "api.md"}, {"name": "SKILL.md"}, {"name": "image.png"}],
    }
    fs = _FakeVikingFS(tree)
    fs.exists = AsyncMock(return_value=True)
    fs.read_file = AsyncMock(
        side_effect=lambda uri, **kw: (
            "name: demo\ndescription: Skill" if uri.endswith(".abstract.md") else "Skill overview"
        )
    )
    for module in ("semantic_dag", "semantic_processor"):
        monkeypatch.setattr(f"openviking.storage.queuefs.{module}.get_viking_fs", lambda: fs)
    processor = SemanticProcessor()
    processor._generate_single_file_summary = AsyncMock(
        side_effect=lambda uri, **kw: {
            "name": uri.rsplit("/", 1)[-1],
            "summary": "Attachment summary",
        }
    )
    processor._generate_overview = AsyncMock(return_value="# Reference\n\nReference description")
    processor._vectorize_single_file = AsyncMock(return_value=True)
    processor._vectorize_directory = AsyncMock()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="skill",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER),
    )
    await executor.run(root)

    assert processor._generate_overview.await_count == 1
    assert processor._generate_overview.await_args.args[0] == f"{root}/reference"
    assert len(processor._vectorize_single_file.await_args_list) == 4
    assert all(
        call.kwargs["context_type"] == "skill"
        for call in processor._vectorize_single_file.await_args_list
    )
    assert {call.args[0] for call in processor._vectorize_directory.await_args_list} == {
        root,
        f"{root}/reference",
    }
    root_call = next(
        call for call in processor._vectorize_directory.await_args_list if call.args[0] == root
    )
    assert root_call.kwargs["abstract"] == "name: demo\ndescription: Skill"
    assert root_call.kwargs["overview"] == "Skill overview"
    assert all(uri.startswith(f"{root}/reference/") for uri, _ in fs.writes)
    assert executor.get_stats().indexed_records == 8


@pytest.mark.asyncio
async def test_skill_summary_failure_keeps_other_files_and_reports_uri(monkeypatch):
    root = "viking://agent/skills/demo"
    fs = _FakeVikingFS({root: [{"name": "bad.md"}, {"name": "good.md"}]})
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fs)
    processor = SemanticProcessor()
    processor._skill_root_semantics = AsyncMock(return_value=("overview", "name: demo"))

    async def summary(uri, **kw):
        if uri.endswith("bad.md"):
            raise ValueError("summary failed")
        return {"name": "good.md", "summary": "good"}

    processor._generate_single_file_summary = summary
    processor._vectorize_single_file = AsyncMock(return_value=True)
    processor._vectorize_directory = AsyncMock()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="skill",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER),
    )
    await executor.run(root)
    assert processor._vectorize_single_file.await_count == 2
    assert executor.get_stats().failures == [f"{root}/bad.md: summary failed"]


@pytest.mark.asyncio
async def test_skill_root_reindex_uses_existing_skill_prompt_and_metadata(monkeypatch):
    root = "viking://agent/skills/demo"
    fs = _FakeVikingFS({})
    fs.read_file = AsyncMock(
        return_value="---\nname: demo\ndescription: Description\ntags: [test]\nallowed-tools: Read\n---\n\nSkill body"
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    write = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.write_abstract_overview", write
    )
    completion = AsyncMock(return_value="Skill overview")
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(vlm=SimpleNamespace(get_completion_async=completion)),
    )
    processor = SemanticProcessor()
    overview, abstract = await processor._skill_root_semantics(
        root,
        ctx=RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER),
        regenerate=True,
    )
    assert overview == "Skill overview"
    assert "name: demo" in abstract and "allowed_tools:" in abstract and "tags:" in abstract
    assert "Skill body" in completion.await_args.args[0]
    assert write.await_args.kwargs["abstract"] == abstract


@pytest.mark.asyncio
async def test_skill_worker_keeps_package_locked_until_embeddings_finish(monkeypatch):
    root = "viking://agent/skills/demo"
    emitted = asyncio.Event()
    locked = True
    events = []
    tracker = get_request_wait_tracker()
    telemetry = get_current_telemetry()
    register_telemetry(telemetry)
    tracker.register_request(telemetry.telemetry_id)
    msg = SemanticMsg(
        uri=root,
        context_type="skill",
        telemetry_id=telemetry.telemetry_id,
        generation_trigger="skill_ingest",
        propagate_to_parent=False,
    )
    tracker.register_semantic_root(msg.telemetry_id, msg.id)

    class Lease:
        lock = {"lease_ref": "package"}

        async def close(self):
            nonlocal locked
            locked = False
            events.append("released")

    class Executor:
        def __init__(self, **kwargs):
            self.stale = False

        async def run(self, uri):
            tracker.register_embedding_root(msg.telemetry_id, "file-embedding")
            emitted.set()

        def get_stats(self):
            return DagStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor", Executor
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=Lease()),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: SimpleNamespace()
    )
    worker = asyncio.create_task(SemanticProcessor().on_dequeue({"data": msg.to_json()}))
    await asyncio.wait_for(emitted.wait(), 1)
    assert locked and not worker.done() and not tracker.is_complete(msg.telemetry_id)
    # The HTTP timeout cleanup must not release the package while embedding runs.
    tracker.cleanup(msg.telemetry_id)
    await asyncio.sleep(0.06)
    assert locked and not worker.done() and not tracker.is_complete(msg.telemetry_id)
    # Deletion cannot pass the package lease while a file vector can still write.
    events.append("embedding-written")
    tracker.mark_embedding_done(msg.telemetry_id, "file-embedding", vector_written=True)
    await asyncio.wait_for(worker, 1)
    assert not locked and tracker.is_complete(msg.telemetry_id)
    assert events == ["embedding-written", "released"]
    tracker.cleanup(msg.telemetry_id)


@pytest.mark.asyncio
async def test_skill_root_index_keeps_metadata(monkeypatch):
    index = AsyncMock()
    monkeypatch.setattr("openviking.utils.embedding_utils.vectorize_directory_meta", index)
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)
    await SemanticProcessor()._vectorize_directory(
        "viking://agent/skills/demo",
        "skill",
        "name: demo\ndescription: Description\ntags: [tag]\nallowed_tools: [Read]",
        "Skill overview",
        ctx=ctx,
        skill_source_path="/tmp/demo",
    )
    assert index.await_args.kwargs["meta"] == {
        "name": "demo",
        "description": "Description",
        "tags": ["tag"],
        "allowed_tools": ["Read"],
        "source_path": "/tmp/demo",
    }
