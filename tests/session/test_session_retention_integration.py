# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from openviking import AsyncOpenViking
from openviking.message import Message, TextPart, ToolPart
from openviking.models.vlm.base import ToolCall, VLMResponse
from openviking.service.task_tracker import get_task_tracker
from openviking.session.session import Session, _ArchiveSummaryResult, _CheckpointRequest


async def _write_archive(
    session,
    index: int,
    messages: list[Message],
    *,
    overview: str = "",
    done: dict | None = None,
    failed: bool = False,
    meta: dict | None = None,
) -> str:
    archive_id = f"archive_{index:03d}"
    archive_uri = f"{session.uri}/history/{archive_id}"
    await session._viking_fs.write_file(
        f"{archive_uri}/messages.jsonl",
        "\n".join(message.to_jsonl() for message in messages) + "\n",
        ctx=session.ctx,
    )
    if overview:
        await session._viking_fs.write_file(
            f"{archive_uri}/.overview.md", overview, ctx=session.ctx
        )
    if meta is not None:
        await session._viking_fs.write_file(
            f"{archive_uri}/.meta.json",
            json.dumps(meta),
            ctx=session.ctx,
        )
    if failed:
        await session._viking_fs.write_file(
            f"{archive_uri}/.failed.json",
            json.dumps({"error": "synthetic failure"}),
            ctx=session.ctx,
        )
    if done is not None:
        await session._viking_fs.write_file(
            f"{archive_uri}/.done",
            json.dumps(done),
            ctx=session.ctx,
        )
    return archive_uri


def _text_message(message_id: str, role: str, text: str) -> Message:
    return Message(id=message_id, role=role, parts=[TextPart(text)])


def test_checkpoint_record_respects_remaining_retained_budget():
    records = Session._build_checkpoint_records(
        [
            _CheckpointRequest(
                turn_anchor_message_id="u1",
                source_message_ids=("a1",),
                retained_message_token_budget=20,
                estimated_active_tokens=15,
            )
        ],
        ("A" * 400,),
    )

    assert records[0]["estimated_tokens"] <= 5


async def test_two_pending_archives_are_visible_independent_of_commit_count(
    client: AsyncOpenViking,
):
    session = client.session(session_id="two_pending_directory_state_test")
    await session.ensure_exists()
    await _write_archive(session, 1, [_text_message("u1", "user", "one")])
    await _write_archive(session, 2, [_text_message("u2", "user", "two")])
    session.meta.commit_count = 99
    await session._save_meta()

    context = await session.get_session_context()

    assert [message["id"] for message in context["messages"]] == ["u1", "u2"]
    assert context["latest_archive_overview"] == ""


async def test_session_context_enforces_hard_budget_without_mutating_archive_raw(
    client: AsyncOpenViking,
):
    session = client.session(session_id="session_context_hard_budget_test")
    await session.ensure_exists()
    archived = _text_message("u1", "user", "A" * 4000)
    await _write_archive(session, 1, [archived])
    live = _text_message("a1", "assistant", "B" * 4000)
    session._messages = [live]
    await session._write_to_agfs_async(messages=[live])

    context = await session.get_session_context(token_budget=100)
    raw = await session._read_archive_messages(f"{session.uri}/history/archive_001")

    assert context["estimatedTokens"] <= 100
    assert context["stats"]["activeTokens"] <= 100
    assert raw[0].content == "A" * 4000
    assert live.content == "B" * 4000


async def test_failed_archive_returns_raw_but_done_without_overview_stays_archived(
    client: AsyncOpenViking,
):
    session = client.session(session_id="failed_and_wm_disabled_archive_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "failed raw")],
        failed=True,
    )
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "completed without overview")],
        done={"starting_message_id": "u2", "ending_message_id": "u2"},
    )

    context = await session.get_session_context()

    assert [message["id"] for message in context["messages"]] == ["u1"]
    assert context["stats"]["failedArchives"] == 1


async def test_done_with_missing_required_overview_remains_uncovered(
    client: AsyncOpenViking,
):
    session = client.session(session_id="missing_required_overview_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "required overview raw")],
        done={
            "starting_message_id": "u1",
            "ending_message_id": "u1",
            "working_memory_enabled": True,
        },
    )

    context = await session.get_session_context()

    assert [message["id"] for message in context["messages"]] == ["u1"]
    assert context["stats"]["failedArchives"] == 1


async def test_legacy_done_marker_covers_only_its_own_archive(
    client: AsyncOpenViking,
):
    session = client.session(session_id="legacy_done_self_coverage_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "failed raw remains active")],
        failed=True,
    )
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "legacy completed raw")],
        overview="# Session Summary\n\nlegacy archive two only",
        done={"starting_message_id": "u2", "ending_message_id": "u2"},
    )

    context = await session.get_session_context()

    assert context["latest_archive_overview"].endswith("legacy archive two only")
    assert [message["id"] for message in context["messages"]] == ["u1"]


async def test_coverage_metadata_cannot_hide_a_pending_archive(
    client: AsyncOpenViking,
):
    session = client.session(session_id="pending_not_explicitly_covered_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "still pending")],
    )
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "completed two")],
        overview="# Session Summary\n\narchive two",
        done={
            "starting_message_id": "u2",
            "ending_message_id": "u2",
            "coverage_start_archive": "archive_001",
            "coverage_end_archive": "archive_002",
            "covered_failed_archives": ["archive_001"],
        },
    )

    context = await session.get_session_context()

    assert [message["id"] for message in context["messages"]] == ["u1"]


