import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openviking.storage.vectordb_adapters.base import VIKINGDB_TEXT_FIELD_BYTE_LIMIT


def _load_script_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "maintenance"
        / "vikingdb_content_backfill"
        / "backfill_vikingdb_content.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_vikingdb_content", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_script_module()
BackfillCandidate = backfill.BackfillCandidate
BackfillOptions = backfill.BackfillOptions
ContentBackfillEnumerator = backfill.ContentBackfillEnumerator
ContentBackfillResolver = backfill.ContentBackfillResolver
ContentBackfillRunner = backfill.ContentBackfillRunner
created_after_cutoff = backfill.created_after_cutoff
has_empty_content = backfill.has_empty_content
seed_uri_for_level = backfill.seed_uri_for_level
updated_at_unchanged = backfill.updated_at_unchanged
vector_record_id = backfill.vector_record_id


def test_seed_uri_for_level_adds_marker_paths():
    assert (
        seed_uri_for_level("viking://resources/docs", 0) == "viking://resources/docs/.abstract.md"
    )
    assert (
        seed_uri_for_level("viking://resources/docs", 1) == "viking://resources/docs/.overview.md"
    )
    assert seed_uri_for_level("viking://resources/docs/a.md", 2) == "viking://resources/docs/a.md"


def test_seed_uri_for_level_keeps_existing_marker_paths():
    assert (
        seed_uri_for_level("viking://resources/docs/.abstract.md", 0)
        == "viking://resources/docs/.abstract.md"
    )
    assert (
        seed_uri_for_level("viking://resources/docs/.overview.md", 1)
        == "viking://resources/docs/.overview.md"
    )


def test_vector_record_id_matches_embedding_handler_rule():
    expected = hashlib.md5("acct:viking://resources/docs/.abstract.md".encode("utf-8")).hexdigest()

    assert vector_record_id("acct", "viking://resources/docs", 0) == expected


def test_has_empty_content_treats_missing_and_empty_as_empty():
    assert has_empty_content({}) is True
    assert has_empty_content({"content": ""}) is True
    assert has_empty_content({"content": None}) is True
    assert has_empty_content({"content": "body"}) is False


def test_created_after_cutoff_supports_created_at_and_create_time():
    cutoff = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    assert created_after_cutoff({"created_at": "2026-07-06T12:00:01.000Z"}, cutoff) is True
    assert created_after_cutoff({"create_time": "2026-07-06T11:59:59.000Z"}, cutoff) is False
    assert created_after_cutoff({}, cutoff) is False


def test_updated_at_unchanged_compares_raw_values():
    original = {"updated_at": "2026-07-06T11:59:59.000Z"}

    assert updated_at_unchanged(original, {"updated_at": "2026-07-06T11:59:59.000Z"})
    assert not updated_at_unchanged(
        original,
        {"updated_at": "2026-07-06T12:00:00.000Z"},
    )
    assert updated_at_unchanged({}, {"updated_at": "2026-07-06T12:00:00.000Z"})


class FakeSourceReader:
    def __init__(self):
        self.text = {}
        self.abstracts = {}
        self.overviews = {}

    async def read_text(self, uri, ctx):
        return self.text.get(uri, "")

    async def read_bytes(self, uri, ctx):
        value = self.text.get(uri, "")
        return value.encode("utf-8")

    async def abstract(self, uri, ctx):
        return self.abstracts.get(uri, "")

    async def overview(self, uri, ctx):
        return self.overviews.get(uri, "")


class FakeRawAgfs:
    def __init__(self, entries_by_path):
        self.entries_by_path = entries_by_path

    def ls(self, path):
        return self.entries_by_path.get(path, [])


class FakeTreeReader(FakeSourceReader):
    def __init__(self, trees):
        super().__init__()
        self.trees = trees
        self.calls = []

    async def tree(self, uri, ctx, *, show_all_hidden, node_limit, level_limit):
        self.calls.append(
            {
                "uri": uri,
                "account_id": ctx.account_id,
                "user_id": ctx.user.user_id,
                "node_limit": node_limit,
                "level_limit": level_limit,
            }
        )
        return self.trees.get((ctx.account_id, ctx.user.user_id, uri), [])


