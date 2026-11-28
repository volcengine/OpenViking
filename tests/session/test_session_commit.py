# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Commit tests"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.message import TextPart, ToolPart
from openviking.scheduling.expiry import (
    CallbackRequest,
    CallbackStatus,
    ExpiryScheduler,
    ItemIdentity,
)
from openviking.server.identity import RequestContext
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
from openviking.session.memory.dataclass import MemoryFile, ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_updater import (
    MemoryReadError,
    MemoryUpdateError,
    MemoryUpdateResult,
)
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.streaming_memory_updater import StreamingMemoryUpdaterConfig
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils.config import get_openviking_config


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    """Poll the task tracker until the task reaches a terminal state."""
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = await tracker.get(task_id)
        if task and task.status.value in ("completed", "failed"):
            return task.to_dict()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


async def _marker_exists(session, archive_uri: str, name: str) -> bool:
    try:
        await session._viking_fs.read_file(f"{archive_uri}/{name}", ctx=session.ctx)
        return True
    except Exception:
        return False


class TestCommit:
    """Test commit"""

    @pytest.mark.parametrize("with_writes", [False, True], ids=["delete-only", "mixed"])
    async def test_commit_preserves_memory_without_replacement(
        self, session_with_messages, monkeypatch, with_writes
    ):
        session = session_with_messages
        fs = session._viking_fs
        compressor = session._session_compressor
        config = get_openviking_config().memory
        monkeypatch.setattr(config, "extraction_output_format", "json")
        monkeypatch.setattr(config, "eager_prefetch", True)
        monkeypatch.setattr(compressor, "vikingdb", None)
        monkeypatch.setattr(
            "openviking.session.memory.streaming_memory_updater._streaming_memory_updater_registry",
            {},
        )
        monkeypatch.setattr(
            compressor,
            "streaming_memory_updater_config",
            StreamingMemoryUpdaterConfig(max_operations_per_update=1),
        )
        monkeypatch.setattr(
            "openviking.utils.embedding_utils.vectorize_directory_meta", AsyncMock()
        )

        root = f"viking://user/{session.ctx.user.user_id}/memories/entities/people"
        source, duplicate, canonical = [
            f"{root}/{name}.md" for name in ("source", "alias", "alice")
        ]
        original = MemoryFileUtils.write(
            MemoryFile(memory_type="entities", content="# Bob\nBob restores antique clocks.")
        )
        await fs.write_file(source, original, ctx=session.ctx)
        prefetched = [source]
        if with_writes:
            alias = MemoryFile(memory_type="entities", content="# Alice\nAlice is a teacher.")
            await fs.write_file(duplicate, MemoryFileUtils.write(alias), ctx=session.ctx)
            prefetched.append(duplicate)
        monkeypatch.setattr(
            SessionExtractContextProvider, "search_files", AsyncMock(return_value=prefetched)
        )

        async def complete(messages, **kwargs):
            reads = [json.loads(m["content"]) for m in messages if m["content"].startswith("{")]
            page_ids = {
                read["args"]["uri"]: read["result"]["page_id"]
                for read in reads
                if read.get("tool_call_name") == "read"
            }
            plan = {
                "delete_ids": [{"delete_page_id": page_ids[source], "replacement_page_id": None}]
            }
            if with_writes:
                plan["entities"] = [
                    {
                        "page_id": 100,
                        "category": "people",
                        "name": "alice",
                        "content": "Alice is a teacher.",
                    }
                ]
                plan["delete_ids"].append(
                    {"delete_page_id": page_ids[duplicate], "replacement_page_id": 100}
                )
            return json.dumps(plan)

        completion = AsyncMock(side_effect=complete)
        vlm = SimpleNamespace(model="local-test", max_tokens=4096, get_completion_async=completion)
        monkeypatch.setattr(compressor.vlm_resolver, "get_vlm", AsyncMock(return_value=vlm))

        commit = await session.commit_async(memory_policy={"memory_types": ["entities"]})
        task = await _wait_for_task(commit["task_id"])
        assert task["status"] == "completed", task
        assert await _marker_exists(session, commit["archive_uri"], ".done")
        completion.assert_awaited_once()
        raw_diff = await fs.read_file(f"{commit['archive_uri']}/memory_diff.json", ctx=session.ctx)
        diff = json.loads(raw_diff)
        operations = diff["operations"]
        assert [entry["uri"] for entry in operations["adds"]] == (
            [canonical] if with_writes else []
        )
        if with_writes:
            assert not await fs.exists(duplicate, ctx=session.ctx)
            assert "Alice is a teacher." in await fs.read_file(canonical, ctx=session.ctx)
        assert await fs.exists(source, ctx=session.ctx), "unreplaced memory was deleted"
        assert await fs.read_file(source, ctx=session.ctx) == original
        [skipped] = diff["skipped_operations"]
        assert (skipped["uri"], skipped["reason_code"]) == (source, "missing_delete_replacement")
        assert task["result"]["memory_extraction"]["skipped_operations"] == [skipped]
        assert [entry["uri"] for entry in operations["deletes"]] == (
            [duplicate] if with_writes else []
        )

    async def test_commit_completes_after_memory_parse_retries(
        self, session_with_messages, monkeypatch
    ):
        session = session_with_messages
        completion = AsyncMock(return_value="invalid code!")
        vlm = SimpleNamespace(model="local-test", max_tokens=4096, get_completion_async=completion)
        resolver = session._session_compressor.vlm_resolver
        monkeypatch.setattr(resolver, "get_vlm", AsyncMock(return_value=vlm))
        monkeypatch.setattr(SessionExtractContextProvider, "prefetch", AsyncMock(return_value=[]))
        result = await session.commit_async()
        task = await _wait_for_task(result["task_id"])
        assert task["status"] == "completed", task
        assert not any(task["result"]["memories_extracted"].values())
        assert completion.await_count >= 2
        assert await _marker_exists(session, result["archive_uri"], ".done")
        assert not await _marker_exists(session, result["archive_uri"], ".failed.json")

    async def test_failed_commit_preserves_successful_memory_writes(
        self, session_with_messages, monkeypatch
    ):
        session = session_with_messages
        fs = session._viking_fs
        compressor = session._session_compressor
        # Index workers are outside the file-only commit fixture.
        monkeypatch.setattr(compressor, "vikingdb", None)
        monkeypatch.setattr(
            "openviking.utils.embedding_utils.vectorize_directory_meta", AsyncMock()
        )
        monkeypatch.setattr(compressor.vlm_resolver, "get_vlm", AsyncMock())
        root = f"viking://user/{session.ctx.user.user_id}/memories/events"
        a, b, c = [f"{root}/{name}.md" for name in "abc"]
        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_type="events",
                    uris=[a, b, c],
                    memory_fields={"event_name": "meeting", "summary": "Review", "ranges": "0"},
                )
            ],
            delete_file_contents=[],
            errors=[],
        )
        run = AsyncMock(return_value=(operations, []))
        react = SimpleNamespace(run=run)
        monkeypatch.setattr(compressor, "_get_or_create_react", lambda **kw: react)
        write = fs.write_file

        async def write_except_b(uri, *args, **kwargs):
            if uri == b:
                raise OSError("injected write failure")
            return await write(uri, *args, **kwargs)

        monkeypatch.setattr(fs, "write_file", write_except_b)
        commit = await session.commit_async()
        task = await _wait_for_task(commit["task_id"])
        assert task["status"] == "failed", task
        assert f"{b}: injected write failure" in task["error"]
        assert await fs.exists(a, ctx=session.ctx) and await fs.exists(c, ctx=session.ctx)
        assert not await fs.exists(b, ctx=session.ctx)
        run.assert_awaited_once()

    async def test_memory_apply_failure_does_not_reextract(
        self, session_with_messages, monkeypatch
    ):
        import openviking.session.session as session_module

        monkeypatch.setattr(session_module, "_MEMORY_EXTRACTION_RETRY_BASE_DELAY_SECONDS", 0)
        session = session_with_messages
        uri = "viking://user/default/memories/preferences/style.md"
        result = MemoryUpdateResult()
        result.add_error(uri, MemoryReadError(uri, TimeoutError("read timeout")))
        extract = AsyncMock(side_effect=MemoryUpdateError(result))
        session._session_compressor.extract_long_term_memories = extract
        committed = await session.commit_async()
        task = await _wait_for_task(committed["task_id"])
        assert extract.await_count == 1
        assert task["status"] == "failed"
        meta = await session._archives.read_meta(committed["archive_uri"])
        assert not meta.get("completed_memory_steps", {}).get("long_term")

    async def test_commit_preserves_unicode_separators_and_accepts_later_messages(
        self, wm_session_with_messages: Session
    ):
        """Unicode separators inside a message must not split its JSONL record."""
        unicode_text = "before\u2028middle\u2029after\u0085tail"
        wm_session_with_messages.add_message("assistant", [TextPart(unicode_text)])
        wm_session_with_messages.add_message("user", [TextPart("continue")])

        result = await wm_session_with_messages.commit_async()

        assert isinstance(result, dict)
        assert result.get("status") == "accepted"
        assert "session_id" in result
        assert result.get("task_id") is not None
        assert "memory_diff_uri" not in result
        assert "memories_extracted" not in result
        archive_content = await wm_session_with_messages._viking_fs.read_file(
            f"{result['archive_uri']}/messages.jsonl",
            ctx=wm_session_with_messages.ctx,
        )
        archived_messages = [
            json.loads(line) for line in archive_content.split("\n") if line.strip()
        ]
        assert unicode_text in {
            part["text"]
            for message in archived_messages
            for part in message["parts"]
            if part["type"] == "text"
        }

    async def test_idle_callback_commits_current_backlog_without_activity_recheck(
        self,
        wm_session_with_messages: Session,
        service: OpenVikingService,
        monkeypatch,
    ):
        """An expiry callback archives the latest lock-protected snapshot."""
        session = wm_session_with_messages
        await session.update_config(
            auto_commit_policy={"idle_timeout_seconds": 300, "keep_recent_count": 2},
            update_auto_commit_policy=True,
        )
        session.meta.keep_recent_count = 2
        await session._save_meta()
        scheduler = MagicMock(spec=ExpiryScheduler)
        service.sessions.configure_expiry(scheduler)
        handler = scheduler.register_handler.call_args.args[1]
        session = await service.sessions.get(session.session_id, session.ctx)
        await session.add_message_async("user", [TextPart("latest activity")])
        report = scheduler.report.await_args.args[0]
        last_message_at = parse_iso_datetime(session.meta.last_message_at)
        assert report.identity == ItemIdentity(
            "session", session.ctx.account_id, session.ctx.user.user_id, session.session_id
        )
        assert report.due_at_ms == int(last_message_at.timestamp() * 1000) + 300_000

        original_commit = Session.commit_async
        commit_results = []

        async def append_before_commit(loaded_session, **kwargs):
            scheduler.report.side_effect = RuntimeError("expiry unavailable")
            await loaded_session.add_message_async("user", [TextPart("append after idle check")])
            result = await original_commit(loaded_session, **kwargs)
            commit_results.append(result)
            return result

        monkeypatch.setattr(Session, "commit_async", append_before_commit)
        callback = await handler.handle(CallbackRequest(report.identity))
        assert callback.status is CallbackStatus.COMPLETED
        assert callback.reason == "accepted"
        assert scheduler.report.await_count == 2
        result = commit_results[0]
        assert result["status"] == "accepted"
        archive_content = await session._viking_fs.read_file(
            f"{result['archive_uri']}/messages.jsonl", ctx=session.ctx
        )
        archived_messages = [json.loads(line) for line in archive_content.split("\n") if line]
        assert len(archived_messages) == 6
        assert archived_messages[-1]["parts"][0]["text"] == "append after idle check"
        persisted = await service.sessions.get(session.session_id, session.ctx)
        assert persisted.messages == []
        assert persisted.meta.keep_recent_count == 2
        assert persisted.meta.last_idle_commit_accepted_at
        phase1 = await persisted._read_phase1_meta(result["archive_uri"])
        assert phase1["status"] == "ready"

    async def test_phase2_splits_with_the_committed_auto_commit_policy(
        self,
        wm_session_with_messages: Session,
        monkeypatch,
    ):
        await wm_session_with_messages.update_config(
            auto_commit_policy={
                "pending_token_threshold": 0,
                "message_count_threshold": 1,
            },
            update_auto_commit_policy=True,
        )
        working_memory_batches = []

        async def generate_summary(
            _session,
            messages,
            latest_archive_overview="",
            checkpoint_requests=None,
            **kwargs,
        ):
            del checkpoint_requests
            working_memory_batches.append([message.id for message in messages])
            return f"{latest_archive_overview}\n{messages[0].id}"

        monkeypatch.setattr(Session, "_generate_archive_summary_async", generate_summary)
        monkeypatch.setattr(
            Session,
            "_get_vlm_config",
            AsyncMock(return_value=SimpleNamespace(is_available=lambda: True)),
        )
        extract_long_term = AsyncMock(return_value=[])
        wm_session_with_messages._session_compressor.extract_long_term_memories = extract_long_term

        result = await wm_session_with_messages.commit_async(
            memory_policy={
                "memory_types": ["events"],
                "working_memory": {"enabled": True},
            }
        )
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        phase1 = await wm_session_with_messages._read_phase1_meta(result["archive_uri"])
        assert phase1["queue_message"]["auto_commit_policy"]["message_count_threshold"] == 1
        assert len(working_memory_batches) == 1
        assert extract_long_term.await_count == 4
        assert all(len(call.kwargs["messages"]) == 1 for call in extract_long_term.await_args_list)

    @pytest.mark.parametrize("threshold", ["message_count_threshold", "pending_token_threshold"])
    @pytest.mark.parametrize(
        "memory_types,agent_evolution_enabled,expected_calls",
        [
            (None, True, 8),
            (["experiences"], True, 1),
            (["events", "experiences"], True, 8),
            (["trajectories"], True, 0),
            (["events"], True, 7),
            (None, False, 7),
        ],
    )
    async def test_phase2_preserves_execution_context_without_changing_user_memory_batches(
        self,
        session: Session,
        monkeypatch,
        threshold,
        memory_types,
        agent_evolution_enabled,
        expected_calls,
    ):
        session._agent_evolution_enabled_provider = lambda: agent_evolution_enabled
        for role, parts in [
            ("user", [TextPart("# OpenViking Batch Training CaseSpec v1\nTask metadata")]),
            ("user", [TextPart("system:\nConfirm the user before changing the booking.")]),
            ("user", [TextPart("Change my booking to flight AB123.")]),
            ("assistant", [TextPart("Please confirm flight AB123 and the 30 dollar fee.")]),
            ("user", [TextPart("I confirm flight AB123 and the 30 dollar fee.")]),
            (
                "user",
                [
                    ToolPart(
                        tool_id="update-1",
                        tool_name="update_booking",
                        tool_input={"flight": "AB123", "fee": 30},
                        tool_output='{"flight":"AB123","status":"confirmed"}',
                        tool_status="completed",
                    )
                ],
            ),
            ("user", [TextPart('# OpenViking OutcomeEvaluation\n{"passed": true}')]),
        ]:
            await session.add_message_async(role, parts)
        original_messages = list(session.messages)
        await session.update_config(
            auto_commit_policy={
                "pending_token_threshold": int(threshold == "pending_token_threshold"),
                "message_count_threshold": int(threshold == "message_count_threshold"),
            },
            update_auto_commit_policy=True,
        )
        working_memory_batches = []

        async def generate_summary(_session, messages, **kwargs):
            working_memory_batches.append([message.id for message in messages])
            return "Summary"

        monkeypatch.setattr(Session, "_generate_archive_summary_async", generate_summary)
        monkeypatch.setattr(
            Session,
            "_get_vlm_config",
            AsyncMock(return_value=SimpleNamespace(is_available=lambda: True)),
        )
        extract_long_term = AsyncMock(return_value=[])
        session._session_compressor.extract_long_term_memories = extract_long_term
        policy = {"working_memory": {"enabled": True}}
        if memory_types is not None:
            policy["memory_types"] = memory_types

        committed = await session.commit_async(memory_policy=policy, keep_recent_count=0)
        task = await _wait_for_task(committed["task_id"])

        assert task["status"] == "completed"
        assert extract_long_term.await_count == expected_calls
        assert working_memory_batches == [[message.id for message in original_messages]]
        if expected_calls in (1, 8):
            extracted_messages = extract_long_term.await_args.kwargs["messages"]
            assert [message.id for message in extracted_messages] == [
                message.id for message in original_messages
            ]
            assert [message.parts for message in extracted_messages] == [
                message.parts for message in original_messages
            ]
            assert extract_long_term.await_args.kwargs["allowed_memory_types"] == {
                "cases",
                "trajectories",
                "experiences",
            }
            user_calls = extract_long_term.await_args_list[:-1]
            assert len(user_calls) == expected_calls - 1
            for call in user_calls:
                assert len(call.kwargs["messages"]) == 1
                assert not {"cases", "trajectories", "experiences"}.intersection(
                    call.kwargs["allowed_memory_types"]
                )
            if user_calls:
                meta = await session._archives.read_meta(committed["archive_uri"])
                for step in ("long_term", "long_term_user", "long_term_agent"):
                    assert set(meta["completed_memory_steps"][step]) == {
                        message.id for message in original_messages
                    }
        else:
            assert all(
                len(call.kwargs["messages"]) == 1 for call in extract_long_term.await_args_list
            )

    async def test_commit_task_reports_intentionally_skipped_memory_operations(
        self,
        wm_session_with_messages: Session,
        monkeypatch,
    ):
        fs = wm_session_with_messages._viking_fs
        write_file = AsyncMock(wraps=fs.write_file)
        monkeypatch.setattr(fs, "write_file", write_file)

        async def extract_long_term_memories(**kwargs):
            archive_uri = kwargs["archive_uri"]
            await wm_session_with_messages._viking_fs.write_file(
                uri=f"{archive_uri}/memory_diff.json",
                content=json.dumps(
                    {
                        "archive_uri": archive_uri,
                        "operations": {"adds": [], "updates": [], "deletes": []},
                        "summary": {
                            "total_adds": 0,
                            "total_updates": 0,
                            "total_deletes": 0,
                            "total_skipped": 1,
                        },
                        "skipped_operations": [
                            {
                                "memory_type": "preferences",
                                "page_id": 102,
                                "reason_code": "peer_not_allowed",
                                "reason": "Target peer is outside the allowed memory scope",
                            }
                        ],
                    }
                ),
                ctx=wm_session_with_messages.ctx,
            )
            return []

        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            side_effect=extract_long_term_memories
        )

        commit_result = await wm_session_with_messages.commit_async()
        task_result = await _wait_for_task(commit_result["task_id"])

        assert commit_result["status"] == "accepted"
        assert task_result["status"] == "completed"
        written_uris = [
            call.args[0] if call.args else call.kwargs["uri"] for call in write_file.await_args_list
        ]
        assert written_uris.count(f"{commit_result['archive_uri']}/memory_diff.json") == 1
        assert task_result["result"]["memory_extraction"] == {
            "skipped": 1,
            "skipped_operations": [
                {
                    "memory_type": "preferences",
                    "page_id": 102,
                    "reason_code": "peer_not_allowed",
                    "reason": "Target peer is outside the allowed memory scope",
                }
            ],
        }

    async def test_recovered_commit_task_reads_existing_skipped_memory_operations(
        self,
        wm_session_with_messages: Session,
        monkeypatch,
    ):
        original_prepare = Session._prepare_phase2_archive_messages

        async def prepare_with_completed_long_term(self, archive_uri, current_messages):
            (
                messages,
                coverage_start_archive,
                coverage_end_archive,
                covered_failed_archives,
                completed_memory_steps,
            ) = await original_prepare(self, archive_uri, current_messages)
            completed_memory_steps.setdefault("long_term", set()).update(
                message.id for message in messages
            )
            await self._viking_fs.write_file(
                uri=f"{archive_uri}/memory_diff.json",
                content=json.dumps(
                    {
                        "archive_uri": archive_uri,
                        "operations": {"adds": [], "updates": [], "deletes": []},
                        "summary": {
                            "total_adds": 0,
                            "total_updates": 0,
                            "total_deletes": 0,
                            "total_skipped": 1,
                        },
                        "skipped_operations": [
                            {
                                "memory_type": "preferences",
                                "page_id": 102,
                                "reason_code": "peer_not_allowed",
                                "reason": "Target peer is outside the allowed memory scope",
                            }
                        ],
                    }
                ),
                ctx=self.ctx,
            )
            return (
                messages,
                coverage_start_archive,
                coverage_end_archive,
                covered_failed_archives,
                completed_memory_steps,
            )

        monkeypatch.setattr(
            Session,
            "_prepare_phase2_archive_messages",
            prepare_with_completed_long_term,
        )
        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock()

        commit_result = await wm_session_with_messages.commit_async()
        task_result = await _wait_for_task(commit_result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["memory_extraction"] == {
            "skipped": 1,
            "skipped_operations": [
                {
                    "memory_type": "preferences",
                    "page_id": 102,
                    "reason_code": "peer_not_allowed",
                    "reason": "Target peer is outside the allowed memory scope",
                }
            ],
        }
        wm_session_with_messages._session_compressor.extract_long_term_memories.assert_not_awaited()

    async def test_commit_default_disables_agent_memory_but_keeps_archive(
        self, wm_session_with_messages: Session
    ):
        async def account_setting_provider() -> bool:
            return False

        wm_session_with_messages._agent_evolution_enabled_provider = account_setting_provider
        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await wm_session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert result["archived"] is True
        assert task_result["status"] == "completed"
        assert task_result["result"]["agent_evolution_enabled"] is False
        assert "cases" not in task_result["result"]["effective_memory_types"]
        assert "trajectories" not in task_result["result"]["effective_memory_types"]
        assert "experiences" not in task_result["result"]["effective_memory_types"]
        assert task_result["result"]["agent_memory_skip_reason"] == ("agent_evolution_disabled")
        call_kwargs = (
            wm_session_with_messages._session_compressor.extract_long_term_memories.call_args.kwargs
        )
        assert call_kwargs["agent_evolution_enabled"] is False
        assert "cases" not in call_kwargs["allowed_memory_types"]
        assert "trajectories" not in call_kwargs["allowed_memory_types"]
        assert "experiences" not in call_kwargs["allowed_memory_types"]

    async def test_commit_uses_account_setting_and_enables_agent_memory(
        self, wm_session_with_messages: Session
    ):
        wm_session_with_messages._agent_evolution_enabled_provider = lambda: True
        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await wm_session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["agent_evolution_enabled"] is True
        assert "cases" in task_result["result"]["effective_memory_types"]
        assert "trajectories" in task_result["result"]["effective_memory_types"]
        assert "experiences" in task_result["result"]["effective_memory_types"]
        call_kwargs = (
            wm_session_with_messages._session_compressor.extract_long_term_memories.call_args.kwargs
        )
        assert call_kwargs["agent_evolution_enabled"] is True
        assert call_kwargs["allowed_memory_types"] is None

    async def test_commit_reads_latest_user_memory_policy_when_session_has_no_override(
        self, session_with_messages: Session
    ):
        memory_policy_provider = AsyncMock(
            return_value={
                "memory_types": ["profile", "soul", "identity"],
            }
        )
        session_with_messages._memory_policy_provider = memory_policy_provider
        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["effective_memory_types"] == ["profile"]
        call_kwargs = (
            session_with_messages._session_compressor.extract_long_term_memories.call_args.kwargs
        )
        assert call_kwargs["allowed_memory_types"] == {"profile"}
        assert call_kwargs["allowed_peer_ids"] == set()
        memory_policy_provider.assert_awaited_once_with()

    async def test_disabled_agent_evolution_keeps_working_memory(
        self, wm_session_with_messages: Session, monkeypatch
    ):
        wm_session_with_messages._agent_evolution_enabled_provider = lambda: False
        summary_called = False

        async def fake_summary(_session, messages, latest_archive_overview="", **kwargs):
            nonlocal summary_called
            del messages, latest_archive_overview
            summary_called = True
            return "# Working Memory\n\nAgent memory production is disabled."

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_summary)
        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await wm_session_with_messages.commit_async(
            memory_policy={
                "memory_types": ["cases", "trajectories", "experiences"],
                "working_memory": {"enabled": True},
            }
        )
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert summary_called is True
        archive_uri = task_result["result"]["archive_uri"]
        assert await _marker_exists(wm_session_with_messages, archive_uri, ".overview.md")
        wm_session_with_messages._session_compressor.extract_long_term_memories.assert_not_awaited()

    async def test_commit_reports_session_skills_separately(
        self, wm_session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = True
        config.vlm = SimpleNamespace(is_available=lambda: False)
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        wm_session_with_messages._agent_evolution_enabled_provider = lambda: True
        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value={
                "contexts": [],
                "session_skills": [{"uri": "viking://user/test/skills/code-review"}],
            }
        )

        result = await wm_session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["memories_extracted"] == {}
        assert task_result["result"]["session_skills_extracted"] == 1
        assert task_result["result"]["session_skill_uris"] == [
            "viking://user/test/skills/code-review"
        ]
        assert "memory_diff_uri" not in task_result["result"]
        wm_session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()
        call_kwargs = (
            wm_session_with_messages._session_compressor.extract_long_term_memories.call_args.kwargs
        )
        assert call_kwargs["allowed_memory_types"] is None

    async def test_commit_skips_session_skills_without_execution_memory_type(
        self, wm_session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = True
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        wm_session_with_messages._meta.memory_policy = {"memory_types": ["profile"]}

        result = await wm_session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["memories_extracted"] == {}
        assert task_result["result"]["session_skills_extracted"] == 0
        assert "memory_diff_uri" not in task_result["result"]
        wm_session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()

    async def test_commit_skips_session_skill_extraction_when_disabled(
        self, wm_session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = False
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await wm_session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["session_skills_extracted"] == 0
        assert task_result["result"]["session_skill_uris"] == []
        assert "memory_diff_uri" not in task_result["result"]
        wm_session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()

    async def test_commit_can_skip_working_memory_summary(
        self, wm_session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = False
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        summary_called = False

        async def fake_summary(messages, latest_archive_overview="", **kwargs):
            nonlocal summary_called
            del messages, latest_archive_overview
            summary_called = True
            return "should not be written"

        async def fake_extract(*args, **kwargs):
            del args
            assert kwargs.get("latest_archive_overview", "") == ""
            return []

        wm_session_with_messages._generate_archive_summary_async = fake_summary
        wm_session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            side_effect=fake_extract
        )

        result = await wm_session_with_messages.commit_async(
            memory_policy={"working_memory": {"enabled": False}}
        )
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert summary_called is False
        archive_uri = task_result["result"]["archive_uri"]
        assert not await _marker_exists(wm_session_with_messages, archive_uri, ".overview.md")
        assert not await _marker_exists(wm_session_with_messages, archive_uri, ".abstract.md")
        context = await wm_session_with_messages.get_session_context()
        assert context["latest_archive_overview"] == ""
        assert context["messages"] == []
        wm_session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()

    async def test_commit_routes_peer_memory_with_single_full_context_pass(
        self,
        client,
        monkeypatch,
    ):
        """Peer memory uses one full-context extraction and operation-level routing."""
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = True
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session = client(session_id="peer_memory_role_routing_test")
        await session.ensure_exists()
        long_term_calls: list[dict] = []

        async def fake_summary(messages, latest_archive_overview="", **kwargs):
            del messages, latest_archive_overview
            return "Invoice support summary"

        async def fake_extract(
            *,
            messages,
            ctx,
            allowed_memory_types,
            allow_self_memory=True,
            peer_memory_enabled=True,
            allowed_peer_ids=None,
            **kwargs,
        ):
            del ctx, kwargs
            long_term_calls.append(
                {
                    "allowed_memory_types": set(allowed_memory_types or set()),
                    "allow_self_memory": allow_self_memory,
                    "peer_memory_enabled": peer_memory_enabled,
                    "allowed_peer_ids": set(allowed_peer_ids or set()),
                    "roles": [message.role for message in messages],
                    "peer_ids": [message.peer_id for message in messages],
                }
            )
            return []

        monkeypatch.setattr(session, "_generate_archive_summary_async", fake_summary)
        monkeypatch.setattr(session._session_compressor, "extract_long_term_memories", fake_extract)

        session.add_message(
            "user",
            [TextPart("我是 Alice，后续发票问题请优先邮件联系我，邮箱是 alice@example.com。")],
            peer_id="web-visitor-alice",
        )
        session.add_message(
            "assistant",
            [TextPart("收到，我会优先通过邮件联系你，并继续跟进发票问题。")],
            peer_id="web-visitor-alice",
        )

        session._meta.memory_policy = {
            "self": {"enabled": False},
            "peer": {"enabled": True},
            "memory_types": ["profile"],
        }

        result = await session.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["memories_extracted"] == {}
        assert long_term_calls == [
            {
                "allowed_memory_types": {
                    "profile",
                },
                "allow_self_memory": False,
                "peer_memory_enabled": True,
                "allowed_peer_ids": {"web-visitor-alice"},
                "roles": ["user", "assistant"],
                "peer_ids": ["web-visitor-alice", "web-visitor-alice"],
            },
        ]

    async def test_successive_commits_write_their_own_memories(self, session, monkeypatch):
        user = session.ctx.user.user_id
        preferences = {"editor": "I use Neovim for Go", "terminal": "I use tmux on servers"}

        async def extract_preferences(*, messages, **_kwargs):
            conversation = next(
                message["content"]
                for message in messages
                if message["role"] == "user" and "**Session Time:**" in message["content"]
            )
            [(topic, preference)] = [
                (topic, preference)
                for topic, preference in preferences.items()
                if preference in conversation
            ]
            return (
                f"sdk.create_preferences(user={user!r}, topic={topic!r}, content={preference!r})\n"
                "sdk.commit()"
            )

        vlm = SimpleNamespace(
            model="local-test", max_tokens=4096, get_completion_async=extract_preferences
        )
        compressor = session._session_compressor
        monkeypatch.setattr(compressor.vlm_resolver, "get_vlm", AsyncMock(return_value=vlm))
        # Give this test a fresh updater that flushes each write immediately.
        monkeypatch.setattr(
            "openviking.session.memory.streaming_memory_updater._streaming_memory_updater_registry",
            {},
        )
        monkeypatch.setattr(
            compressor,
            "streaming_memory_updater_config",
            StreamingMemoryUpdaterConfig(max_operations_per_update=1),
        )
        # The session fixture persists files but does not process index work.
        monkeypatch.setattr(compressor, "vikingdb", None)
        monkeypatch.setattr(
            "openviking.utils.embedding_utils.vectorize_directory_meta", AsyncMock()
        )

        archive_uris = []
        for preference in preferences.values():
            message = await session.add_message_async("user", [TextPart(preference)])
            commit = await session.commit_async(memory_policy={"memory_types": ["preferences"]})
            task = await _wait_for_task(commit["task_id"])
            assert task["status"] == "completed", task
            assert sum(task["result"]["memories_extracted"].values()) == 1
            archive_uri = commit["archive_uri"]
            archive_uris.append(archive_uri)
            archive = await session.get_session_archive(archive_uri.rsplit("/", 1)[-1])
            assert [item["id"] for item in archive["messages"]] == [message.id]

        assert len(set(archive_uris)) == 2
        root = f"viking://user/{user}/memories/preferences/{user}"
        for topic, preference in preferences.items():
            content = await session._viking_fs.read_file(f"{root}/{topic}.md", ctx=session.ctx)
            assert preference in content
        assert session.messages == []

    async def test_commit_empty_session(self, session: Session):
        """Test committing empty session"""
        # Empty session commit should not raise error
        result = await session.commit_async()

        assert isinstance(result, dict)
        assert result.get("archived") is False

    async def test_commit_multiple_times(self, client, service):
        """Later commits use updated account settings while preserving Working Memory."""
        manager = service.runtime_config_manager
        vlm = SimpleNamespace(
            is_available=lambda: True,
            max_tokens=4096,
            get_completion_async=AsyncMock(return_value="# Working Memory\n\nAccount summary."),
        )
        service.sessions._vlm_resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=vlm))
        session = client(session_id="multi_commit_test")
        await session.ensure_exists()
        extract = AsyncMock(return_value=[])
        session._session_compressor.extract_long_term_memories = extract
        policy = {"memory_types": ["profile"], "working_memory": {"enabled": True}}
        await manager.patch_account(
            session.ctx.account_id,
            {"output_language_override": "en", "memory": {"extraction_enabled": False}},
        )

        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = await session.commit_async(memory_policy=policy)
        assert result1.get("status") == "accepted"
        assert (await _wait_for_task(result1["task_id"]))["status"] == "completed"
        assert await session._archives.read_overview(result1["archive_uri"])
        extract.assert_not_awaited()

        await manager.patch_account(
            session.ctx.account_id,
            {"output_language_override": "zh-CN", "memory": {"extraction_enabled": True}},
        )
        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = await session.commit_async(memory_policy=policy)
        assert result2.get("status") == "accepted"
        assert (await _wait_for_task(result2["task_id"]))["status"] == "completed"
        assert result1["archive_uri"] != result2["archive_uri"]
        assert session.messages == []
        extract.assert_awaited_once()
        assert extract.await_args.kwargs["ctx"].account_id == session.ctx.account_id
        prompts = str(vlm.get_completion_async.call_args_list)
        assert "Write all section content in en." in prompts
        assert "Write all section content in zh-CN." in prompts

    async def test_commit_keep_recent_count_retains_live_tail_and_resets_pending_tokens(
        self,
        client,
        service: OpenVikingService,
        request_context: RequestContext,
        monkeypatch,
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = False
        config.vlm = SimpleNamespace(is_available=lambda: False)
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session = client(session_id="commit_keep_recent_count_test")
        await session.ensure_exists()
        session._meta.memory_policy = {"working_memory": {"enabled": True}}
        session._session_compressor.extract_long_term_memories = AsyncMock(return_value=[])

        session.add_message("user", [TextPart("Round 1 user")])
        session.add_message("assistant", [TextPart("Round 1 assistant")])
        session.add_message("user", [TextPart("Round 2 user")])
        session.add_message("assistant", [TextPart("Round 2 assistant")])

        result = await session.commit_async(keep_recent_count=2, enable_working_memory=True)
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert len(session.messages) == 2
        assert [message.parts[0].text for message in session.messages] == [
            "Round 2 user",
            "Round 2 assistant",
        ]

        persisted = await service.sessions.get(session.session_id, request_context)
        assert persisted.meta.pending_tokens == 0

        context = await session.get_session_context()
        assert context["latest_archive_overview"]
        assert [message["parts"][0]["text"] for message in context["messages"]] == [
            "Round 2 user",
            "Round 2 assistant",
        ]

    async def test_commit_uses_latest_archive_overview_for_summary_and_extraction(
        self, client, monkeypatch
    ):
        """Second commit should pass the latest completed archive overview into Phase 2."""
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = False
        config.vlm = SimpleNamespace(is_available=lambda: False)
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session = client(session_id="latest_overview_threading_test")
        await session.ensure_exists()
        session._meta.memory_policy = {
            "peer": {"enabled": False},
            "memory_types": ["profile"],
            "working_memory": {"enabled": True},
        }
        session._session_compressor.extract_long_term_memories = AsyncMock(return_value=[])

        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = await session.commit_async(enable_working_memory=True)
        await _wait_for_task(result1["task_id"])

        previous_overview = await session._archives.read_overview(result1["archive_uri"])
        seen: dict[str, str] = {}

        session_type = type(session)
        original_generate = session_type._generate_archive_summary_async

        async def capture_generate(self, messages, latest_archive_overview="", **kwargs):
            seen["summary"] = latest_archive_overview
            return await original_generate(
                self,
                messages,
                latest_archive_overview=latest_archive_overview,
                **kwargs,
            )

        async def capture_extract(*args, **kwargs):
            seen["extract"] = kwargs.get("latest_archive_overview", "")
            return []

        monkeypatch.setattr(session_type, "_generate_archive_summary_async", capture_generate)
        session._session_compressor.extract_long_term_memories = capture_extract

        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = await session.commit_async(enable_working_memory=True)
        task_result = await _wait_for_task(result2["task_id"])

        assert task_result["status"] == "completed"
        assert seen["summary"] == previous_overview
        assert seen["extract"] == previous_overview

    async def test_commit_failed_after_long_term_extraction_failure_does_not_block(
        self, client, monkeypatch
    ):
        """Binary archive outcome: if long-term extraction fails (after retries),
        the whole archive is marked .failed.json and skipped — there is no
        partial state — but a failed archive must not block the next commit.
        """
        session = client(session_id="failed_archive_does_not_block_commit")
        await session.ensure_exists()

        extract = AsyncMock(
            return_value=[], side_effect=RuntimeError("synthetic extraction failure")
        )
        monkeypatch.setattr(session._session_compressor, "extract_long_term_memories", extract)

        session.add_message("user", [TextPart("First round message")])
        result = await session.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "failed"

        archive_uri = result["archive_uri"]
        assert await _marker_exists(session, archive_uri, ".failed.json")
        assert not await _marker_exists(session, archive_uri, ".done")
        assert not await _marker_exists(session, archive_uri, ".partial.json")

        failed_payload = json.loads(
            await session._viking_fs.read_file(
                f"{archive_uri}/.failed.json",
                ctx=session.ctx,
            )
        )
        assert failed_payload.get("skipped") is True
        assert "synthetic extraction failure" in failed_payload["error"]

        extract.side_effect = None
        message = await session.add_message_async("user", [TextPart("Second round message")])
        second = await session.commit_async()
        second_task = await _wait_for_task(second["task_id"])
        assert second_task["status"] == "completed", second_task
        assert second["archive_uri"] != archive_uri
        archive = await session.get_session_archive(second["archive_uri"].rsplit("/", 1)[-1])
        assert [item["id"] for item in archive["messages"]] == [message.id]