async def test_later_coverage_absorbs_failed_raw_and_stable_deduplicates_root(
    client: AsyncOpenViking,
):
    session = client.session(session_id="failed_coverage_roll_forward_test")
    await session.ensure_exists()
    duplicate = _text_message("u1", "user", "failed raw")
    await _write_archive(session, 1, [duplicate], failed=True)
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "current raw")],
        overview="# Session Summary\n\ncovered one and two",
        done={
            "starting_message_id": "u1",
            "ending_message_id": "u2",
            "coverage_start_archive": "archive_001",
            "coverage_end_archive": "archive_002",
            "covered_failed_archives": ["archive_001"],
        },
    )
    session._messages = [duplicate]
    await session._write_to_agfs_async(messages=session._messages)

    context = await session.get_session_context()

    assert context["latest_archive_overview"] == "# Session Summary\n\ncovered one and two"
    assert [message["id"] for message in context["messages"]] == ["u1"]


async def test_phase2_replays_failed_but_not_completed_without_overview(
    client: AsyncOpenViking,
):
    session = client.session(session_id="phase2_failed_replay_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "failed one")],
        failed=True,
    )
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "completed without overview")],
        done={"starting_message_id": "u2", "ending_message_id": "u2"},
    )
    current_uri = await _write_archive(
        session,
        3,
        [_text_message("u3", "user", "current three")],
    )

    messages, start, end, failed, completed_steps = await session._prepare_phase2_archive_messages(
        current_uri,
        [_text_message("u3", "user", "current three")],
    )

    assert [message.id for message in messages] == ["u1", "u3"]
    assert start == "archive_001"
    assert end == "archive_003"
    assert failed == ["archive_001"]
    assert completed_steps == {}


async def test_phase2_never_replays_an_earlier_pending_archive(
    client: AsyncOpenViking,
):
    session = client.session(session_id="phase2_pending_not_replayed_test")
    await session.ensure_exists()
    await _write_archive(session, 1, [_text_message("u1", "user", "pending one")])
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "failed two")],
        failed=True,
    )
    current_uri = await _write_archive(
        session,
        3,
        [_text_message("u3", "user", "current three")],
    )

    messages, start, end, failed, _completed_steps = await session._prepare_phase2_archive_messages(
        current_uri,
        [_text_message("u3", "user", "current three")],
    )

    assert [message.id for message in messages] == ["u2", "u3"]
    assert start == "archive_002"
    assert end == "archive_003"
    assert failed == ["archive_002"]


async def test_phase2_waits_for_all_earlier_pending_archives(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="phase2_wait_all_pending_test")
    await session.ensure_exists()
    first_uri = await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "pending one")],
    )
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "completed two")],
        overview="# Summary\n\ncomplete",
        done={"starting_message_id": "u2", "ending_message_id": "u2"},
    )
    monkeypatch.setattr("openviking.session.session._ARCHIVE_WAIT_POLL_SECONDS", 0.01)

    waiter = asyncio.create_task(session._wait_for_previous_archive_done(3))
    await asyncio.sleep(0.03)
    assert not waiter.done()

    await session._viking_fs.write_file(
        f"{first_uri}/.failed.json",
        json.dumps({"error": "synthetic terminal failure"}),
        ctx=session.ctx,
    )
    assert await asyncio.wait_for(waiter, timeout=0.5)


async def test_missing_previous_archive_directory_does_not_block_phase2(
    client: AsyncOpenViking,
):
    session = client.session(session_id="missing_previous_archive_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "archive two")],
    )

    assert await asyncio.wait_for(
        session._wait_for_previous_archive_done(2),
        timeout=0.5,
    )


async def test_phase2_roll_forward_writes_coverage_and_calls_existing_summary_once(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="phase2_roll_forward_end_to_end_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "failed one")],
        failed=True,
    )
    current = _text_message("u2", "user", "current two")
    current_uri = await _write_archive(session, 2, [current])
    seen_message_ids: list[list[str]] = []

    async def fake_summary(messages, latest_archive_overview=""):
        assert latest_archive_overview == ""
        seen_message_ids.append([message.id for message in messages])
        return "# Session Summary\n\n## Current State\nBoth rounds are covered."

    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=False,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    monkeypatch.setattr(session, "_generate_archive_summary_async", fake_summary)

    task_id = str(uuid4())
    await get_task_tracker().create(
        "session_commit",
        resource_id=session.session_id,
        account_id=session.ctx.account_id,
        user_id=session.ctx.user.user_id,
        task_id=task_id,
    )
    await session._run_memory_extraction(
        task_id=task_id,
        archive_uri=current_uri,
        messages=[current],
        usage_records=[],
        first_message_id=current.id,
        last_message_id=current.id,
        memory_policy={"working_memory": {"enabled": True}},
    )

    done = json.loads(await session._viking_fs.read_file(f"{current_uri}/.done", ctx=session.ctx))
    context = await session.get_session_context()
    assert seen_message_ids == [["u1", "u2"]]
    assert done["coverage_start_archive"] == "archive_001"
    assert done["coverage_end_archive"] == "archive_002"
    assert done["covered_failed_archives"] == ["archive_001"]
    assert context["latest_archive_overview"].endswith("Both rounds are covered.")
    assert context["messages"] == []


