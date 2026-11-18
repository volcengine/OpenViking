# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from dataclasses import replace

import pytest

from openviking.message import Message, TextPart
from openviking.session.midterm_memory import (
    build_midterm_records,
    deserialize_midterm_manifest,
    render_midterm_recall_record,
    render_midterm_record,
    serialize_midterm_manifest,
)


def _message(
    index: int,
    text: str,
    *,
    speaker: str | None = None,
    role: str = "user",
    peer_id: str = "conv-26",
) -> Message:
    return Message(
        id=f"msg-{index}",
        role=role,
        parts=[TextPart(text)],
        peer_id=peer_id,
        speaker=speaker,
        created_at=f"2023-04-02T09:36:{index:02d}+00:00",
    )


def test_builds_pairs_from_consecutive_structured_speaker_blocks():
    records = build_midterm_records(
        [
            _message(1, "I am going to Paris.", speaker="Alice"),
            _message(2, "The ticket is booked.", speaker="Alice"),
            _message(3, "Is it for the exhibition?", speaker="Bob"),
            _message(4, "Do you need a hotel?", speaker="Bob"),
            _message(5, "Yes, please.", speaker="Alice"),
        ],
        session_id="session-1",
        archive_uri="viking://session/archive_001",
    )

    assert [record.kind for record in records] == ["dialogue_pair", "single_turn"]
    assert records[0].blocks[0].speaker == "Alice"
    assert records[0].blocks[0].texts == (
        "I am going to Paris.",
        "The ticket is booked.",
    )
    assert records[0].blocks[1].speaker == "Bob"
    assert records[1].source_message_ids == ("msg-5",)
    assert records[0].filename == "pair_0001.md"
    assert records[1].filename == "single_turn_0002.md"


def test_legacy_locomo_prefix_is_only_used_when_multiple_speakers_are_present():
    records = build_midterm_records(
        [
            _message(1, "Caroline: I joined the support group."),
            _message(2, "Melanie: I am proud of you."),
        ],
        session_id="session-1",
        archive_uri="viking://session/archive_001",
    )

    assert [block.speaker for block in records[0].blocks] == ["Caroline", "Melanie"]
    assert records[0].blocks[0].texts == ("I joined the support group.",)


def test_manifest_round_trip_preserves_dialogue_evidence():
    records = build_midterm_records(
        [
            _message(1, "Question", speaker="Alice"),
            _message(2, "Answer", speaker="Bob"),
        ],
        session_id="session-1",
        archive_uri="viking://session/archive_001",
    )

    persisted = deserialize_midterm_manifest(serialize_midterm_manifest(records))
    assert persisted == records
    assert persisted[0].converted_to_long_term is False
    assert persisted[0].converted_at is None
    rendered = render_midterm_record(persisted[0])
    assert "Content-Hash:" in rendered
    assert rendered.count("Timestamp:") == 1
    assert "Timestamp: 2023-04-02T09:36:01+00:00" in rendered
    assert "Converted-To-Long-Term: false" in rendered


def test_content_hash_and_filename_are_deterministic():
    messages = [
        _message(1, "Question", speaker="Alice"),
        _message(2, "Answer", speaker="Bob"),
    ]
    first = build_midterm_records(
        messages,
        session_id="session-1",
        archive_uri="viking://session/archive_001",
    )[0]
    second = build_midterm_records(
        [replace(message, id=f"rerun-{message.id}") for message in messages],
        session_id="session-1",
        archive_uri="viking://session/archive_001",
    )[0]

    assert first.content_hash == second.content_hash
    assert first.filename == second.filename
    assert first.recall_filename == second.recall_filename
    assert first.peer_ids == ("conv-26",)
    first_recall = render_midterm_recall_record(first)
    second_recall = render_midterm_recall_record(second)
    assert first_recall == second_recall
    assert "session-1" not in first_recall
    assert "archive_001" not in first_recall
    assert first_recall.count("Timestamp:") == 1
    assert "Timestamp: 2023-04-02T09:36:01+00:00" in first_recall