class FakeCollection:
    def __init__(self, records):
        self.records = records
        self.updates = []

    def fetch_data(self, ids):
        from openviking.storage.vectordb.collection.result import (
            DataItem,
            FetchDataInCollectionResult,
        )

        items = []
        missing = []
        for record_id in ids:
            record = self.records.get(record_id)
            if record is None:
                missing.append(record_id)
            else:
                items.append(DataItem(id=record_id, fields=dict(record)))
        return FetchDataInCollectionResult(items=items, ids_not_exist=missing)

    def update_data(self, data_list):
        self.updates.extend(data_list)
        for item in data_list:
            record = self.records[item["id"]]
            record.update(item)
        return {"updated": len(data_list), "primary_keys": [item["id"] for item in data_list]}


class FailingCollection(FakeCollection):
    def update_data(self, data_list):
        raise RuntimeError("update failed")


def one_candidate_enumerator(*candidates):
    class Enumerator:
        async def iter_candidates(self):
            for candidate in candidates:
                yield candidate

    return Enumerator()


@pytest.mark.asyncio
async def test_resolver_uses_directory_abstract_for_l0():
    reader = FakeSourceReader()
    reader.abstracts["viking://resources/docs"] = "abstract text"
    resolver = ContentBackfillResolver(reader)
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="user",
        uri="viking://resources/docs",
        level=0,
        context_type="resource",
        expected_record_id="id",
    )

    result = await resolver.resolve(candidate, {"abstract": "fallback"})

    assert result.content == "abstract text"
    assert result.source == "abstract"


@pytest.mark.asyncio
async def test_resolver_uses_directory_overview_for_l1():
    reader = FakeSourceReader()
    reader.overviews["viking://resources/docs"] = "overview text"
    resolver = ContentBackfillResolver(reader)
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="user",
        uri="viking://resources/docs",
        level=1,
        context_type="resource",
        expected_record_id="id",
    )

    result = await resolver.resolve(candidate, {"abstract": "fallback"})

    assert result.content == "overview text"
    assert result.source == "overview"


@pytest.mark.asyncio
async def test_resolver_uses_file_text_for_l2():
    reader = FakeSourceReader()
    reader.text["viking://resources/docs/a.md"] = "full file text"
    resolver = ContentBackfillResolver(reader)
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="user",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="id",
    )

    result = await resolver.resolve(candidate, {"abstract": "summary"})

    assert result.content == "full file text"
    assert result.source == "file"


@pytest.mark.asyncio
async def test_resolver_recreates_memory_chunk_content(monkeypatch):
    reader = FakeSourceReader()
    reader.text["viking://user/u/memories/cases/a.md"] = "aaaa\n\nbbbb\n\ncccc"
    resolver = ContentBackfillResolver(reader)
    monkeypatch.setattr(resolver, "_memory_chunk_chars", 7)
    monkeypatch.setattr(resolver, "_memory_chunk_overlap", 0)
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="u",
        uri="viking://user/u/memories/cases/a.md#chunk_0001",
        level=2,
        context_type="memory",
        expected_record_id="id",
    )

    result = await resolver.resolve(candidate, {})

    assert result.content == "bbbb"
    assert result.source == "memory_chunk"


@pytest.mark.asyncio
async def test_enumerator_discovers_accounts_users_and_resource_candidates():
    raw_agfs = FakeRawAgfs(
        {
            "/local": [{"name": "acct", "isDir": True}],
            "/local/acct/user": [{"name": "alice", "isDir": True}],
        }
    )
    tree_reader = FakeTreeReader(
        {
            (
                "acct",
                "default",
                "viking://resources",
            ): [
                {"uri": "viking://resources/docs", "isDir": True},
                {"uri": "viking://resources/docs/a.md", "isDir": False},
                {"uri": "viking://resources/docs/.abstract.md", "isDir": False},
            ],
            (
                "acct",
                "alice",
                "viking://user/alice/resources",
            ): [
                {"uri": "viking://user/alice/resources/note.md", "isDir": False},
            ],
        }
    )
    enumerator = ContentBackfillEnumerator(raw_agfs, tree_reader)

    candidates = [candidate async for candidate in enumerator.iter_candidates()]

    keys = {(c.account_id, c.owner_user_id, c.uri, c.level) for c in candidates}
    assert ("acct", "default", "viking://resources/docs", 0) in keys
    assert ("acct", "default", "viking://resources/docs", 1) in keys
    assert ("acct", "default", "viking://resources/docs/a.md", 2) in keys
    assert ("acct", "alice", "viking://user/alice/resources/note.md", 2) in keys
    assert ("acct", "default", "viking://resources/docs/.abstract.md", 2) not in keys