async def test_phase2_rolls_forward_done_archive_with_missing_required_overview(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="invalid_done_roll_forward_test")
    await session.ensure_exists()
    await _write_archive(
        session,
        1,
        [_text_message("u1", "user", "raw from invalid completed archive")],
        done={
            "starting_message_id": "u1",
            "ending_message_id": "u1",
            "working_memory_enabled": True,
        },
    )
    current = _text_message("u2", "user", "current archive")
    current_uri = await _write_archive(session, 2, [current])
    seen_message_ids: list[list[str]] = []

    async def fake_summary(messages, latest_archive_overview=""):
        assert latest_archive_overview == ""
        seen_message_ids.append([message.id for message in messages])
        return "# Session Summary\n\n## Current State\nInvalid archive was recovered."

    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=False,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    monkeypatch.setattr(session, "_generate_archive_summary_async", fake_summary)

    task_id = str(uuid4())
    await get_task_tracker().create(
        "session_commit",
        resource_id=session.session_id,
        account_id=session.ctx.account_id,
        user_id=session.ctx.user.user_id,
        task_id=task_id,
    )
    await session._run_memory_extraction(
        task_id=task_id,
        archive_uri=current_uri,
        messages=[current],
        usage_records=[],
        first_message_id=current.id,
        last_message_id=current.id,
        memory_policy={"working_memory": {"enabled": True}},
    )

    done = json.loads(await session._viking_fs.read_file(f"{current_uri}/.done", ctx=session.ctx))
    context = await session.get_session_context()
    assert seen_message_ids == [["u1", "u2"]]
    assert done["coverage_start_archive"] == "archive_001"
    assert done["covered_failed_archives"] == ["archive_001"]
    assert context["latest_archive_overview"].endswith("Invalid archive was recovered.")
    assert context["messages"] == []


async def test_roll_forward_does_not_repeat_completed_memory_step_messages(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="phase2_memory_step_idempotency_test")
    await session.ensure_exists()
    first = _text_message("u1", "user", "already extracted")
    await _write_archive(
        session,
        1,
        [first],
        failed=True,
        meta={"completed_memory_steps": {"long_term": ["u1"]}},
    )
    current = _text_message("u2", "user", "extract this once")
    current_uri = await _write_archive(session, 2, [current])
    summary_inputs: list[list[str]] = []
    long_term_inputs: list[list[str]] = []

    async def fake_summary(messages, latest_archive_overview=""):
        assert latest_archive_overview == ""
        summary_inputs.append([message.id for message in messages])
        return "# Session Summary\n\n## Current State\nBoth messages are summarized."

    async def fake_long_term(*, messages, **_kwargs):
        long_term_inputs.append([message.id for message in messages])
        return []

    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=True,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    monkeypatch.setattr(session, "_generate_archive_summary_async", fake_summary)
    monkeypatch.setattr(
        session._session_compressor,
        "extract_long_term_memories",
        fake_long_term,
    )

    task_id = str(uuid4())
    await get_task_tracker().create(
        "session_commit",
        resource_id=session.session_id,
        account_id=session.ctx.account_id,
        user_id=session.ctx.user.user_id,
        task_id=task_id,
    )
    await session._run_memory_extraction(
        task_id=task_id,
        archive_uri=current_uri,
        messages=[current],
        usage_records=[],
        first_message_id=current.id,
        last_message_id=current.id,
        memory_policy={
            "self": {"enabled": True},
            "peer": {"enabled": False},
            "memory_types": ["profile"],
            "working_memory": {"enabled": True},
        },
    )

    done = json.loads(await session._viking_fs.read_file(f"{current_uri}/.done", ctx=session.ctx))
    assert summary_inputs == [["u1", "u2"]]
    assert long_term_inputs == [["u2"]]
    assert done["completed_memory_steps"]["long_term"] == ["u1", "u2"]