class _MemoryFS:
    def __init__(self):
        self.files = {}

    async def exists(self, uri, **kwargs):
        return uri in self.files or any(key.startswith(uri + "/") for key in self.files)

    async def mkdir(self, *args, **kwargs):
        pass

    async def read_file(self, uri, **kwargs):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, **kwargs):
        self.files[uri] = content

    async def rm(self, uri, recursive=False, **kwargs):
        for key in list(self.files):
            if key == uri or (recursive and key.startswith(uri + "/")):
                del self.files[key]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "converted",
        "no_output",
        "enqueue_failure",
        "embedding_failure",
        "longterm_embedding_failure",
        "longterm_failure",
        "deferred",
    ],
)
async def test_midterm_embedding_lifecycle_without_native_backend(monkeypatch, outcome):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openviking.session.session import Session
    from openviking_cli.utils.config.memory_config import MemoryConfig

    # Only external storage, queue execution, and LLM output are substituted.
    # Staging, rendering, embedding-message creation, Phase 2 ordering, and
    # conversion cleanup all run through their real implementation.
    fs = _MemoryFS()
    queued = []
    meta = {}
    queue_started, finish_queue = asyncio.Event(), asyncio.Event()
    ready = asyncio.Event()

    async def enqueue(message):
        if outcome == "enqueue_failure":
            return False
        queued.append(message)
        queue_started.set()
        return True

    async def wait_for_work(*args, **kwargs):
        assert kwargs["exclude_work_id"] in {"current-commit-work", None}
        await finish_queue.wait()

    async def update_stage(task_id, stage, **kwargs):
        meta.update(kwargs.get("meta") or {})
        ready.set()

    tracker = SimpleNamespace(
        start=AsyncMock(),
        fail=AsyncMock(),
        complete=AsyncMock(),
        update_stage=update_stage,
        wait_for_task_work=wait_for_work,
        get=AsyncMock(return_value=SimpleNamespace(meta={})),
        build_queue_status=AsyncMock(
            side_effect=lambda *args, **kwargs: {
                "Embedding": {
                    "error_count": int(
                        outcome == "embedding_failure"
                        or (outcome == "longterm_embedding_failure" and ready.is_set())
                    )
                }
            }
        ),
    )
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    config = SimpleNamespace(memory=MemoryConfig(topic_continuity={"enabled": True}))
    for name in [
        "openviking.session.session.get_openviking_config",
        "openviking_cli.utils.config.get_openviking_config",
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
    ]:
        monkeypatch.setattr(name, lambda: config)
    monkeypatch.setattr(
        "openviking.session.memory.memory_type_registry.get_default_registry",
        lambda: SimpleNamespace(get=lambda name: None, list_names=lambda **kwargs: ["events"]),
    )

    async def extract(*, messages, **kwargs):
        assert ready.is_set(), "long-term extraction started before midterm readiness"
        assert all(not message.id.startswith("midterm_") for message in messages)
        assert queued and all("/midterm_qa/" in msg.context_data["uri"] for msg in queued)
        if outcome == "longterm_failure":
            raise ValueError("long-term extraction failed")
        return (
            [SimpleNamespace(category="events")]
            if outcome in {"converted", "longterm_embedding_failure"}
            else []
        )

    compressor = SimpleNamespace(extract_long_term_memories=AsyncMock(side_effect=extract))
    session = Session(viking_fs=None, session_compressor=compressor)
    session._viking_fs = fs
    session._vikingdb_manager = SimpleNamespace(
        has_queue_manager=True, enqueue_embedding_msg=enqueue
    )
    messages = [_message(1, "Question", speaker="Alice"), _message(2, "Answer", speaker="Bob")]
    archive = session._session_uri + "/history/archive_001"
    session._prepare_phase2_archive_messages = AsyncMock(return_value=(messages, 1, 1, [], {}))
    session._archives.read_meta = AsyncMock(side_effect=lambda *args: dict(meta))

    async def merge_meta(uri, updates, **kwargs):
        meta.update(updates)

    session._merge_archive_meta = merge_meta
    session._merge_and_save_commit_meta = AsyncMock(return_value=None)
    session._write_done_file = AsyncMock()
    session._tool_outputs.hydrate_for_extraction = AsyncMock(return_value=messages)
    session._run_usage_reporting = AsyncMock(return_value=[])
    from openviking.service.task_work_index import bind_task_context

    with bind_task_context(
        "midterm-lifecycle",
        session.ctx.account_id,
        session.ctx.user.user_id,
        work_id="current-commit-work",
    ):
        run = asyncio.create_task(
            session._run_memory_extraction(
                task_id="midterm-lifecycle",
                archive_uri=archive,
                messages=messages,
                first_message_id=messages[0].id,
                last_message_id=messages[-1].id,
                memory_policy={"working_memory": {"enabled": False}},
                defer_long_term_extraction=outcome == "deferred",
            )
        )
    try:
        if outcome != "enqueue_failure":
            await asyncio.wait_for(queue_started.wait(), 3)
            await asyncio.sleep(0)
            assert not ready.is_set()
            compressor.extract_long_term_memories.assert_not_awaited()
            assert "midterm_memory_ready" not in meta
            assert "Question" in str(queued[0].message)
        finish_queue.set()
        processed = await asyncio.wait_for(run, 3)
    finally:
        if not run.done():
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    if outcome in {"enqueue_failure", "embedding_failure"}:
        tracker.fail.assert_awaited_once()
        assert not ready.is_set()
        compressor.extract_long_term_memories.assert_not_awaited()
        return
    assert meta["midterm_memory_ready"]["ready"] is True
    if outcome == "deferred":
        assert processed is False
        compressor.extract_long_term_memories.assert_not_awaited()
    elif outcome in {"longterm_failure", "longterm_embedding_failure"}:
        tracker.fail.assert_awaited_once()
    else:
        tracker.complete.assert_awaited_once()
    manifest = archive + "/midterm_memory/manifest.json"
    recall = queued[0].context_data["uri"]
    assert await fs.exists(manifest) is (outcome != "converted")
    assert await fs.exists(recall) is (outcome != "converted")

    if outcome == "deferred":
        # A recovered queue delivery must reuse the searchable midterm vectors.
        tracker.get.return_value.meta["long_term_release_requested"] = True
        assert (
            await session._run_memory_extraction(
                task_id="midterm-lifecycle",
                archive_uri=archive,
                messages=messages,
                first_message_id=messages[0].id,
                last_message_id=messages[-1].id,
                memory_policy={"working_memory": {"enabled": False}},
                defer_long_term_extraction=True,
            )
            is True
        )
        tracker.complete.assert_awaited_once()
        compressor.extract_long_term_memories.assert_awaited_once()
        assert len(queued) == 1
