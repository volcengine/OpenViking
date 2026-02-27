# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Commit tests"""

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.session import Session


class TestCommit:
    """Test commit"""

    async def test_commit_success(self, session_with_messages: Session):
        """Test successful commit"""
        result = session_with_messages.commit()

        assert isinstance(result, dict)
        assert result.get("status") == "committed"
        assert "session_id" in result

    async def test_commit_extracts_memories(
        self, session_with_messages: Session, client: AsyncOpenViking
    ):
        """Test commit extracts memories"""
        result = session_with_messages.commit()

        assert "memories_extracted" in result
        # Wait for memory extraction to complete
        await client.wait_processed(timeout=60.0)

    async def test_commit_archives_messages(self, session_with_messages: Session):
        """Test commit archives messages"""
        initial_message_count = len(session_with_messages.messages)
        assert initial_message_count > 0

        result = session_with_messages.commit()

        assert result.get("archived") is True
        # Current message list should be cleared after commit
        assert len(session_with_messages.messages) == 0

    async def test_commit_empty_session(self, session: Session):
        """Test committing empty session"""
        # Empty session commit should not raise error
        result = session.commit()

        assert isinstance(result, dict)

    async def test_commit_multiple_times(self, client: AsyncOpenViking):
        """Test multiple commits"""
        session = client.session(session_id="multi_commit_test")

        # First round of conversation
        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = session.commit()
        assert result1.get("status") == "committed"

        # Second round of conversation
        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = session.commit()
        assert result2.get("status") == "committed"

    async def test_commit_with_usage_records(self, client: AsyncOpenViking):
        """Test commit with usage records"""
        session = client.session(session_id="usage_commit_test")

        session.add_message("user", [TextPart("Test message")])
        session.used(contexts=["viking://user/test/resources/doc.md"])
        session.add_message("assistant", [TextPart("Response")])

        result = session.commit()

        assert result.get("status") == "committed"
        assert "active_count_updated" in result

    async def test_active_count_incremented_after_commit(self, client_with_resource_sync: tuple):
        """Regression test: active_count must actually increment after commit.

        Previously _update_active_counts() had three bugs:
        1. Called storage.update() with MongoDB-style kwargs (filter=, update=)
           that don't match the actual signature update(collection, id, data),
           causing a silent TypeError on every commit.
        2. Used $inc syntax which storage.update() does not support (merge semantics
           require a plain value, not an increment operator).
        3. Used fetch_by_uri() to locate the record, but that method's path-field
           filter returns the entire subtree (hierarchical match), so any URI that
           has child records triggers a 'Duplicate records found' error and returns
           None — leaving active_count un-updated even after fixes 1 and 2.

        Fix: use storage.filter() to look up the record by URI and read
        its stored id, then call storage.update() with that id.
        """
        client, uri = client_with_resource_sync
        vikingdb = client._client.service.vikingdb_manager

        # Look up the record by URI
        records_before = await vikingdb.get_context_by_uri(
            account_id="default",
            uri=uri,
            limit=1,
        )
        assert records_before, f"Resource not found for URI: {uri}"
        count_before = records_before[0].get("active_count") or 0

        # Mark as used and commit
        session = client.session(session_id="active_count_regression_test")
        session.add_message("user", [TextPart("Query")])
        session.used(contexts=[uri])
        session.add_message("assistant", [TextPart("Answer")])
        result = session.commit()

        assert result.get("active_count_updated") == 1

        # Verify the count actually changed in storage
        records_after = await vikingdb.get_context_by_uri(
            account_id="default",
            uri=uri,
            limit=1,
        )
        assert records_after, f"Record disappeared after commit for URI: {uri}"
        count_after = records_after[0].get("active_count") or 0
        assert count_after == count_before + 1, (
            f"active_count not incremented: before={count_before}, after={count_after}"
        )
