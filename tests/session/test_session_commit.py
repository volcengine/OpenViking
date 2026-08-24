# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Commit tests"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from openviking.message import TextPart
from openviking.server.identity import RequestContext
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session


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

    async def test_commit_preserves_unicode_separators_and_accepts_later_messages(
        self, session_with_messages: Session
    ):
        """Unicode separators inside a message must not split its JSONL record."""
        unicode_text = "before\u2028middle\u2029after\u0085tail"
        session_with_messages.add_message("assistant", [TextPart(unicode_text)])
        session_with_messages.add_message("user", [TextPart("continue")])

        result = await session_with_messages.commit_async()

        assert isinstance(result, dict)
        assert result.get("status") == "accepted"
        assert "session_id" in result
        assert result.get("task_id") is not None
        assert "memory_diff_uri" not in result
        assert "memories_extracted" not in result
        archive_content = await session_with_messages._viking_fs.read_file(
            f"{result['archive_uri']}/messages.jsonl",
            ctx=session_with_messages.ctx,
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

    async def test_commit_extracts_memories(
        self,
        session_with_messages: Session,
        service: OpenVikingService,
    ):
        """Test commit kicks off background memory extraction"""

        async def extract_long_term_memories(**kwargs):
            archive_uri = kwargs["archive_uri"]
            await session_with_messages._viking_fs.write_file(
                uri=f"{archive_uri}/memory_diff.json",
                content=json.dumps({"archive_uri": archive_uri}),
                ctx=session_with_messages.ctx,
            )
            return []

        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            side_effect=extract_long_term_memories
        )
        if hasattr(session_with_messages._session_compressor, "extract_execution_memories"):
            session_with_messages._session_compressor.extract_execution_memories = AsyncMock(
                return_value={"contexts": [], "session_skills": []}
            )

        result = await session_with_messages.commit_async()
        task_id = result["task_id"]

        # Wait for background memory extraction to complete
        task_result = await _wait_for_task(task_id)
        assert task_result["status"] == "completed"
        assert (
            task_result["result"]["memory_diff_uri"]
            == f"{task_result['result']['archive_uri']}/memory_diff.json"
        )
        memory_diff = json.loads(
            await session_with_messages._viking_fs.read_file(
                task_result["result"]["memory_diff_uri"],
                ctx=session_with_messages.ctx,
            )
        )
        assert memory_diff["archive_uri"] == task_result["result"]["archive_uri"]
        assert "memories_extracted" in task_result["result"]
        memory_counts = task_result["result"]["memories_extracted"]
        assert isinstance(memory_counts, dict)

        # Wait for semantic/embedding queues
        await service.resources.wait_processed(timeout=60.0)

    async def test_commit_task_reports_intentionally_skipped_memory_operations(
        self,
        session_with_messages: Session,
    ):
        async def extract_long_term_memories(**kwargs):
            archive_uri = kwargs["archive_uri"]
            await session_with_messages._viking_fs.write_file(
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
                ctx=session_with_messages.ctx,
            )
            return []

        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            side_effect=extract_long_term_memories
        )

        commit_result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(commit_result["task_id"])

        assert commit_result["status"] == "accepted"
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

    async def test_recovered_commit_task_reads_existing_skipped_memory_operations(
        self,
        session_with_messages: Session,
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
        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock()

        commit_result = await session_with_messages.commit_async()
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
        session_with_messages._session_compressor.extract_long_term_memories.assert_not_awaited()

    async def test_commit_default_disables_agent_memory_but_keeps_archive(
        self, session_with_messages: Session
    ):
        async def account_setting_provider() -> bool:
            return False

        session_with_messages._agent_evolution_enabled_provider = account_setting_provider
        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert result["archived"] is True
        assert task_result["status"] == "completed"
        assert task_result["result"]["agent_evolution_enabled"] is False
        assert "cases" not in task_result["result"]["effective_memory_types"]
        assert "trajectories" not in task_result["result"]["effective_memory_types"]
        assert "experiences" not in task_result["result"]["effective_memory_types"]
        assert task_result["result"]["agent_memory_skip_reason"] == ("agent_evolution_disabled")
        call_kwargs = (
            session_with_messages._session_compressor.extract_long_term_memories.call_args.kwargs
        )
        assert call_kwargs["agent_evolution_enabled"] is False
        assert "cases" not in call_kwargs["allowed_memory_types"]
        assert "trajectories" not in call_kwargs["allowed_memory_types"]
        assert "experiences" not in call_kwargs["allowed_memory_types"]

    async def test_commit_uses_account_setting_and_enables_agent_memory(
        self, session_with_messages: Session
    ):
        session_with_messages._agent_evolution_enabled_provider = lambda: True
        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["agent_evolution_enabled"] is True
        assert "cases" in task_result["result"]["effective_memory_types"]
        assert "trajectories" in task_result["result"]["effective_memory_types"]
        assert "experiences" in task_result["result"]["effective_memory_types"]
        call_kwargs = (
            session_with_messages._session_compressor.extract_long_term_memories.call_args.kwargs
        )
        assert call_kwargs["agent_evolution_enabled"] is True
        assert call_kwargs["allowed_memory_types"] is None

    async def test_commit_reads_latest_user_memory_policy_when_session_has_no_override(
        self, session_with_messages: Session
    ):
        memory_policy_provider = AsyncMock(
            return_value={
                "memory_types": ["profile"],
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
        self, session_with_messages: Session, monkeypatch
    ):
        session_with_messages._agent_evolution_enabled_provider = lambda: False
        summary_called = False

        async def fake_summary(_session, messages, latest_archive_overview=""):
            nonlocal summary_called
            del messages, latest_archive_overview
            summary_called = True
            return "# Working Memory\n\nAgent memory production is disabled."

        monkeypatch.setattr(Session, "_generate_archive_summary_async", fake_summary)
        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await session_with_messages.commit_async(
            memory_policy={
                "memory_types": ["cases", "trajectories", "experiences"],
                "working_memory": {"enabled": True},
            }
        )
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert summary_called is True
        archive_uri = task_result["result"]["archive_uri"]
        assert await _marker_exists(session_with_messages, archive_uri, ".overview.md")
        session_with_messages._session_compressor.extract_long_term_memories.assert_not_awaited()

    async def test_commit_reports_session_skills_separately(
        self, session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = True
        config.vlm = SimpleNamespace(is_available=lambda: False)
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session_with_messages._agent_evolution_enabled_provider = lambda: True
        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value={
                "contexts": [],
                "session_skills": [{"uri": "viking://user/test/skills/code-review"}],
            }
        )

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["memories_extracted"] == {}
        assert task_result["result"]["session_skills_extracted"] == 1
        assert task_result["result"]["session_skill_uris"] == [
            "viking://user/test/skills/code-review"
        ]
        assert "memory_diff_uri" not in task_result["result"]
        session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()
        call_kwargs = (
            session_with_messages._session_compressor.extract_long_term_memories.call_args.kwargs
        )
        assert call_kwargs["allowed_memory_types"] is None

    async def test_commit_skips_session_skills_without_execution_memory_type(
        self, session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = True
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        session_with_messages._meta.memory_policy = {"memory_types": ["profile"]}

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["memories_extracted"] == {}
        assert task_result["result"]["session_skills_extracted"] == 0
        assert "memory_diff_uri" not in task_result["result"]
        session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()

    async def test_commit_skips_session_skill_extraction_when_disabled(
        self, session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = False
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["session_skills_extracted"] == 0
        assert task_result["result"]["session_skill_uris"] == []
        assert "memory_diff_uri" not in task_result["result"]
        session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()

    async def test_commit_can_skip_working_memory_summary(
        self, session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = False
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        summary_called = False

        async def fake_summary(messages, latest_archive_overview=""):
            nonlocal summary_called
            del messages, latest_archive_overview
            summary_called = True
            return "should not be written"

        async def fake_extract(*args, **kwargs):
            del args
            assert kwargs.get("latest_archive_overview", "") == ""
            return []

        session_with_messages._generate_archive_summary_async = fake_summary
        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            side_effect=fake_extract
        )

        result = await session_with_messages.commit_async(
            memory_policy={"working_memory": {"enabled": False}}
        )
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert summary_called is False
        archive_uri = task_result["result"]["archive_uri"]
        assert not await _marker_exists(session_with_messages, archive_uri, ".overview.md")
        assert not await _marker_exists(session_with_messages, archive_uri, ".abstract.md")
        context = await session_with_messages.get_session_context()
        assert context["latest_archive_overview"] == ""
        assert context["messages"] == []
        session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()

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

        async def fake_summary(messages, latest_archive_overview=""):
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

    async def test_commit_archives_messages(self, session_with_messages: Session):
        """Test commit archives messages"""
        initial_message_count = len(session_with_messages.messages)
        assert initial_message_count > 0

        result = await session_with_messages.commit_async()

        assert result.get("archived") is True
        # Current message list should be cleared after commit
        assert len(session_with_messages.messages) == 0

    async def test_commit_empty_session(self, session: Session):
        """Test committing empty session"""
        # Empty session commit should not raise error
        result = await session.commit_async()

        assert isinstance(result, dict)
        assert result.get("archived") is False

    async def test_commit_multiple_times(self, client):
        """Test multiple commits"""
        session = client(session_id="multi_commit_test")
        await session.ensure_exists()

        # First round of conversation
        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = await session.commit_async()
        assert result1.get("status") == "accepted"
        assert result1.get("task_id") is not None

        # Wait for first commit's background task to finish
        await _wait_for_task(result1["task_id"])

        # Second round of conversation
        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = await session.commit_async()
        assert result2.get("status") == "accepted"
        assert result2.get("task_id") is not None

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
        session._session_compressor.extract_long_term_memories = AsyncMock(return_value=[])

        session.add_message("user", [TextPart("Round 1 user")])
        session.add_message("assistant", [TextPart("Round 1 assistant")])
        session.add_message("user", [TextPart("Round 2 user")])
        session.add_message("assistant", [TextPart("Round 2 assistant")])

        result = await session.commit_async(keep_recent_count=2)
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
        }
        session._session_compressor.extract_long_term_memories = AsyncMock(return_value=[])

        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = await session.commit_async()
        await _wait_for_task(result1["task_id"])

        previous_overview = await session._viking_fs.read_file(
            f"{result1['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )
        seen: dict[str, str] = {}

        session_type = type(session)
        original_generate = session_type._generate_archive_summary_async

        async def capture_generate(self, messages, latest_archive_overview=""):
            seen["summary"] = latest_archive_overview
            return await original_generate(
                self,
                messages,
                latest_archive_overview=latest_archive_overview,
            )

        async def capture_extract(*args, **kwargs):
            seen["extract"] = kwargs.get("latest_archive_overview", "")
            return []

        monkeypatch.setattr(session_type, "_generate_archive_summary_async", capture_generate)
        session._session_compressor.extract_long_term_memories = capture_extract

        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = await session.commit_async()
        task_result = await _wait_for_task(result2["task_id"])

        assert task_result["status"] == "completed"
        assert seen["summary"] == previous_overview
        assert seen["extract"] == previous_overview

    async def test_active_count_incremented_after_commit(self, client_with_resource_sync: tuple):
        service, client_ctx, uri = client_with_resource_sync
        vikingdb = service.vikingdb_manager

        # Look up the record by URI
        records_before = await vikingdb.get_context_by_uri(
            uri=uri,
            limit=1,
            ctx=client_ctx,
        )
        assert records_before, f"Resource not found for URI: {uri}"
        count_before = records_before[0].get("active_count") or 0

        # Mark as used and commit
        session = service.sessions.session(
            client_ctx,
            session_id="active_count_regression_test",
        )
        await session.ensure_exists()
        session._session_compressor.extract_long_term_memories = AsyncMock(return_value=[])
        session.add_message("user", [TextPart("Query")])
        session.used(contexts=[uri])
        session.add_message("assistant", [TextPart("Answer")])
        result = await session.commit_async()

        # Wait for background task to complete (active_count is updated there)
        task_result = await _wait_for_task(result["task_id"])
        assert task_result["status"] == "completed"
        assert task_result["result"]["active_count_updated"] == 1

        # Verify the count actually changed in storage
        records_after = await vikingdb.get_context_by_uri(
            uri=uri,
            limit=1,
            ctx=client_ctx,
        )
        assert records_after, f"Record disappeared after commit for URI: {uri}"
        count_after = records_after[0].get("active_count") or 0
        assert count_after == count_before + 1, (
            f"active_count not incremented: before={count_before}, after={count_after}"
        )

    async def test_commit_failed_after_long_term_extraction_failure_does_not_block(self, client):
        """Binary archive outcome: if long-term extraction fails (after retries),
        the whole archive is marked .failed.json and skipped — there is no
        partial state — but a failed archive must not block the next commit.
        """
        session = client(session_id="failed_archive_does_not_block_commit")
        await session.ensure_exists()

        async def failing_extract(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("synthetic extraction failure")

        session._session_compressor.extract_long_term_memories = failing_extract

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

        # A failed archive is a skippable terminal state and must not block the
        # next commit (this previously raised FailedPreconditionError).
        session.add_message("user", [TextPart("Second round message")])
        second = await session.commit_async()
        assert second["status"] == "accepted"