@pytest.mark.asyncio
async def test_enumerator_skips_internal_account_directories():
    raw_agfs = FakeRawAgfs(
        {
            "/local": [
                {"name": "_system", "isDir": True},
                {"name": "acct", "isDir": True},
            ],
        }
    )
    tree_reader = FakeTreeReader(
        {
            (
                "acct",
                "default",
                "viking://resources",
            ): [{"uri": "viking://resources/docs/a.md", "isDir": False}],
        }
    )
    enumerator = ContentBackfillEnumerator(raw_agfs, tree_reader)

    candidates = [candidate async for candidate in enumerator.iter_candidates()]

    assert {candidate.account_id for candidate in candidates} == {"acct"}


@pytest.mark.asyncio
async def test_enumerator_passes_node_limit_to_tree_reader():
    raw_agfs = FakeRawAgfs({"/local": [{"name": "acct", "isDir": True}]})
    tree_reader = FakeTreeReader(
        {
            (
                "acct",
                "default",
                "viking://resources",
            ): [{"uri": "viking://resources/docs/a.md", "isDir": False}],
        }
    )
    enumerator = ContentBackfillEnumerator(raw_agfs, tree_reader, node_limit=7)

    candidates = [candidate async for candidate in enumerator.iter_candidates()]

    assert len(candidates) == 1
    assert tree_reader.calls[0]["node_limit"] == 7