async def test_completed_partial_turn_inserts_checkpoint_after_user_anchor(
    client: AsyncOpenViking,
):
    session = client.session(session_id="partial_turn_checkpoint_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "investigate the outage")
    early = _text_message("a1", "assistant", "checking the first signal")
    tail = _text_message("a2", "assistant", "the latest raw step")
    overview = """# Session Summary

## Current State
Connection pool exhaustion is confirmed.

## Key Facts & Decisions
- Keep the user query as the Turn anchor.

## Open Issues
- Validate the recycle configuration.
"""
    archive_uri = await _write_archive(
        session,
        1,
        [anchor, early],
        overview=overview,
        meta={
            "retention_plan": {
                "mode": "turn_budget",
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["a1"],
                "raw_tail_start_message_id": "a2",
                "retained_message_token_budget": 12_000,
            },
            "checkpoints": [
                {
                    "turn_anchor_message_id": "u1",
                    "source_message_ids": ["a1"],
                    "abstract": "Checked the first signal and found pool saturation.",
                    "estimated_tokens": 12,
                }
            ],
        },
        done={
            "starting_message_id": "u1",
            "ending_message_id": "a1",
            "coverage_start_archive": "archive_001",
            "coverage_end_archive": "archive_001",
            "covered_failed_archives": [],
        },
    )
    session._messages = [anchor, tail]
    await session._write_to_agfs_async(messages=session._messages)
    # The retained-message budget belongs to commit planning. Context reads
    # must use their own request budget instead of silently reusing this value.
    session.meta.retention_mode = "turn_budget"
    session.meta.retained_message_token_budget = 1

    context = await session.get_session_context(token_budget=128_000)

    assert [message["id"] for message in context["messages"]] == [
        "u1",
        "checkpoint_archive_001_u1",
        "a2",
    ]
    checkpoint = context["messages"][1]
    assert checkpoint["message_kind"] == "checkpoint"
    assert "peer_id" not in checkpoint
    assert checkpoint["source_message_ids"] == ["a1"]
    assert checkpoint["parts"][0]["uri"] == archive_uri
    assert checkpoint["parts"][0]["abstract"] == (
        "Checked the first signal and found pool saturation."
    )


async def test_legacy_partial_turn_does_not_derive_checkpoint_from_overview(
    client: AsyncOpenViking,
):
    session = client.session(session_id="legacy_partial_turn_without_checkpoint_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "investigate the outage")
    early = _text_message("a1", "assistant", "checking the first signal")
    tail = _text_message("a2", "assistant", "the latest raw step")
    await _write_archive(
        session,
        1,
        [anchor, early],
        overview="# Working Memory\n\n## Current State\nPool exhaustion was found.",
        meta={
            "retention_plan": {
                "mode": "turn_budget",
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["a1"],
                "retained_message_token_budget": 12_000,
            }
        },
        done={"starting_message_id": "u1", "ending_message_id": "a1"},
    )
    session._messages = [anchor, tail]
    await session._write_to_agfs_async(messages=session._messages)

    context = await session.get_session_context()

    assert [message["id"] for message in context["messages"]] == ["u1", "a2"]


async def test_phase2_persists_checkpoint_from_same_summary_call(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="phase2_checkpoint_product_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "investigate the outage")
    early = _text_message("a1", "assistant", "checking the first signal")
    tail = _text_message("a2", "assistant", "the latest raw step")
    archive_uri = await _write_archive(
        session,
        1,
        [anchor, early],
        meta={
            "retention_plan": {
                "mode": "turn_budget",
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["a1"],
                "raw_tail_start_message_id": "a2",
                "retained_message_token_budget": 6000,
                "estimated_active_tokens": 100,
            }
        },
    )
    session._messages = [anchor, tail]
    await session._write_to_agfs_async(messages=session._messages)
    calls = 0

    async def fake_summary(
        messages,
        latest_archive_overview="",
        checkpoint_requests=None,
    ):
        nonlocal calls
        calls += 1
        assert latest_archive_overview == ""
        assert [message.id for message in messages] == ["u1", "a1"]
        assert checkpoint_requests is not None
        assert len(checkpoint_requests) == 1
        assert checkpoint_requests[0].turn_anchor_message_id == "u1"
        assert checkpoint_requests[0].source_message_ids == ("a1",)
        return _ArchiveSummaryResult(
            overview="# Working Memory\n\n## Current State\nPool saturation confirmed.",
            checkpoint_summaries=("Checked the first signal; pool saturation was confirmed.",),
        )

    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=False,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    monkeypatch.setattr(session, "_generate_archive_summary_async", fake_summary)

    task_id = str(uuid4())
    await get_task_tracker().create(
        "session_commit",
        resource_id=session.session_id,
        account_id=session.ctx.account_id,
        user_id=session.ctx.user.user_id,
        task_id=task_id,
    )
    await session._run_memory_extraction(
        task_id=task_id,
        archive_uri=archive_uri,
        messages=[anchor, early],
        usage_records=[],
        first_message_id="u1",
        last_message_id="a1",
        memory_policy={"working_memory": {"enabled": True}},
    )

    meta = json.loads(
        await session._viking_fs.read_file(f"{archive_uri}/.meta.json", ctx=session.ctx)
    )
    context = await session.get_session_context()
    assert calls == 1
    assert meta["checkpoints"][0]["turn_anchor_message_id"] == "u1"
    assert meta["checkpoints"][0]["source_message_ids"] == ["a1"]
    assert meta["checkpoints"][0]["abstract"] == (
        "Checked the first signal; pool saturation was confirmed."
    )
    assert meta["checkpoints"][0]["estimated_tokens"] > 0
    assert [message["id"] for message in context["messages"]] == [
        "u1",
        "checkpoint_archive_001_u1",
        "a2",
    ]


async def test_wm_creation_returns_two_products_in_one_model_call(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="single_call_checkpoint_generation_test")
    calls: list[dict] = []

    class FakeVLM:
        @staticmethod
        def is_available() -> bool:
            return True

        async def get_completion_async(self, **kwargs):
            calls.append(kwargs)
            return VLMResponse(
                tool_calls=[
                    ToolCall(
                        id="tool-call-1",
                        name="create_working_memory",
                        arguments={
                            "working_memory": (
                                "# Working Memory\n\n## Current State\nInvestigation continues."
                            ),
                            "checkpoint_summaries": [
                                "Queried the service and found pool saturation."
                            ],
                        },
                    )
                ],
                finish_reason="tool_calls",
            )

    vlm = FakeVLM()
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(vlm=vlm),
    )
    anchor = _text_message("secret-anchor-id", "user", "investigate the outage")
    source = _text_message("secret-source-id", "assistant", "queried the service")
    result = await session._generate_archive_summary_async(
        [anchor, source],
        checkpoint_requests=[
            _CheckpointRequest(
                turn_anchor_message_id=anchor.id,
                source_message_ids=(source.id,),
                retained_message_token_budget=6000,
                estimated_active_tokens=100,
            )
        ],
    )

    assert isinstance(result, _ArchiveSummaryResult)
    assert result.checkpoint_summaries == ("Queried the service and found pool saturation.",)
    assert len(calls) == 1
    assert calls[0]["tools"][0]["function"]["name"] == "create_working_memory"
    assert '<checkpoint_source index="0">' in calls[0]["prompt"]
    assert calls[0]["prompt"].count("queried the service") == 1
    assert "secret-anchor-id" not in calls[0]["prompt"]
    assert "secret-source-id" not in calls[0]["prompt"]


async def test_wm_update_returns_two_products_in_one_model_call(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="single_call_checkpoint_update_test")
    calls: list[dict] = []

    class FakeVLM:
        @staticmethod
        def is_available() -> bool:
            return True

        async def get_completion_async(self, **kwargs):
            calls.append(kwargs)
            return VLMResponse(
                tool_calls=[
                    ToolCall(
                        id="tool-call-1",
                        name="update_working_memory",
                        arguments={
                            "sections": {
                                name: {"op": "KEEP"}
                                for name in (
                                    "Session Title",
                                    "Current State",
                                    "Task & Goals",
                                    "Key Facts & Decisions",
                                    "Files & Context",
                                    "Errors & Corrections",
                                    "Open Issues",
                                )
                            },
                            "checkpoint_summaries": [
                                "Compared logs and ruled out the network path."
                            ],
                        },
                    )
                ],
                finish_reason="tool_calls",
            )

    vlm = FakeVLM()
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(vlm=vlm),
    )
    source = _text_message("source-id", "assistant", "compared the logs")
    prior = """# Working Memory

## Session Title
Outage investigation

## Current State
Investigation continues.

## Task & Goals
Find the outage cause.

## Key Facts & Decisions
None.

## Files & Context
None.

## Errors & Corrections
None.

## Open Issues
Confirm the database state.
"""
    result = await session._generate_archive_summary_async(
        [source],
        latest_archive_overview=prior,
        checkpoint_requests=[
            _CheckpointRequest(
                turn_anchor_message_id="anchor-id",
                source_message_ids=(source.id,),
                retained_message_token_budget=6000,
                estimated_active_tokens=100,
            )
        ],
    )

    assert isinstance(result, _ArchiveSummaryResult)
    assert result.checkpoint_summaries == ("Compared logs and ruled out the network path.",)
    assert "Investigation continues." in result.overview
    assert len(calls) == 1
    assert calls[0]["tools"][0]["function"]["name"] == "update_working_memory"
    assert "checkpoint_summaries" in calls[0]["prompt"]


async def test_roll_forward_collects_multiple_checkpoint_requests(
    client: AsyncOpenViking,
):
    session = client.session(session_id="multiple_roll_forward_checkpoint_test")
    await session.ensure_exists()
    first_anchor = _text_message("u1", "user", "first investigation")
    first_source = _text_message("a1", "assistant", "first archived step")
    second_anchor = _text_message("u2", "user", "second investigation")
    second_source = _text_message("a2", "assistant", "second archived step")
    await _write_archive(
        session,
        1,
        [first_anchor, first_source],
        failed=True,
        meta={
            "retention_plan": {
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["a1"],
                "retained_message_token_budget": 6000,
                "estimated_active_tokens": 100,
            }
        },
    )
    await _write_archive(
        session,
        2,
        [second_anchor, second_source],
        failed=True,
        meta={
            "retention_plan": {
                "partial_turn": True,
                "turn_anchor_message_id": "u2",
                "checkpoint_source_message_ids": ["a2"],
                "retained_message_token_budget": 6000,
                "estimated_active_tokens": 120,
            }
        },
    )
    current = _text_message("u3", "user", "current work")
    current_uri = await _write_archive(session, 3, [current], meta={})
    combined = [first_anchor, first_source, second_anchor, second_source, current]

    requests = await session._collect_checkpoint_requests_for_phase2(
        current_uri,
        ["archive_001", "archive_002"],
        combined,
    )
    formatted = session._format_messages_for_wm(combined, requests)

    assert [request.turn_anchor_message_id for request in requests] == ["u1", "u2"]
    assert [request.source_message_ids for request in requests] == [("a1",), ("a2",)]
    assert formatted.count('<checkpoint_source index="0">') == 1
    assert formatted.count('<checkpoint_source index="1">') == 1


async def test_checkpoint_request_rejects_user_or_cross_turn_sources(
    client: AsyncOpenViking,
):
    session = client.session(session_id="invalid_checkpoint_source_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "first query")
    assistant = _text_message("a1", "assistant", "first response")
    archive_uri = await _write_archive(
        session,
        1,
        [anchor, assistant],
        meta={
            "retention_plan": {
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["u1"],
                "retained_message_token_budget": 6000,
                "estimated_active_tokens": 100,
            }
        },
    )

    with pytest.raises(ValueError, match="outside its Assistant/Tool prefix"):
        await session._collect_checkpoint_requests_for_phase2(
            archive_uri,
            [],
            [anchor, assistant],
        )


async def test_missing_required_checkpoint_keeps_archive_raw_uncovered(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="missing_required_checkpoint_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "investigate the outage")
    early = _text_message("a1", "assistant", "checking the first signal")
    tail = _text_message("a2", "assistant", "the latest raw step")
    archive_uri = await _write_archive(
        session,
        1,
        [anchor, early],
        meta={
            "retention_plan": {
                "mode": "turn_budget",
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["a1"],
                "retained_message_token_budget": 6000,
                "estimated_active_tokens": 100,
            }
        },
    )
    session._messages = [anchor, tail]
    await session._write_to_agfs_async(messages=session._messages)

    async def fake_summary(*_args, **_kwargs):
        # Simulate an old/malformed implementation that returns only overview.
        return "# Working Memory\n\n## Current State\nPool saturation confirmed."

    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=False,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    monkeypatch.setattr(session, "_generate_archive_summary_async", fake_summary)

    task_id = str(uuid4())
    await get_task_tracker().create(
        "session_commit",
        resource_id=session.session_id,
        account_id=session.ctx.account_id,
        user_id=session.ctx.user.user_id,
        task_id=task_id,
    )
    await session._run_memory_extraction(
        task_id=task_id,
        archive_uri=archive_uri,
        messages=[anchor, early],
        usage_records=[],
        first_message_id="u1",
        last_message_id="a1",
        memory_policy={"working_memory": {"enabled": True}},
    )

    assert not await session._viking_fs.exists(f"{archive_uri}/.done", ctx=session.ctx)
    assert await session._viking_fs.exists(f"{archive_uri}/.failed.json", ctx=session.ctx)
    context = await session.get_session_context()
    assert [message["id"] for message in context["messages"]] == ["u1", "a1", "a2"]
    assert not any(message.get("message_kind") == "checkpoint" for message in context["messages"])


async def test_working_memory_disabled_does_not_generate_or_restore_checkpoint(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="wm_disabled_partial_checkpoint_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "investigate the outage")
    early = _text_message("a1", "assistant", "checking the first signal")
    tail = _text_message("a2", "assistant", "the latest raw step")
    archive_uri = await _write_archive(
        session,
        1,
        [anchor, early],
        meta={
            "retention_plan": {
                "mode": "turn_budget",
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["a1"],
                "retained_message_token_budget": 6000,
                "estimated_active_tokens": 100,
            }
        },
    )
    session._messages = [anchor, tail]
    await session._write_to_agfs_async(messages=session._messages)

    async def unexpected_summary(*_args, **_kwargs):
        raise AssertionError("Working Memory generation must stay disabled")

    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=False,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    monkeypatch.setattr(session, "_generate_archive_summary_async", unexpected_summary)

    task_id = str(uuid4())
    await get_task_tracker().create(
        "session_commit",
        resource_id=session.session_id,
        account_id=session.ctx.account_id,
        user_id=session.ctx.user.user_id,
        task_id=task_id,
    )
    await session._run_memory_extraction(
        task_id=task_id,
        archive_uri=archive_uri,
        messages=[anchor, early],
        usage_records=[],
        first_message_id="u1",
        last_message_id="a1",
        memory_policy={
            "working_memory": {"enabled": False},
            "self": {"enabled": False},
            "peer": {"enabled": False},
        },
    )

    assert await session._viking_fs.exists(f"{archive_uri}/.done", ctx=session.ctx)
    context = await session.get_session_context()
    assert [message["id"] for message in context["messages"]] == ["u1", "a2"]


async def test_covered_failed_partial_turn_checkpoint_points_to_covering_overview(
    client: AsyncOpenViking,
):
    session = client.session(session_id="covered_failed_partial_checkpoint_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "investigate the outage")
    early = _text_message("a1", "assistant", "checking an early signal")
    tail = _text_message("a2", "assistant", "the latest raw step")
    await _write_archive(
        session,
        1,
        [anchor, early],
        failed=True,
        meta={
            "retention_plan": {
                "mode": "turn_budget",
                "partial_turn": True,
                "turn_anchor_message_id": "u1",
                "checkpoint_source_message_ids": ["a1"],
                "raw_tail_start_message_id": "a2",
                "retained_message_token_budget": 12_000,
            }
        },
    )
    covering_uri = await _write_archive(
        session,
        2,
        [_text_message("u2", "user", "a later query")],
        overview="# Session Summary\n\n## Current State\nThe early signal is covered.",
        meta={
            "checkpoints": [
                {
                    "turn_anchor_message_id": "u1",
                    "source_message_ids": ["a1"],
                    "abstract": "The early signal ruled out a network failure.",
                    "estimated_tokens": 11,
                }
            ]
        },
        done={
            "starting_message_id": "u1",
            "ending_message_id": "u2",
            "coverage_start_archive": "archive_001",
            "coverage_end_archive": "archive_002",
            "covered_failed_archives": ["archive_001"],
        },
    )
    session._messages = [anchor, tail]
    await session._write_to_agfs_async(messages=session._messages)

    context = await session.get_session_context()

    checkpoint = context["messages"][1]
    assert checkpoint["id"] == "checkpoint_archive_002_u1"
    assert checkpoint["parts"][0]["uri"] == covering_uri
    assert checkpoint["source_message_ids"] == ["a1"]


async def test_repeated_partial_commits_merge_checkpoints_for_same_turn_anchor(
    client: AsyncOpenViking,
):
    session = client.session(session_id="repeated_partial_checkpoint_merge_test")
    await session.ensure_exists()
    anchor = _text_message("u1", "user", "investigate the outage")
    tail = _text_message("a3", "assistant", "latest raw step")
    await _write_archive(
        session,
        1,
        [anchor, _text_message("a1", "assistant", "first old step")],
        overview="# Working Memory\n\n## Current State\nFirst prefix covered.",
        meta={
            "checkpoints": [
                {
                    "turn_anchor_message_id": "u1",
                    "source_message_ids": ["a1"],
                    "abstract": "First prefix found pool saturation.",
                    "estimated_tokens": 9,
                }
            ]
        },
        done={"starting_message_id": "u1", "ending_message_id": "a1"},
    )
    second_uri = await _write_archive(
        session,
        2,
        [anchor, _text_message("a2", "assistant", "second old step")],
        overview="# Working Memory\n\n## Current State\nSecond prefix covered.",
        meta={
            "checkpoints": [
                {
                    "turn_anchor_message_id": "u1",
                    "source_message_ids": ["a2"],
                    "abstract": "Second prefix ruled out the network path.",
                    "estimated_tokens": 11,
                }
            ]
        },
        done={"starting_message_id": "u1", "ending_message_id": "a2"},
    )
    session._messages = [anchor, tail]
    await session._write_to_agfs_async(messages=session._messages)

    context = await session.get_session_context()

    assert [message["id"] for message in context["messages"]] == [
        "u1",
        "checkpoint_archive_002_u1",
        "a3",
    ]
    checkpoint = context["messages"][1]
    assert checkpoint["source_message_ids"] == ["a1", "a2"]
    assert checkpoint["parts"][0]["uri"] == second_uri
    assert checkpoint["parts"][0]["abstract"] == (
        "First prefix found pool saturation.\n\n"
        "Second prefix ruled out the network path."
    )


async def test_commit_externalizes_tool_outputs_across_the_whole_turn(
    client: AsyncOpenViking,
):
    session = client.session(session_id="turn_wide_externalization_test")
    session.add_message("user", [TextPart("inspect all files")])
    for index in range(10):
        session.add_message(
            "assistant",
            [
                TextPart(f"step {index}"),
                ToolPart(
                    tool_id=f"tool-{index}",
                    tool_name="read",
                    tool_output=str(index) * 10_000,
                    tool_status="completed",
                ),
            ],
        )

    result = await session.commit_async(
        retention_mode="turn_budget",
        keep_recent_turn_count=1,
        retained_message_token_budget=50_000,
    )

    tool_parts = [
        part for message in session.messages for part in message.parts if isinstance(part, ToolPart)
    ]
    assert result["archived"] is False
    assert any(part.tool_output_ref for part in tool_parts)
    assert all(part.tool_output_group_original_chars == 100_000 for part in tool_parts)
    assert any(part.tool_output_externalized_reason == "turn_budget" for part in tool_parts)


async def test_turn_budget_commit_archives_complete_old_turn_and_keeps_latest_user(
    client: AsyncOpenViking,
):
    session = client.session(session_id="turn_budget_phase1_boundary_test")
    session.add_message("user", [TextPart("first query")])
    session.add_message("assistant", [TextPart("first answer")])
    session.add_message("user", [TextPart("latest query")])
    session.add_message("assistant", [TextPart("latest answer")])

    result = await session.commit_async(
        retention_mode="turn_budget",
        keep_recent_turn_count=1,
        retained_message_token_budget=12_000,
        memory_policy={
            "working_memory": {"enabled": False},
            "self": {"enabled": False},
            "peer": {"enabled": False},
        },
    )
    archived = await session._read_archive_messages(result["archive_uri"])

    assert [message.content for message in archived] == ["first query", "first answer"]
    assert [message.content for message in session.messages] == ["latest query", "latest answer"]
    assert result["estimated_active_tokens"] > 0
    assert result["budget_exceeded"] is False


async def test_concurrent_stale_session_instances_use_one_authoritative_phase1_snapshot(
    client: AsyncOpenViking,
):
    session_a = client.session(session_id="multi_worker_phase1_snapshot_test")
    session_a.add_message("user", [TextPart("only once")])
    session_b = client.session(session_id=session_a.session_id)
    await session_b.load()
    disabled_policy = {
        "working_memory": {"enabled": False},
        "self": {"enabled": False},
        "peer": {"enabled": False},
    }

    results = await asyncio.gather(
        session_a.commit_async(memory_policy=disabled_policy),
        session_b.commit_async(memory_policy=disabled_policy),
    )

    assert sum(result["archived"] is True for result in results) == 1
    assert len(await session_a._list_archive_refs()) == 1
    assert await session_a._read_live_messages_strict() == []


async def test_concurrent_stale_workers_append_without_losing_messages(
    client: AsyncOpenViking,
):
    session_a = client.session(session_id="multi_worker_append_lock_test")
    await session_a.ensure_exists()
    session_b = client.session(session_id=session_a.session_id)
    await session_b.load()

    await asyncio.gather(
        asyncio.to_thread(session_a.add_message, "user", [TextPart("from worker a")]),
        asyncio.to_thread(session_b.add_message, "user", [TextPart("from worker b")]),
    )

    fresh = client.session(session_id=session_a.session_id)
    await fresh.load()
    assert sorted(message.content for message in fresh.messages) == [
        "from worker a",
        "from worker b",
    ]


async def test_phase2_meta_merge_serializes_with_concurrent_append(
    client: AsyncOpenViking,
    monkeypatch,
):
    initial = client.session(session_id="phase2_meta_append_lock_test")
    await initial.add_message_async("user", [TextPart("first")])

    phase2 = client.session(session_id=initial.session_id)
    appending = client.session(session_id=initial.session_id)
    await phase2.load()
    await appending.load()

    phase2_inside_save = asyncio.Event()
    allow_phase2_save = asyncio.Event()
    original_save_meta = phase2._save_meta

    async def delayed_save_meta():
        phase2_inside_save.set()
        await allow_phase2_save.wait()
        await original_save_meta()

    monkeypatch.setattr(phase2, "_save_meta", delayed_save_meta)
    merge_task = asyncio.create_task(
        phase2._merge_and_save_commit_meta(
            archive_index=1,
            memories_extracted={},
            telemetry_snapshot=None,
        )
    )
    await phase2_inside_save.wait()

    append_task = asyncio.create_task(
        appending.add_message_async("assistant", [TextPart("second")])
    )
    await asyncio.sleep(0.05)
    assert not append_task.done()

    allow_phase2_save.set()
    await asyncio.gather(merge_task, append_task)

    fresh = client.session(session_id=initial.session_id)
    await fresh.load()
    assert [message.content for message in fresh.messages] == ["first", "second"]
    assert fresh.meta.message_count == 2
    assert fresh.meta.total_message_count == 2
    assert fresh.meta.commit_count == 1


async def test_add_waits_for_commit_root_rewrite_and_remains_live(
    client: AsyncOpenViking,
    monkeypatch,
):
    committing = client.session(session_id="add_during_commit_lock_test")
    committing.add_message("user", [TextPart("archive me")])
    adding = client.session(session_id=committing.session_id)
    await adding.load()
    commit_inside_rewrite = asyncio.Event()
    allow_commit_rewrite = asyncio.Event()
    original_write = committing._write_to_agfs_async

    async def delayed_root_write(messages):
        commit_inside_rewrite.set()
        await allow_commit_rewrite.wait()
        await original_write(messages)

    class CapturingQueueManager:
        async def enqueue(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(committing, "_write_to_agfs_async", delayed_root_write)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: CapturingQueueManager(),
    )
    disabled_policy = {
        "working_memory": {"enabled": False},
        "self": {"enabled": False},
        "peer": {"enabled": False},
    }

    commit_task = asyncio.create_task(committing.commit_async(memory_policy=disabled_policy))
    await commit_inside_rewrite.wait()
    add_task = asyncio.create_task(
        asyncio.to_thread(adding.add_message, "assistant", [TextPart("keep me live")])
    )
    await asyncio.sleep(0.05)
    assert not add_task.done()
    allow_commit_rewrite.set()
    await asyncio.gather(commit_task, add_task)

    fresh = client.session(session_id=committing.session_id)
    await fresh.load()
    context = await fresh.get_session_context()
    assert [message.content for message in fresh.messages] == ["keep me live"]
    assert [message["parts"][0]["text"] for message in context["messages"]] == [
        "archive me",
        "keep me live",
    ]


async def test_queue_enqueue_failure_marks_archive_failed_and_keeps_raw_logically_live(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="queue_enqueue_failure_recovery_test")
    session.add_message("user", [TextPart("do not lose me")])

    class FailingQueueManager:
        async def enqueue(self, *_args, **_kwargs):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: FailingQueueManager(),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await session.commit_async()

    states = await session._scan_archive_states()
    context = await session.get_session_context()
    failed = json.loads(
        await session._viking_fs.read_file(
            f"{states[0].archive_uri}/.failed.json",
            ctx=session.ctx,
        )
    )
    assert states[0].state == "failed"
    assert failed["stage"] == "queue_enqueue"
    assert [message["parts"][0]["text"] for message in context["messages"]] == ["do not lose me"]


async def test_phase1_root_rewrite_failure_marks_orphan_archive_failed(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="phase1_root_failure_recovery_test")
    session.add_message("user", [TextPart("archive candidate")])
    session.add_message("assistant", [TextPart("retained tail")])
    original_write = session._write_to_agfs_async

    async def write_then_fail(messages):
        await original_write(messages)
        raise RuntimeError("synthetic root rewrite failure")

    monkeypatch.setattr(session, "_write_to_agfs_async", write_then_fail)

    with pytest.raises(RuntimeError, match="synthetic root rewrite failure"):
        await session.commit_async(keep_recent_count=1)

    states = await session._scan_archive_states()
    failed = json.loads(
        await session._viking_fs.read_file(
            f"{states[0].archive_uri}/.failed.json",
            ctx=session.ctx,
        )
    )
    fresh = client.session(session_id=session.session_id)
    await fresh.load()
    context = await fresh.get_session_context()
    assert states[0].state == "failed"
    assert failed["stage"] == "phase1_persist"
    assert [message["parts"][0]["text"] for message in context["messages"]] == [
        "archive candidate",
        "retained tail",
    ]


async def test_phase1_enqueues_before_root_rewrite_and_publishes_ready_last(
    client: AsyncOpenViking,
    monkeypatch,
):
    session = client.session(session_id="phase1_publish_order_test")
    session.add_message("user", [TextPart("archive me")])
    observations: list[tuple[list[str], str]] = []

    class InspectingQueueManager:
        async def enqueue(self, _queue_name, data):
            root = await session._read_live_messages_strict()
            archive_uri = data["archive_uri"]
            observations.append(
                (
                    [message.content for message in root],
                    (await session._read_phase1_meta(archive_uri)).get("status", ""),
                )
            )

    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: InspectingQueueManager(),
    )

    result = await session.commit_async()

    assert observations == [(["archive me"], "preparing")]
    assert (await session._read_phase1_meta(result["archive_uri"]))["status"] == "ready"
    assert await session._read_live_messages_strict() == []


async def test_interrupted_phase1_recovers_when_root_rewrite_is_durable(
    client: AsyncOpenViking,
):
    session = client.session(session_id="phase1_reconcile_durable_root_test")
    original = [
        _text_message("u1", "user", "archive me"),
        _text_message("a1", "assistant", "retain me"),
    ]
    session._messages = original
    await session._write_to_agfs_async(messages=original)
    archive_uri = await _write_archive(session, 1, [original[0]])
    await session._write_phase1_marker(
        archive_uri,
        queue_message={"task_id": "synthetic"},
        original_messages=original,
        archived_messages=[original[0]],
        retained_messages=[original[1]],
        keep_recent_count=1,
        retention_mode=None,
        keep_recent_turn_count=0,
        retained_message_token_budget=0,
        min_raw_tail_steps=1,
    )
    await session._write_to_agfs_async(messages=[original[1]])

    assert await session._ensure_phase1_ready(archive_uri)
    assert (await session._read_phase1_meta(archive_uri))["status"] == "ready"
    assert session.meta.message_count == 1


async def test_interrupted_phase1_before_root_rewrite_becomes_failed(
    client: AsyncOpenViking,
):
    session = client.session(session_id="phase1_reconcile_original_root_test")
    original = [_text_message("u1", "user", "still live")]
    session._messages = original
    await session._write_to_agfs_async(messages=original)
    archive_uri = await _write_archive(session, 1, original)
    await session._write_phase1_marker(
        archive_uri,
        queue_message={"task_id": "synthetic"},
        original_messages=original,
        archived_messages=original,
        retained_messages=[],
        keep_recent_count=0,
        retention_mode=None,
        keep_recent_turn_count=0,
        retained_message_token_budget=0,
        min_raw_tail_steps=1,
    )

    assert not await session._ensure_phase1_ready(archive_uri)
    states = await session._scan_archive_states()
    context = await session.get_session_context()
    assert states[0].state == "failed"
    assert [message["id"] for message in context["messages"]] == ["u1"]


async def test_stale_worker_uses_lock_snapshot_memory_policy_for_queue_message(
    client: AsyncOpenViking,
    monkeypatch,
):
    stale_session = client.session(session_id="stale_memory_policy_snapshot_test")
    stale_session.add_message("user", [TextPart("archive with persisted policy")])
    updater = client.session(session_id=stale_session.session_id)
    await updater.load()
    updater.meta.memory_policy = {
        "working_memory": {"enabled": False},
        "self": {"enabled": False},
        "peer": {"enabled": False},
    }
    await updater._save_meta()
    queued: list[dict] = []

    class CapturingQueueManager:
        async def enqueue(self, _queue_name, data):
            queued.append(data)

    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: CapturingQueueManager(),
    )

    result = await stale_session.commit_async()

    assert result["archived"] is True
    assert queued[0]["memory_policy"] == updater.meta.memory_policy
