# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Context retrieval tests"""

import asyncio
import json
from functools import partial

import pytest
import pytest_asyncio

from openviking.message import Message, TextPart
from openviking.models.vlm.base import ToolCall, VLMResponse
from openviking.privacy.reference import build_privacy_field_reference
from openviking.prompts import manager as prompt_manager
from openviking.server.config import UserSettingsPatch
from openviking.server.identity import RequestContext
from openviking.server.user_config import patch_user_config
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
from openviking.session import working_memory as wm
from openviking.session.extraction_batch import estimate_extraction_message_tokens
from openviking.session.session import _estimate_summary_template_tokens
from openviking.storage.abstract_overview import (
    WM_TEMPLATE_DESCRIPTIONS_KEY,
    body_for_preview,
    parse_abstract_overview,
    render_abstract_overview,
)
from openviking.storage.queuefs import QueueManager, SessionCommitMsg, get_queue_manager
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.config import OPENVIKING_PROMPT_TEMPLATES_DIR_ENV, get_openviking_config
from openviking_cli.utils.config.vlm_config import VLMConfig


def _install_fake_vlm(monkeypatch):
    async def _fake_get_completion(self, prompt, thinking=False, max_retries=0, **kwargs):
        return "# Test Summary\n\nFake summary for testing.\n\n## Details\nTest content."

    async def _fake_get_vision_completion(self, prompt, images, thinking=False):
        return "Fake image description for testing."

    monkeypatch.setattr(VLMConfig, "is_available", lambda self: True)
    monkeypatch.setattr(VLMConfig, "get_completion_async", _fake_get_completion)
    monkeypatch.setattr(VLMConfig, "get_vision_completion_async", _fake_get_vision_completion)


@pytest_asyncio.fixture(scope="function")
async def client(
    service: OpenVikingService,
    request_context: RequestContext,
    monkeypatch,
    working_memory_policy,
):
    service.sessions.set_default_user_memory_policy(working_memory_policy)
    _install_fake_vlm(monkeypatch)
    monkeypatch.setattr(get_openviking_config().memory, "extraction_enabled", False)
    client = partial(service.sessions.session, request_context)

    queue_manager = get_queue_manager()
    original_enqueue = queue_manager.enqueue
    commit_tasks: list[asyncio.Task] = []
    queued_commit_tasks: set[str] = set()
    tracker = get_task_tracker()
    original_has_work = tracker.has_work

    async def has_work(task_id, **kwargs):
        return task_id in queued_commit_tasks or await original_has_work(task_id, **kwargs)

    monkeypatch.setattr(
        tracker,
        "has_work",
        has_work,
    )

    async def enqueue_with_session_commit_fallback(queue_name, data, **keys):
        if queue_name != QueueManager.SESSION_COMMIT:
            return await original_enqueue(queue_name, data, **keys)

        assert keys == {"group_key": SessionCommitMsg.from_dict(data).queue_group_key}
        queued_commit_tasks.add(data["task_id"])

        async def process_commit():
            message = SessionCommitMsg(**data)
            queued_session = client(session_id=message.session_id)
            while True:
                phase1 = await queued_session._read_phase1_meta(message.archive_uri)
                if phase1.get("status") == "ready":
                    break
                await asyncio.sleep(0)
            await queued_session.load()
            try:
                while not await queued_session.resume_queued_commit(message):
                    await asyncio.sleep(0)
            finally:
                queued_commit_tasks.discard(message.task_id)

        commit_tasks.append(asyncio.create_task(process_commit()))
        return data["task_id"]

    monkeypatch.setattr(queue_manager, "enqueue", enqueue_with_session_commit_fallback)
    yield client
    if commit_tasks:
        await asyncio.gather(*commit_tasks, return_exceptions=True)


