# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Usage record tests"""

import asyncio

import pytest

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.session import Session


class TestUsed:
    """Test usage recording"""

    async def test_used_contexts(self, session: Session):
        """Test recording used contexts"""
        # Add some messages first
        session.add_message("user", [TextPart("Test message")])

        # Record used contexts
        session.used(
            contexts=[
                "viking://user/test/resources/doc1.md",
                "viking://user/test/resources/doc2.md",
            ]
        )

        # Verify usage records
        assert len(session.usage_records) > 0

    async def test_used_skill(self, session: Session):
        """Test recording used skill"""
        session.add_message("user", [TextPart("Test message")])

        session.used(skill={"uri": "viking://user/skills/search", "name": "search_skill"})

        assert len(session.usage_records) > 0

    async def test_used_both(self, session: Session):
        """Test recording both context and skill"""
        session.add_message("user", [TextPart("Test message")])

        session.used(
            contexts=["viking://user/test/resources/doc.md"],
            skill={"uri": "viking://user/skills/analyze", "name": "analyze_skill"},
        )

        assert len(session.usage_records) > 0

    async def test_used_multiple_times(self, session: Session):
        """Test recording usage multiple times"""
        session.add_message("user", [TextPart("Message 1")])
        session.used(contexts=["viking://user/test/resources/doc1.md"])

        session.add_message("user", [TextPart("Message 2")])
        session.used(contexts=["viking://user/test/resources/doc2.md"])

        # Should have multiple usage records
        assert len(session.usage_records) >= 2

    async def test_used_empty(self, session: Session):
        """Test empty usage record"""
        session.add_message("user", [TextPart("Test message")])

        # No parameters passed
        session.used()

        # Should not raise error

    async def test_used_async_survives_reload_with_full_usage_fields(
        self,
        client: AsyncOpenViking,
    ):
        session = client.session(session_id="durable_usage_reload")
        await session.ensure_exists()

        await session.used_async(
            skill={
                "uri": "viking://user/skills/search",
                "input": "query",
                "output": "result",
                "success": False,
                "contribution": 0.75,
                "timestamp": "2026-08-04T01:02:03+00:00",
            }
        )

        reloaded = client.session(session_id=session.session_id)
        await reloaded.load()

        assert len(reloaded.usage_records) == 1
        usage = reloaded.usage_records[0]
        assert usage.uri == "viking://user/skills/search"
        assert usage.type == "skill"
        assert usage.contribution == 0.75
        assert usage.input == "query"
        assert usage.output == "result"
        assert usage.success is False
        assert usage.timestamp == "2026-08-04T01:02:03+00:00"
        assert reloaded.stats.skills_used == 1

    async def test_usage_append_during_phase1_remains_for_next_commit(
        self,
        client: AsyncOpenViking,
        monkeypatch,
    ):
        session = client.session(session_id="durable_usage_commit_race")
        session.add_message("user", [TextPart("first turn")])
        await session.used_async(
            skill={
                "uri": "viking://user/skills/first",
                "contribution": 0.5,
                "input": "first input",
                "output": "first output",
                "success": False,
                "timestamp": "2026-08-04T02:03:04+00:00",
            }
        )

        concurrent_session = client.session(session_id=session.session_id)
        await concurrent_session.load()

        phase1_snapshot_ready = asyncio.Event()
        release_phase1 = asyncio.Event()
        captured_usage_records = []
        original_write_phase1_marker = session._write_phase1_marker

        async def blocking_write_phase1_marker(*args, **kwargs):
            captured_usage_records.extend(kwargs["queue_message"]["usage_records"])
            phase1_snapshot_ready.set()
            await release_phase1.wait()
            return await original_write_phase1_marker(*args, **kwargs)

        monkeypatch.setattr(session, "_write_phase1_marker", blocking_write_phase1_marker)

        commit_task = asyncio.create_task(session.commit_async())
        await phase1_snapshot_ready.wait()
        append_task = asyncio.create_task(
            concurrent_session.used_async(contexts=["viking://resources/second"])
        )
        await asyncio.sleep(0)
        assert not append_task.done()

        release_phase1.set()
        result = await commit_task
        await append_task

        assert captured_usage_records == [
            {
                "uri": "viking://user/skills/first",
                "type": "skill",
                "contribution": 0.5,
                "input": "first input",
                "output": "first output",
                "success": False,
                "timestamp": "2026-08-04T02:03:04+00:00",
            }
        ]

        reloaded = client.session(session_id=session.session_id)
        await reloaded.load()
        assert [usage.uri for usage in reloaded.usage_records] == ["viking://resources/second"]
        assert result["archived"] is True

    async def test_phase1_failure_restores_usage_sidecar(
        self,
        client: AsyncOpenViking,
        monkeypatch,
    ):
        session = client.session(session_id="durable_usage_phase1_failure")
        session.add_message("user", [TextPart("archive candidate")])
        await session.used_async(
            skill={
                "uri": "viking://user/skills/retry",
                "input": "retry input",
                "output": "retry output",
                "success": False,
            }
        )

        class FailingQueueManager:
            async def enqueue(self, _queue_name, _data):
                raise RuntimeError("queue unavailable")

        monkeypatch.setattr(
            "openviking.storage.queuefs.get_queue_manager",
            lambda: FailingQueueManager(),
        )

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await session.commit_async()

        reloaded = client.session(session_id=session.session_id)
        await reloaded.load()
        assert [usage.to_dict() for usage in reloaded.usage_records] == [
            {
                "uri": "viking://user/skills/retry",
                "type": "skill",
                "contribution": 0.0,
                "input": "retry input",
                "output": "retry output",
                "success": False,
                "timestamp": session.usage_records[0].timestamp,
            }
        ]