@pytest.mark.asyncio
async def test_runner_dry_run_writes_summary_without_update(tmp_path):
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="default",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="rec-1",
    )

    reader = FakeSourceReader()
    reader.text[candidate.uri] = "full content"
    collection = FakeCollection(
        {
            "rec-1": {
                "id": "rec-1",
                "content": "",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(candidate),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(run_dir=tmp_path, execute=False),
    )

    summary = await runner.run()

    assert summary.candidate_count == 1
    assert summary.updated_count == 0
    assert collection.updates == []
    summary_file = tmp_path / "summary.json"
    assert json.loads(summary_file.read_text())["candidate_count"] == 1
    assert not (tmp_path / "candidates.jsonl").exists()
    assert not (tmp_path / "state.sqlite").exists()


@pytest.mark.asyncio
async def test_runner_execute_updates_only_id_and_content(tmp_path):
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="default",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="rec-1",
    )

    reader = FakeSourceReader()
    reader.text[candidate.uri] = "full content"
    collection = FakeCollection(
        {
            "rec-1": {
                "id": "rec-1",
                "content": "",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(candidate),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(run_dir=tmp_path, execute=True),
    )

    summary = await runner.run()

    assert summary.updated_count == 1
    assert collection.updates == [{"id": "rec-1", "content": "full content"}]


@pytest.mark.asyncio
async def test_runner_truncates_content_on_utf8_boundary(tmp_path):
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="default",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="rec-1",
    )

    reader = FakeSourceReader()
    reader.text[candidate.uri] = ("a" * VIKINGDB_TEXT_FIELD_BYTE_LIMIT) + "中文"
    collection = FakeCollection(
        {
            "rec-1": {
                "id": "rec-1",
                "content": "",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(candidate),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(run_dir=tmp_path, execute=True),
    )

    await runner.run()

    content = collection.updates[0]["content"]
    assert len(content.encode("utf-8")) == VIKINGDB_TEXT_FIELD_BYTE_LIMIT


@pytest.mark.asyncio
async def test_runner_limit_caps_candidates(tmp_path):
    candidates = [
        BackfillCandidate(
            account_id="acct",
            owner_user_id="default",
            uri=f"viking://resources/docs/{idx}.md",
            level=2,
            context_type="resource",
            expected_record_id=f"rec-{idx}",
        )
        for idx in range(2)
    ]
    reader = FakeSourceReader()
    for candidate in candidates:
        reader.text[candidate.uri] = "content"
    collection = FakeCollection(
        {
            candidate.expected_record_id: {
                "id": candidate.expected_record_id,
                "content": "",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
            for candidate in candidates
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(*candidates),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(run_dir=tmp_path, execute=True, limit=1),
    )

    summary = await runner.run()

    assert summary.candidate_count == 1
    assert collection.updates == [{"id": "rec-0", "content": "content"}]


@pytest.mark.asyncio
async def test_runner_skips_record_created_after_cutoff(tmp_path):
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="default",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="rec-1",
    )
    reader = FakeSourceReader()
    reader.text[candidate.uri] = "full content"
    collection = FakeCollection(
        {
            "rec-1": {
                "id": "rec-1",
                "content": "",
                "created_at": "2026-07-06T12:00:01.000Z",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(candidate),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(
            run_dir=tmp_path,
            execute=True,
            cutoff=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
        ),
    )

    summary = await runner.run()

    assert summary.updated_count == 0
    assert summary.skipped_count == 1
    assert summary.created_after_cutoff_count == 1
    assert collection.updates == []


@pytest.mark.asyncio
async def test_runner_counts_non_empty_skip_without_detail_file(tmp_path):
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="default",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="rec-1",
    )
    reader = FakeSourceReader()
    collection = FakeCollection(
        {
            "rec-1": {
                "id": "rec-1",
                "content": "already done",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(candidate),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(run_dir=tmp_path, execute=True),
    )

    await runner.run()

    assert not (tmp_path / "state.sqlite").exists()
    assert not (tmp_path / "skipped.jsonl").exists()


@pytest.mark.asyncio
async def test_runner_fail_fast_raises_update_errors(tmp_path):
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="default",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="rec-1",
    )
    reader = FakeSourceReader()
    reader.text[candidate.uri] = "full content"
    collection = FailingCollection(
        {
            "rec-1": {
                "id": "rec-1",
                "content": "",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(candidate),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(run_dir=tmp_path, execute=True, fail_fast=True),
    )

    with pytest.raises(RuntimeError, match="update failed"):
        await runner.run()


@pytest.mark.asyncio
async def test_runner_can_record_candidates_and_skipped_details(tmp_path):
    candidate = BackfillCandidate(
        account_id="acct",
        owner_user_id="default",
        uri="viking://resources/docs/a.md",
        level=2,
        context_type="resource",
        expected_record_id="rec-1",
    )
    reader = FakeSourceReader()
    collection = FakeCollection(
        {
            "rec-1": {
                "id": "rec-1",
                "content": "already done",
                "updated_at": "2026-07-06T11:00:00.000Z",
            }
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(candidate),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(
            run_dir=tmp_path,
            execute=True,
            record_candidates=True,
            record_skipped=True,
        ),
    )

    await runner.run()

    assert (tmp_path / "candidates.jsonl").exists()
    skipped = (tmp_path / "skipped.jsonl").read_text()
    assert "content_non_empty" in skipped


@pytest.mark.asyncio
async def test_runner_progress_records_total_and_processed_counts(tmp_path):
    candidates = [
        BackfillCandidate(
            account_id="acct",
            owner_user_id="default",
            uri=f"viking://resources/docs/{idx}.md",
            level=2,
            context_type="resource",
            expected_record_id=f"rec-{idx}",
        )
        for idx in range(2)
    ]
    reader = FakeSourceReader()
    reader.text[candidates[0].uri] = "content"
    collection = FakeCollection(
        {
            "rec-0": {
                "id": "rec-0",
                "content": "",
                "updated_at": "2026-07-06T11:00:00.000Z",
            },
            "rec-1": {
                "id": "rec-1",
                "content": "already done",
                "updated_at": "2026-07-06T11:00:00.000Z",
            },
        }
    )
    runner = ContentBackfillRunner(
        enumerator=one_candidate_enumerator(*candidates),
        resolver=ContentBackfillResolver(reader),
        collection=collection,
        options=BackfillOptions(run_dir=tmp_path, execute=False),
    )

    await runner.run()

    progress = json.loads((tmp_path / "progress.json").read_text())
    assert progress["candidate_count"] == 2
    assert progress["processed_count"] == 1
    assert progress["skipped_count"] == 1