def _estimate_tokens(text: str) -> int:
    return -(-len(text) // 4)


def _override_wm_state_template(monkeypatch, tmp_path, template_line, *, suffix=""):
    relative_path = "compression/ov_wm_v2.yaml"
    bundled = prompt_manager.PromptManager._get_bundled_templates_dir() / relative_path
    override = tmp_path / relative_path
    override.parent.mkdir(exist_ok=True)
    before, state_section = bundled.read_text(encoding="utf-8").split("  ## Current State\n", 1)
    _, remaining = state_section.split("\n", 1)
    if suffix:
        remaining = remaining.replace("\nllm_config:", f"\n{suffix}\n\nllm_config:")
    override.write_text(
        f"{before}  ## Current State\n  {template_line}\n{remaining}", encoding="utf-8"
    )
    monkeypatch.setenv(OPENVIKING_PROMPT_TEMPLATES_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(prompt_manager, "_default_manager", None)


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = await tracker.get(task_id)
        if task and task.status.value in ("completed", "failed"):
            return task.to_dict()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


async def test_oversized_legacy_assistant_only_commit_completes_without_checkpoint(
    client: partial,
):
    session = client(session_id="legacy_assistant_only_turn_test")
    await session.ensure_exists()
    for index in range(3):
        session.add_message("assistant", [TextPart(str(index) * 1000)])

    result = await session.commit_async(
        retention_mode="turn_budget",
        keep_recent_turn_count=1,
        retained_message_token_budget=150,
        min_raw_tail_steps=1,
    )
    task = await _wait_for_task(result["task_id"])
    context = await session.get_session_context()

    assert task["status"] == "completed"
    assert context["latest_archive_overview"]
    assert context["messages"] == []
    archive_meta = json.loads(
        await session._viking_fs.read_file(
            f"{result['archive_uri']}/.meta.json",
            ctx=session.ctx,
        )
    )
    assert archive_meta["retention_plan"]["partial_turn"] is False
    assert archive_meta["retention_plan"]["turn_anchor_message_id"] is None
    assert archive_meta.get("checkpoints", []) == []


class TestGetContextForSearch:
    """Test get_context_for_search"""

    async def test_get_context_basic(self, session_with_messages: Session):
        """Test basic context retrieval"""
        context = await session_with_messages.get_context_for_search(query="testing help")

        assert isinstance(context, dict)
        assert "latest_archive_overview" in context
        assert "current_messages" in context

    async def test_get_context_with_max_messages(self, session_with_messages: Session):
        """Test limiting max messages"""
        context = await session_with_messages.get_context_for_search(query="test", max_messages=2)

        assert isinstance(context, dict)
        assert len(context["current_messages"]) <= 2

    async def test_get_context_returns_latest_completed_archive_only(self, client: partial):
        """Current context should expose only the latest completed archive overview."""
        session = client(session_id="archive_context_test")
        await session.ensure_exists()

        session.add_message("user", [TextPart("First message")])
        session.add_message("assistant", [TextPart("First response")])
        result1 = await session.commit_async()
        await _wait_for_task(result1["task_id"])

        session.add_message("user", [TextPart("Second message")])
        session.add_message("assistant", [TextPart("Second response")])
        session.add_message("user", [TextPart("Third message")])
        result2 = await session.commit_async()
        await _wait_for_task(result2["task_id"])
        latest_overview = await session._viking_fs.read_file(
            f"{result2['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )

        session.add_message("user", [TextPart("Current message")])
        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"] == body_for_preview(latest_overview)
        assert len(context["current_messages"]) == 1

    async def test_get_context_skips_incomplete_latest_archive(self, client: partial):
        """Incomplete archives without .done must not replace the latest completed overview."""
        session = client(session_id="archive_context_incomplete_test")
        await session.ensure_exists()

        session.add_message("user", [TextPart("First message")])
        session.add_message("assistant", [TextPart("First response")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        completed_overview = await session._viking_fs.read_file(
            f"{result['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )
        await session._viking_fs.write_file(
            uri=f"{session.uri}/history/archive_999/.overview.md",
            content="INCOMPLETE OVERVIEW",
            ctx=session.ctx,
        )

        context = await session.get_context_for_search(query="test")

        assert context["latest_archive_overview"] == body_for_preview(completed_overview)

    async def test_get_context_includes_incomplete_archive_messages(self, client: partial):
        """Pending archive messages should be merged with current live messages."""
        session = client(session_id="archive_context_pending_messages_test")
        await session.ensure_exists()

        session.add_message("user", [TextPart("First message")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        pending_messages = [
            Message(id="pending-user", role="user", parts=[TextPart("Pending user message")]),
            Message(
                id="pending-assistant",
                role="assistant",
                parts=[TextPart("Pending assistant response")],
            ),
        ]
        await session._viking_fs.write_file(
            uri=f"{session.uri}/history/archive_002/messages.jsonl",
            content="\n".join(msg.to_jsonl() for msg in pending_messages) + "\n",
            ctx=session.ctx,
        )

        session.add_message("user", [TextPart("Current live message")])
        context = await session.get_context_for_search(query="test")

        assert [m.content for m in context["current_messages"]] == [
            "Pending user message",
            "Pending assistant response",
            "Current live message",
        ]

    async def test_get_context_max_messages_applies_after_pending_merge(self, client: partial):
        """max_messages should trim the merged pending + live message sequence."""
        session = client(session_id="archive_context_pending_max_messages_test")
        await session.ensure_exists()

        session.add_message("user", [TextPart("First message")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        pending_messages = [
            Message(id="pending-1", role="user", parts=[TextPart("Pending 1")]),
            Message(id="pending-2", role="assistant", parts=[TextPart("Pending 2")]),
        ]
        await session._viking_fs.write_file(
            uri=f"{session.uri}/history/archive_002/messages.jsonl",
            content="\n".join(msg.to_jsonl() for msg in pending_messages) + "\n",
            ctx=session.ctx,
        )

        session.add_message("user", [TextPart("Live 1")])
        session.add_message("assistant", [TextPart("Live 2")])

        context = await session.get_context_for_search(query="test", max_messages=3)

        assert [m.content for m in context["current_messages"]] == [
            "Pending 2",
            "Live 1",
            "Live 2",
        ]

    async def test_get_context_empty_session(self, session: Session):
        """Test getting context from empty session"""
        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"] == ""
        assert context["current_messages"] == []

    async def test_get_context_after_commit(self, client: partial):
        """Test getting context after commit"""
        session = client(session_id="post_commit_context_test")
        await session.ensure_exists()

        session.add_message("user", [TextPart("Test message before commit")])
        session.add_message("assistant", [TextPart("Response before commit")])

        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("New message after commit")])

        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"]
        assert len(context["current_messages"]) == 1

    async def test_get_context_tracks_multiple_rapid_commits_by_done_boundary(
        self, client: partial, monkeypatch
    ):
        """Context should only advance latest overview when the earlier archive is .done."""
        session = client(session_id="archive_context_done_boundary_test")
        await session.ensure_exists()
        first_gate = asyncio.Event()
        second_gate = asyncio.Event()
        second_started = asyncio.Event()

        async def gated_summary(self, prompt, **kwargs):
            del self, kwargs
            if "First round" in prompt:
                await first_gate.wait()
                return "# First Summary\n\nFirst archive completed."
            second_started.set()
            await second_gate.wait()
            return "# Second Summary\n\nSecond archive completed."

        monkeypatch.setattr(VLMConfig, "get_completion_async", gated_summary)

        session.add_message("user", [TextPart("First round user")])
        session.add_message("assistant", [TextPart("First round assistant")])
        result1 = await session.commit_async()

        session.add_message("user", [TextPart("Second round user")])
        session.add_message("assistant", [TextPart("Second round assistant")])
        result2 = await session.commit_async()

        context = await session.get_context_for_search(query="test")
        assert context["latest_archive_overview"] == ""
        assert [m.content for m in context["current_messages"]] == [
            "First round user",
            "First round assistant",
            "Second round user",
            "Second round assistant",
        ]

        first_gate.set()
        await asyncio.wait_for(second_started.wait(), timeout=5.0)

        first_overview = await session._viking_fs.read_file(
            f"{result1['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )
        context = await session.get_context_for_search(query="test")
        assert context["latest_archive_overview"] == body_for_preview(first_overview)
        assert [m.content for m in context["current_messages"]] == [
            "Second round user",
            "Second round assistant",
        ]

        second_gate.set()
        await _wait_for_task(result1["task_id"])
        await _wait_for_task(result2["task_id"])

        second_overview = await session._viking_fs.read_file(
            f"{result2['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )
        context = await session.get_context_for_search(query="test")
        assert context["latest_archive_overview"] == body_for_preview(second_overview)
        assert context["current_messages"] == []


class TestGetSessionContext:
    """Test get_session_context"""

    async def test_get_session_context_returns_latest_archive_overview_and_history(
        self, client: partial, monkeypatch
    ):
        session = client(session_id="assemble_trim_test")
        await session.ensure_exists()
        summaries = [
            "# Session Summary\n\n" + ("A" * 80),
            "# Session Summary\n\n" + ("B" * 20),
        ]

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            del self, latest_archive_overview, kwargs
            return summaries.pop(0)

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)

        session.add_message("user", [TextPart("first turn")])
        session.add_message("assistant", [TextPart("first reply")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("second turn")])
        session.add_message("assistant", [TextPart("second reply")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("active tail")])

        newest_summary = "# Session Summary\n\n" + ("B" * 20)
        active_tokens = sum(message.estimated_tokens for message in session.messages)
        token_budget = active_tokens + _estimate_tokens(newest_summary)

        context = await session.get_session_context(token_budget=token_budget)

        assert context["latest_archive_overview"] == newest_summary
        assert context["pre_archive_abstracts"] == []
        assert len(context["messages"]) == 1
        assert context["messages"][0]["parts"][0]["text"] == "active tail"
        assert context["estimatedTokens"] == token_budget
        assert context["stats"] == {
            "totalArchives": 2,
            "includedArchives": 0,
            "droppedArchives": 2,
            "failedArchives": 0,
            "activeTokens": active_tokens,
            "archiveTokens": _estimate_tokens(newest_summary),
        }

    async def test_get_session_context_counts_active_tool_parts(
        self, session_with_tool_call: tuple[Session, str, str]
    ):
        session, _message_id, tool_id = session_with_tool_call

        context = await session.get_session_context()

        assert len(context["messages"]) == 1
        tool_parts = [part for part in context["messages"][0]["parts"] if part["type"] == "tool"]
        assert tool_parts[0]["tool_id"] == tool_id
        assert context["stats"]["activeTokens"] == session.messages[0].estimated_tokens
        assert context["stats"]["activeTokens"] > _estimate_tokens("Executing tool...")

    async def test_get_session_context_stops_at_newest_terminal_without_abstracts(
        self, client: partial, monkeypatch
    ):
        """Newest completed supplies overview; abstracts are never read for context."""
        session = client(session_id="assemble_lazy_read_test")
        await session.ensure_exists()
        summaries = [
            "# Summary\n\n" + ("A" * 80),
            "# Summary\n\n" + ("B" * 80),
            "# Summary\n\n" + ("C" * 80),
        ]

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            del self, latest_archive_overview, kwargs
            return summaries.pop(0)

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)

        for word in ("first", "second", "third"):
            session.add_message("user", [TextPart(f"{word} turn")])
            session.add_message("assistant", [TextPart(f"{word} reply")])
            result = await session.commit_async()
            await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("active tail")])

        newest_summary = "# Summary\n\n" + ("C" * 80)
        active_tokens = sum(m.estimated_tokens for m in session.messages)
        token_budget = active_tokens + _estimate_tokens(newest_summary)

        original_read_file = session._viking_fs.read_file
        read_uris: list[str] = []

        async def tracking_read_file(*args, **kwargs):
            uri = args[0] if args else kwargs.get("uri")
            read_uris.append(uri)
            return await original_read_file(*args, **kwargs)

        monkeypatch.setattr(session._viking_fs, "read_file", tracking_read_file)

        context = await session.get_session_context(token_budget=token_budget)

        assert context["latest_archive_overview"] == newest_summary
        assert context["pre_archive_abstracts"] == []
        assert context["stats"]["includedArchives"] == 0
        assert context["stats"]["totalArchives"] == 3

        abstract_reads = [u for u in read_uris if u.endswith(".abstract.md")]
        assert abstract_reads == [], f"Old abstracts must not be read, got: {abstract_reads}"
        overview_reads = [u for u in read_uris if u.endswith(".overview.md")]
        assert overview_reads, (
            f"Newest terminal overview should be available, got: {overview_reads}"
        )
        assert any("archive_003" in u for u in overview_reads), (
            f"Newest completed overview should be read, got: {overview_reads}"
        )

    async def test_get_session_context_does_not_touch_archives_older_than_terminal(
        self, client: partial, monkeypatch
    ):
        """Scan cost must not grow with history length.

        Archive history grows without bound, so the read path stops at the newest
        terminal marker: no marker, overview, meta or messages read may reference
        an older archive.
        """
        session = client(session_id="assemble_terminal_stop_cost_test")
        await session.ensure_exists()
        summaries = [f"# Summary\n\narchive {index}" for index in range(1, 5)]

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            del self, latest_archive_overview, kwargs
            return summaries.pop(0)

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)

        for word in ("first", "second", "third", "fourth"):
            session.add_message("user", [TextPart(f"{word} turn")])
            session.add_message("assistant", [TextPart(f"{word} reply")])
            result = await session.commit_async()
            await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("active tail")])

        touched: list[str] = []
        original_read_file = session._viking_fs.read_file
        original_exists = session._viking_fs.exists

        async def tracking_read_file(*args, **kwargs):
            touched.append(args[0] if args else kwargs.get("uri"))
            return await original_read_file(*args, **kwargs)

        async def tracking_exists(*args, **kwargs):
            touched.append(args[0] if args else kwargs.get("uri"))
            return await original_exists(*args, **kwargs)

        monkeypatch.setattr(session._viking_fs, "read_file", tracking_read_file)
        monkeypatch.setattr(session._viking_fs, "exists", tracking_exists)

        context = await session.get_session_context()

        assert context["latest_archive_overview"].endswith("archive 4")
        stale = [
            uri
            for uri in touched
            if isinstance(uri, str) and any(f"archive_{index:03d}" in uri for index in (1, 2, 3))
        ]
        assert stale == [], f"Archives older than the terminal must not be read: {stale}"

    async def test_get_session_context_pre_archive_abstracts_always_empty(
        self, client: partial, monkeypatch
    ):
        session = client(session_id="assemble_trim_oldest_abstracts_test")
        await session.ensure_exists()
        summaries = [
            "# Summary\n\n" + ("A" * 80),
            "# Summary\n\n" + ("B" * 80),
            "# Summary\n\n" + ("C" * 80),
        ]

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            del self, latest_archive_overview, kwargs
            return summaries.pop(0)

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)

        for word in ("first", "second", "third"):
            session.add_message("user", [TextPart(f"{word} turn")])
            session.add_message("assistant", [TextPart(f"{word} reply")])
            result = await session.commit_async()
            await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("active tail")])

        newest_summary = "# Summary\n\n" + ("C" * 80)
        active_tokens = sum(m.estimated_tokens for m in session.messages)
        token_budget = active_tokens + _estimate_tokens(newest_summary)

        context = await session.get_session_context(token_budget=token_budget)

        assert context["latest_archive_overview"] == newest_summary
        assert context["pre_archive_abstracts"] == []
        assert context["estimatedTokens"] == (active_tokens + _estimate_tokens(newest_summary))
        assert context["stats"]["totalArchives"] == 3
        assert context["stats"]["includedArchives"] == 0
        assert context["stats"]["droppedArchives"] == 3

    async def test_get_session_context_newest_done_unreadable_overview_does_not_fallback(
        self, client: partial, monkeypatch
    ):
        session = client(session_id="assemble_failed_archive_test")
        await session.ensure_exists()
        summaries = [
            "# Session Summary\n\narchive one",
            "# Session Summary\n\narchive two",
        ]

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            del self, latest_archive_overview, kwargs
            return summaries.pop(0)

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)

        session.add_message("user", [TextPart("turn one")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("turn two")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        original_read_file = session._viking_fs.read_file

        async def flaky_read_file(*args, **kwargs):
            uri = args[0] if args else kwargs.get("uri")
            if isinstance(uri, str) and uri.endswith("archive_002/.overview.md"):
                raise RuntimeError("simulated archive read failure")
            return await original_read_file(*args, **kwargs)

        monkeypatch.setattr(session._viking_fs, "read_file", flaky_read_file)

        context = await session.get_session_context(token_budget=128_000)

        # Newest terminal becomes failed/unusable for overview; no older fallback.
        assert context["latest_archive_overview"] == ""
        assert context["pre_archive_abstracts"] == []
        assert context["stats"]["totalArchives"] == 2
        assert context["stats"]["includedArchives"] == 0
        assert context["stats"]["failedArchives"] == 1

    async def test_get_session_context_newest_failed_skips_overview(
        self, client: partial, monkeypatch
    ):
        session = client(session_id="assemble_newest_failed_test")
        await session.ensure_exists()

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            del self, latest_archive_overview, kwargs
            return "# Session Summary\n\narchive one"

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)

        session.add_message("user", [TextPart("turn one")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        await session._viking_fs.write_file(
            uri=f"{session.uri}/history/archive_002/.failed.json",
            content='{"stage":"summary","error":"boom"}',
            ctx=session.ctx,
        )
        pending = Message(
            id="pending-after-fail",
            role="user",
            parts=[TextPart("after failed archive")],
        )
        await session._viking_fs.write_file(
            uri=f"{session.uri}/history/archive_003/messages.jsonl",
            content=pending.to_jsonl() + "\n",
            ctx=session.ctx,
        )
        session.add_message("user", [TextPart("live tail")])

        context = await session.get_session_context(token_budget=128_000)

        assert context["latest_archive_overview"] == ""
        assert context["pre_archive_abstracts"] == []
        assert [m["parts"][0]["text"] for m in context["messages"]] == [
            "after failed archive",
            "live tail",
        ]
        assert context["stats"]["failedArchives"] == 1

    async def test_get_session_context_budget_trim_drops_latest_archive_abstract(
        self, client: partial, monkeypatch
    ):
        session = client(session_id="assemble_trim_id_test")
        await session.ensure_exists()

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            del self, latest_archive_overview, kwargs
            return "# Session Summary\n\n" + ("Z" * 80)

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)

        session.add_message("user", [TextPart("turn one")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        context = await session.get_session_context(token_budget=1)

        assert context["latest_archive_overview"] == ""
        assert context["pre_archive_abstracts"] == []
        assert context["stats"]["includedArchives"] == 0
        assert context["stats"]["droppedArchives"] == 1

    async def test_get_session_context_returns_pending_messages_while_commit_running(
        self, client: partial
    ):
        """Regression for #3129: when an archive has been written (Phase 1 done,
        commit_count advanced) but its ``.done`` marker has not been written yet
        (Phase 2 still running), ``get_session_context`` must still surface the
        archived messages.

        This test sets up the post-Phase-1 filesystem state directly — archive
        directory + ``messages.jsonl`` + updated ``.meta.json``, no ``.done`` —
        and then loads a fresh session from that state.  It does **not** go
        through ``commit_async``, so it is deterministic regardless of the
        queue-worker architecture."""
        session_id = "deterministic_pending_archive_test"
        session = client(session_id=session_id)
        await session.ensure_exists()

        # Add messages to the session (in-memory only at this point).
        session.add_message("user", [TextPart("Pending user message")])
        session.add_message("assistant", [TextPart("Pending assistant response")])

        # Build the post-Phase-1 filesystem state directly.
        vfs = session._viking_fs
        session_uri = session._session_uri

        # Serialise current in-memory messages as JSONL for the archive.
        archive_messages_jsonl = "\n".join(msg.to_jsonl() for msg in list(session.messages)) + "\n"

        # Write archive directory + messages (Phase 1 done).
        archive_dir = f"{session_uri}/history/archive_001"
        await vfs.mkdir(archive_dir, exist_ok=True, ctx=session.ctx)
        await vfs.write_file(
            f"{archive_dir}/messages.jsonl",
            archive_messages_jsonl,
            ctx=session.ctx,
        )

        # Update .meta.json: bump commit_count to match the archive index.
        meta = session._meta
        meta.commit_count = 1
        meta.message_count = 0
        await vfs.write_file(
            f"{session_uri}/.meta.json",
            json.dumps(meta.to_dict(), ensure_ascii=False),
            ctx=session.ctx,
        )

        # Clear live messages (Phase 1 trims these after archiving).
        await vfs.write_file(f"{session_uri}/messages.jsonl", "", ctx=session.ctx)

        # Crucially: do NOT write .done or .failed.json — Phase 2 is still
        # running, so the archive must be treated as pending.

        # Load a fresh session from the filesystem state we just wrote.
        fresh_session = client(session_id=session_id)

        # The pending archive's messages must be visible.
        context = await fresh_session.get_session_context()
        assert [m["parts"][0]["text"] for m in context["messages"]] == [
            "Pending user message",
            "Pending assistant response",
        ]


class TestGetSessionArchive:
    """Test get_session_archive"""

    @pytest.mark.parametrize(
        "summary_format",
        [
            "structured",
            "compact",
            "wrapped",
            "custom",
            "plain-description",
            "description-only",
            "repeated-title",
            "trailing-partial-example",
            "jinja",
            "example",
            "italic-example",
            "first-line-example",
            "first-line-italic-example",
            "marked-example",
        ],
    )
    async def test_get_session_archive_returns_messages_and_working_memory(
        self, client: partial, monkeypatch, tmp_path, summary_format
    ):
        session = client(session_id="session_archive_expand_test")
        await session.ensure_exists()
        template = (
            "# Working Memory\n\n## Session Title\nSign-off review\n\n## Current State\n{state}"
        )
        copies_description = summary_format in {
            "custom",
            "plain-description",
            "description-only",
            "jinja",
            "repeated-title",
            "trailing-partial-example",
        }
        unmarked_example = summary_format in {"first-line-example", "first-line-italic-example"}
        if summary_format == "compact":
            template = "# Working Memory\n\n**Status**: {state}"
        elif summary_format == "wrapped":
            template = template.replace("{state}", "**Status**:\n{state}")
        elif (
            copies_description
            or unmarked_example
            or summary_format in {"example", "italic-example", "marked-example"}
        ):
            description = "_Record the latest sign-off outcome here._"
            template_line = description
            if summary_format == "jinja":
                template_line = "_Record the latest sign-off outcome in {{ output_language }}._"
                description = "_Record the latest sign-off outcome in en._"
            elif summary_format == "plain-description":
                template_line = description = "Capture the latest release status in one sentence."
            elif summary_format == "example":
                template_line += "\n  Sign-off complete."
            elif summary_format == "italic-example":
                template_line += "\n  _Sign-off complete._"
            elif summary_format == "first-line-example":
                template_line = description = "Sign-off complete."
            elif summary_format == "first-line-italic-example":
                template_line = description = "_Sign-off complete._"
            elif summary_format == "marked-example":
                description = "Record the current release decision."
                template_line = f"<!-- wm-example -->\n  {description}"
                template = template.replace("{state}", "{state}\nThe rollout is pending.")
            if copies_description:
                template_line = f"<!-- wm-description -->\n  {template_line}"
            suffix = {
                "repeated-title": "  # Working Memory\n  {# End of prompt #}",
                "trailing-partial-example": (
                    "  For reference, here is an example of just one section.\n\n"
                    "  # Working Memory\n\n"
                    "  ## Current State\n"
                    "  <!-- wm-example -->\n"
                    "  Follow-up is scheduled."
                ),
            }.get(summary_format, "")
            _override_wm_state_template(monkeypatch, tmp_path, template_line, suffix=suffix)
            prompt = prompt_manager.render_prompt(
                "compression.ov_wm_v2", {"messages": "", "output_language": "en"}
            )
            assert description in prompt
            if summary_format == "repeated-title":
                assert prompt.endswith("# Working Memory\n")
            elif summary_format == "trailing-partial-example":
                assert prompt.endswith(
                    "## Current State\n<!-- wm-example -->\nFollow-up is scheduled."
                )
            if copies_description:
                template = template.replace("{state}", description + "\n{state}")
            if unmarked_example:
                template = template.replace("{state}", "{state}\nTracker updated.")

        states = ("Sign-off pending.", "Sign-off complete.")
        if summary_format in {"italic-example", "first-line-italic-example"}:
            states = tuple(f"_{state}_" for state in states)
        if summary_format == "marked-example":
            states = (states[0], description)
        if summary_format == "description-only":
            template = template.replace("Sign-off review", "{state}").removesuffix("\n{state}")
        overviews = iter(template.format(state=state) for state in states)

        async def complete(self, prompt, **kwargs):
            if kwargs.get("tools"):
                return "Regenerate Working Memory."
            return next(overviews)

        monkeypatch.setattr(VLMConfig, "get_completion_async", complete)

        messages_by_turn = {}
        for turn in ("one", "two"):
            user = session.add_message("user", [TextPart(f"turn {turn}")])
            assistant = session.add_message("assistant", [TextPart(f"reply {turn}")])
            messages_by_turn[turn] = [user.to_dict(), assistant.to_dict()]
            result = await session.commit_async()
            task = await _wait_for_task(result["task_id"])
            assert task["status"] == "completed", task

        for index, (turn, expected) in enumerate(zip(messages_by_turn, states, strict=True), 1):
            archive_id = f"archive_{index:03d}"
            archive_uri = f"{session.uri}/history/{archive_id}"
            abstract_uri = f"{archive_uri}/.abstract.md"
            stored = await session._viking_fs.read_file(abstract_uri, ctx=session.ctx)
            assert body_for_preview(stored) == expected

            archive = await session.get_session_archive(archive_id)
            assert archive["archive_id"] == archive_id
            assert archive["abstract"] == expected
            assert archive["overview"] == template.format(state=expected)
            assert archive["messages"] == messages_by_turn[turn]

            await session._viking_fs.write_file(abstract_uri, "# Working Memory", ctx=session.ctx)
            historical = await session.get_session_archive(archive_id)
            assert historical["abstract"] == expected
            if unmarked_example:
                await session._viking_fs.rm(abstract_uri, ctx=session.ctx)
                assert (await session.get_session_archive(archive_id))["abstract"] == expected

        if copies_description:
            vfs = session._viking_fs
            first_archive = f"{session.uri}/history/archive_001"
            second_archive = f"{session.uri}/history/archive_002"
            first_abstract = f"{first_archive}/.abstract.md"
            second_abstract = f"{second_archive}/.abstract.md"
            await vfs.write_file(first_abstract, description, ctx=session.ctx)
            await vfs.rm(second_abstract, ctx=session.ctx)
            for archive_uri in (first_archive, second_archive):
                await vfs.rm(f"{archive_uri}/.meta.json", ctx=session.ctx)

            # A different template and missing archive metadata should not affect L1 provenance.
            _override_wm_state_template(monkeypatch, tmp_path, "_Describe the acceptance result._")
            for index, expected in enumerate(states, 1):
                historical = await session.get_session_archive(f"archive_{index:03d}")
                assert historical["abstract"] == expected
                assert historical["overview"] == template.format(state=expected)
            assert await vfs.read_file(first_abstract, ctx=session.ctx) == description
            assert not await vfs.exists(second_abstract, ctx=session.ctx)

    async def test_archive_template_description_is_protected_with_overview(
        self, client, service, monkeypatch, tmp_path
    ):
        session = client(session_id="private_wm_description")
        await session.ensure_exists()
        vfs = session._viking_fs
        await patch_user_config(vfs, session.ctx, UserSettingsPatch(privacy={"mode": "auto"}))
        secret = "synthetic-wm-template-value"
        await service.privacy_configs.upsert(
            session.ctx, "credential", "wm-release", {"token": secret, "label": "archive_summary"}
        )
        description = f"Record the latest release status for {secret}."
        _override_wm_state_template(
            monkeypatch, tmp_path, f"<!-- wm-description -->\n  {description}"
        )
        state = "Sign-off complete."

        async def complete(self, prompt, **kwargs):
            assert description in prompt
            return f"# Working Memory\n\n## Current State\n{description}\n{state}"

        monkeypatch.setattr(VLMConfig, "get_completion_async", complete)
        await session.add_message_async("user", [TextPart("Check the release status.")])
        result = await session.commit_async()
        task = await _wait_for_task(result["task_id"])
        assert task["status"] == "completed", task

        archive_uri = result["archive_uri"]
        abstract_uri = f"{archive_uri}/.abstract.md"
        raw = await vfs.read_file(f"{archive_uri}/.overview.md", ctx=session.ctx)
        overview = parse_abstract_overview(raw)
        reference = build_privacy_field_reference(
            session.ctx.user.user_id, "credential", "wm-release", "token"
        )
        protected_description = description.replace(secret, reference)
        assert protected_description in overview.preview_body
        assert secret not in raw
        assert overview.metadata[WM_TEMPLATE_DESCRIPTIONS_KEY] == {
            "## Current State": protected_description
        }
        assert overview.metadata["generated_by"]["trigger"] == "archive_summary"
        assert body_for_preview(await vfs.read_file(abstract_uri, ctx=session.ctx)) == state
        await vfs.rm(abstract_uri, ctx=session.ctx)
        assert (await session.get_session_archive("archive_001"))["abstract"] == state

    async def test_archive_abstract_uses_last_batch_rendered_description(
        self, client, monkeypatch, tmp_path
    ):
        session = client(session_id="multilingual_wm_batches")
        await session.ensure_exists()
        _override_wm_state_template(
            monkeypatch,
            tmp_path,
            "<!-- wm-description -->\n  _Record the latest sign-off outcome in {{ output_language }}._",
        )
        chinese = "请继续核对最新验收结果并记录检查进度。" * 180
        english = "The final sign-off is complete."
        languages = []

        async def complete(self, prompt, **kwargs):
            if chinese[:20] in prompt and english not in prompt:
                assert "_Record the latest sign-off outcome in zh-CN._" in prompt
                languages.append("zh-CN")
                return "# Interim summary\n\nThe first batch was reviewed."
            assert english in prompt
            description = "_Record the latest sign-off outcome in en._"
            assert description in prompt
            languages.append("en")
            return f"# Working Memory\n\n## Current State\n{description}\n{english}"

        monkeypatch.setattr(VLMConfig, "get_completion_async", complete)
        first_message = session.add_message("user", [TextPart(chinese)])
        session.add_message("user", [TextPart(english)])
        first_batch_tokens = estimate_extraction_message_tokens([first_message])
        first_batch_tokens += _estimate_summary_template_tokens("")
        window = (first_batch_tokens * 10 + 6) // 7
        result = await session.commit_async(session_window_context=window)
        task = await _wait_for_task(result["task_id"])
        assert task["status"] == "completed", task
        assert languages == ["zh-CN", "en"]
        abstract_uri = f"{result['archive_uri']}/.abstract.md"
        vfs = session._viking_fs
        assert body_for_preview(await vfs.read_file(abstract_uri, ctx=session.ctx)) == english
        raw_overview = await vfs.read_file(f"{result['archive_uri']}/.overview.md", ctx=session.ctx)
        assert parse_abstract_overview(raw_overview).metadata[WM_TEMPLATE_DESCRIPTIONS_KEY] == {
            "## Current State": "_Record the latest sign-off outcome in en._"
        }
        await vfs.rm(abstract_uri, ctx=session.ctx)
        assert (await session.get_session_archive("archive_001"))["abstract"] == english

    async def test_archive_abstract_keeps_italic_state_after_wm_update(self, client, monkeypatch):
        session = client(session_id="italic_wm_update")
        await session.ensure_exists()
        title = "# Working Memory\n\n## Session Title\nSign-off review\n\n## Current State\n"
        states = ("*Sign-off pending.*", "_Sign-off complete._")
        updates = iter(states)

        async def complete(self, prompt, **kwargs):
            if not kwargs.get("tools"):
                return title + "Waiting for the review."
            assert kwargs["tools"][0]["function"]["name"] == "update_working_memory"
            return VLMResponse(
                tool_calls=[
                    ToolCall(
                        id="update",
                        name="update_working_memory",
                        arguments={
                            "sections": {
                                "Current State": {
                                    "op": "UPDATE",
                                    "content": f"{next(updates)}\nTracker updated.",
                                },
                            },
                        },
                    )
                ],
                finish_reason="tool_calls",
            )

        monkeypatch.setattr(VLMConfig, "get_completion_async", complete)

        for index in range(1, 4):
            session.add_message("user", [TextPart(f"Check the sign-off status, turn {index}.")])
            session.add_message("assistant", [TextPart("I checked the tracker.")])
            result = await session.commit_async()
            task = await _wait_for_task(result["task_id"])
            assert task["status"] == "completed", task

        for index, expected in enumerate(states, 2):
            archive_id = f"archive_{index:03d}"
            abstract_uri = f"{session.uri}/history/{archive_id}/.abstract.md"
            vfs = session._viking_fs
            stored = await vfs.read_file(abstract_uri, ctx=session.ctx)
            assert body_for_preview(stored) == expected
            await vfs.rm(abstract_uri, ctx=session.ctx)
            historical = await session.get_session_archive(archive_id)
            assert f"## Current State\n{expected}\nTracker updated.\n" in historical["overview"]
            assert historical["abstract"] == expected

    @pytest_asyncio.fixture
    async def historical_archive(self, client: partial):
        session = client(session_id="historical_session_archive_test")
        await session.ensure_exists()
        archive_uri = f"{session.uri}/history/archive_001"
        vfs = session._viking_fs
        await vfs.write_file(f"{archive_uri}/messages.jsonl", "", ctx=session.ctx)
        await vfs.write_file(
            f"{archive_uri}/.done", '{"enable_working_memory": true}', ctx=session.ctx
        )
        return session, archive_uri

    @pytest.mark.parametrize(
        "state",
        [
            "Sign-off complete.",
            "Record the current release decision.",
            "Capture the latest release status in one sentence.",
        ],
    )
    async def test_historical_archive_keeps_saved_state(
        self, historical_archive, monkeypatch, tmp_path, state
    ):
        session, archive_uri = historical_archive
        _override_wm_state_template(monkeypatch, tmp_path, state)
        overview = (
            f"# Working Memory\n\n## Session Title\nSign-off review\n\n## Current State\n{state}"
            "\nThe rollout is pending."
        )
        vfs = session._viking_fs
        await vfs.write_file(f"{archive_uri}/.overview.md", overview, ctx=session.ctx)
        await vfs.write_file(f"{archive_uri}/.abstract.md", state, ctx=session.ctx)
        assert (await session.get_session_archive("archive_001"))["abstract"] == state
        await vfs.write_file(
            f"{archive_uri}/.meta.json",
            json.dumps(
                {
                    "wm_template_descriptions": {"## Current State": state},
                    "wm_rendered_template_descriptions": {"## Current State": state},
                }
            ),
            ctx=session.ctx,
        )
        assert (await session.get_session_archive("archive_001"))["abstract"] == state
        assert await vfs.read_file(f"{archive_uri}/.abstract.md", ctx=session.ctx) == state

    async def test_next_commit_reads_previous_overview_with_unreadable_messages(
        self, historical_archive, monkeypatch
    ):
        session, archive_uri = historical_archive
        vfs = session._viking_fs
        description = (
            "_2-5 sentences MAX: What is the latest status? What is unresolved or pending? "
            "Immediate next steps. NOT a fact dump._"
        )
        overview = f"# Working Memory\n\n## Current State\n{description}\nSign-off pending."
        await vfs.write_file(f"{archive_uri}/.overview.md", overview, ctx=session.ctx)
        await vfs.write_file(f"{archive_uri}/.abstract.md", description, ctx=session.ctx)
        messages_uri = f"{archive_uri}/messages.jsonl"
        await vfs.rm(messages_uri, ctx=session.ctx)
        await vfs.mkdir(messages_uri, ctx=session.ctx)

        async def complete(self, prompt, **kwargs):
            assert overview in prompt
            return VLMResponse(
                tool_calls=[
                    ToolCall(
                        id="update",
                        name="update_working_memory",
                        arguments={
                            "sections": {
                                "Current State": {"op": "UPDATE", "content": "Sign-off complete."}
                            }
                        },
                    )
                ],
                finish_reason="tool_calls",
            )

        monkeypatch.setattr(VLMConfig, "get_completion_async", complete)
        session.add_message("user", [TextPart("The review passed.")])
        result = await session.commit_async()
        task = await _wait_for_task(result["task_id"])
        assert task["status"] == "completed", task
        assert await session._archives.read_abstract(archive_uri) == "Sign-off pending."
        assert (await session.get_session_archive("archive_002"))[
            "abstract"
        ] == "Sign-off complete."

    async def test_unreadable_bundled_prompt_allows_historical_read_and_next_commit(
        self, historical_archive, monkeypatch, tmp_path
    ):
        session, archive_uri = historical_archive
        manager = prompt_manager.PromptManager
        relative_path = "compression/ov_wm_v2.yaml"
        bundled_dir = manager._get_bundled_templates_dir()
        override = tmp_path / "overrides" / relative_path
        override.parent.mkdir(parents=True)
        override.write_bytes((bundled_dir / relative_path).read_bytes())
        load_template = manager.load_template

        def load_with_missing_bundled_prompt(self, prompt_id):
            if prompt_id == "compression.ov_wm_v2" and self.templates_dir == bundled_dir:
                raise FileNotFoundError(bundled_dir / relative_path)
            return load_template(self, prompt_id)

        monkeypatch.setattr(manager, "load_template", load_with_missing_bundled_prompt)
        monkeypatch.setenv(OPENVIKING_PROMPT_TEMPLATES_DIR_ENV, str(override.parent.parent))
        monkeypatch.setattr(prompt_manager, "_default_manager", None)
        wm._bundled_wm_template_descriptions.cache_clear()

        overview = "# Working Memory\n\n## Current State\n_Pending sign-off._"
        vfs = session._viking_fs
        await vfs.write_file(f"{archive_uri}/.overview.md", overview, ctx=session.ctx)

        archive = await session.get_session_archive("archive_001")
        assert archive["status"] == "completed"
        assert archive["abstract"] == "_Pending sign-off._"

        async def fake_generate(self, _messages, latest_archive_overview="", **kwargs):
            assert latest_archive_overview == overview
            return "# Working Memory\n\n## Current State\nSign-off complete."

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_generate)
        session.add_message("user", [TextPart("The review passed.")])
        session.add_message("assistant", [TextPart("I recorded the result.")])
        result = await session.commit_async()
        task = await _wait_for_task(result["task_id"])
        assert task["status"] == "completed", task
        archive = await session.get_session_archive("archive_002")
        assert archive["abstract"] == "Sign-off complete."

    @pytest.mark.parametrize(
        ("stored_body", "legacy", "expected"),
        [
            pytest.param("# Working Memory", False, "等待验收。", id="stale-heading"),
            pytest.param("# Working Memory\n", True, "等待验收。", id="legacy-stale-heading"),
            pytest.param(None, False, "等待验收。", id="missing"),
            pytest.param("", False, "等待验收。", id="empty-body"),
            pytest.param("已有摘要。", False, "已有摘要。", id="valid"),
            pytest.param(
                "# Working Memory\n已有摘要。",
                False,
                "# Working Memory\n已有摘要。",
                id="heading-with-body",
            ),
            pytest.param(
                "    # Working Memory\n",
                True,
                "    # Working Memory\n",
                id="legacy-indented-code",
            ),
        ],
    )
    async def test_get_session_archive_reads_historical_abstract(
        self, historical_archive, stored_body, legacy, expected
    ):
        session, archive_uri = historical_archive
        vfs = session._viking_fs
        abstract_uri = f"{archive_uri}/.abstract.md"
        overview = "# Working Memory\n\n## Current State\n等待验收。"
        await vfs.write_file(f"{archive_uri}/.overview.md", overview, ctx=session.ctx)

        if stored_body is None:
            assert not await vfs.exists(abstract_uri, ctx=session.ctx)
        else:
            stored = (
                stored_body if legacy else render_abstract_overview(0, archive_uri, stored_body)
            )
            await vfs.write_file(abstract_uri, stored, ctx=session.ctx)
            assert await vfs.read_file(abstract_uri, ctx=session.ctx) == stored

        archive = await session.get_session_archive("archive_001")
        assert archive["abstract"] == expected
        assert archive["overview"] == overview

        if stored_body is None:
            assert not await vfs.exists(abstract_uri, ctx=session.ctx)
        else:
            assert await vfs.read_file(abstract_uri, ctx=session.ctx) == stored

    async def test_get_session_archive_falls_back_when_abstract_read_fails(
        self, historical_archive, monkeypatch
    ):
        session, archive_uri = historical_archive
        vfs = session._viking_fs
        abstract_uri = f"{archive_uri}/.abstract.md"
        await vfs.write_file(
            f"{archive_uri}/.overview.md",
            "# Working Memory\n\n## Current State\n等待验收。",
            ctx=session.ctx,
        )
        await vfs.write_file(abstract_uri, "已有摘要。", ctx=session.ctx)

        original_read_file = vfs.read_file

        async def read_file(uri, *args, **kwargs):
            if uri == abstract_uri:
                raise OSError("abstract read failed")
            return await original_read_file(uri, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(vfs, "read_file", read_file)
            archive = await session.get_session_archive("archive_001")

        assert archive["abstract"] == "等待验收。"
        assert await vfs.read_file(abstract_uri, ctx=session.ctx) == "已有摘要。"

    @pytest.mark.parametrize(
        ("overview", "expected"),
        [
            pytest.param(
                "\n \n# Working Memory\n\n## Current State\n\n### Status\n- - -\n等待验收。\n后续信息。",
                "等待验收。",
                id="state-only",
            ),
            pytest.param(
                "# Working Memory\n\n## Session Title\n_验收流程_",
                "_验收流程_",
                id="title-only",
            ),
            pytest.param(
                "# Working Memory\n\n"
                "## Current State\n"
                "_2-5 sentences MAX: What is the latest status? What is unresolved or pending? "
                "Immediate next steps. NOT a fact dump._\n"
                "***\n\n"
                "## Session Title\n"
                "_A short and distinctive 5-10 word descriptive title for the session. "
                "Info-dense, no filler._",
                "",
                id="template-only",
            ),
            pytest.param(
                "# Working Memory\n\n## Current State\n## Key Facts & Decisions\n"
                "**Owner**: Review team",
                "Review team",
                id="other-section",
            ),
            pytest.param("# Working Memory", "", id="fixed-title-only"),
            pytest.param(
                "# Working Memory\n\nSign-off complete.",
                "Sign-off complete.",
                id="compact-prose",
            ),
            pytest.param(
                "# Session Summary\n\n**Status**: Review pending.",
                "Review pending.",
                id="legacy-bold-field",
            ),
            pytest.param(
                "# Session Summary\n\nArchived conversation.",
                "# Session Summary",
                id="legacy-first-line",
            ),
        ],
    )
    async def test_get_session_archive_extracts_abstract_from_overview(
        self, historical_archive, overview, expected
    ):
        session, archive_uri = historical_archive
        await session._viking_fs.write_file(
            f"{archive_uri}/.overview.md", overview, ctx=session.ctx
        )

        archive = await session.get_session_archive("archive_001")
        assert archive["abstract"] == expected

    async def test_get_session_archive_reports_missing_required_overview(self, historical_archive):
        session, archive_uri = historical_archive
        archive = await session.get_session_archive("archive_001")
        assert archive["status"] == "failed"
        assert archive["failure"]["stage"] == "archive_overview"
        assert archive["abstract"] == archive["overview"] == ""

        await session._viking_fs.write_file(f"{archive_uri}/.done", "{}", ctx=session.ctx)
        with pytest.raises(NotFoundError, match="Session archive not found: archive_001"):
            await session.get_session_archive("archive_001")

    async def test_get_session_archive_raises_for_missing_archive(self, client: partial):
        session = client(session_id="missing_session_archive_test")
        await session.ensure_exists()

        with pytest.raises(NotFoundError, match="Session archive not found: archive_999"):
            await session.get_session_archive("archive_999")
