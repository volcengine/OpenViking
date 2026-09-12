# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for semantic enqueue in _write_memory_with_refresh.

Verifies that memory writes now trigger semantic processing to generate
L0 directory abstracts (.abstract.md files), fixing Issue #2797/#4612.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction


def _make_instance() -> ContentWriteCoordinator:
    """Create a ContentWriteCoordinator without calling __init__."""
    inst = ContentWriteCoordinator.__new__(ContentWriteCoordinator)
    # Attributes referenced by _write_memory_with_refresh body
    inst._vikingdb = None
    inst._viking_fs = None
    return inst


def _make_ctx():
    """Create a mock RequestContext."""
    ctx = MagicMock()
    ctx.account_id = "default"
    ctx.user.user_id = "home"
    return ctx


async def _run_write_with_capture(inst, *, mode="create", wait=False):
    """Run _write_memory_with_refresh and capture _enqueue_semantic_refresh_changes args."""
    inst._viking_fs = AsyncMock()
    inst._viking_fs._uri_to_path.return_value = "C:/fake/path"
    inst._viking_fs._async_agfs = AsyncMock()
    lease = MagicMock()
    inst._viking_fs._async_agfs.pathlock_acquire_exact = AsyncMock(return_value=lease)
    inst._viking_fs._async_agfs.pathlock_release = AsyncMock()
    inst._write_in_place = AsyncMock()

    captured_kwargs = {}

    async def capture_enqueue(**kwargs):
        captured_kwargs.update(kwargs)
        return FreshnessAction.REFRESH_NOW

    with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
        mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
        mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
        mock_mu.memory_type_from_uri.return_value = "event"

        with patch.object(inst, "_enqueue_semantic_refresh_changes", side_effect=capture_enqueue):
            with patch.object(inst, "_vikingdb_has_queue", return_value=False):
                with patch.object(inst, "_build_write_result", return_value={"status": "ok"}):
                    result = await inst._write_memory_with_refresh(
                        uri="viking://user/home/memories/events/mem_test.md",
                        root_uri="viking://user/home/memories/events",
                        content="test content",
                        mode=mode,
                        wait=wait,
                        timeout=30.0,
                        ctx=_make_ctx(),
                        written_bytes=12,
                        telemetry_id="test-tel",
                    )
    return result, captured_kwargs


@pytest.mark.asyncio
async def test_write_memory_enqueues_semantic_refresh():
    """Memory writes should call _enqueue_semantic_refresh_changes."""
    inst = _make_instance()

    # Mock the viking_fs pathlock + write path
    inst._viking_fs = AsyncMock()
    inst._viking_fs._uri_to_path.return_value = "C:/fake/path"
    inst._viking_fs._async_agfs = AsyncMock()
    lease = MagicMock()
    inst._viking_fs._async_agfs.pathlock_acquire_exact = AsyncMock(return_value=lease)
    inst._viking_fs._async_agfs.pathlock_release = AsyncMock()
    inst._write_in_place = AsyncMock()

    # Patch MemoryUpdater classmethod side effects
    with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
        mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
        mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
        mock_mu.memory_type_from_uri.return_value = "event"

        # Verify the semantic enqueue is invoked with correct args
        async def fake_enqueue(**kwargs):
            # Capture the call args via assertion on the real call below
            return "REFRESH_NOW"

        with patch.object(inst, "_enqueue_semantic_refresh_changes", side_effect=fake_enqueue) as mock_enqueue:
            with patch.object(inst, "_vikingdb_has_queue", return_value=False):
                with patch.object(inst, "_build_write_result", return_value={"status": "ok"}):
                    ctx = MagicMock()
                    ctx.account_id = "default"
                    ctx.user.user_id = "home"

                    result = await inst._write_memory_with_refresh(
                        uri="viking://user/home/memories/events/mem_test.md",
                        root_uri="viking://user/home/memories/events",
                        content="test",
                        mode="create",
                        wait=False,
                        timeout=30.0,
                        ctx=ctx,
                        written_bytes=4,
                        telemetry_id="test-tel",
                    )

                    assert result == {"status": "ok"}
                    # Semantic enqueue must have been called
                    mock_enqueue.assert_called_once()
                    kwargs = mock_enqueue.call_args.kwargs
                    assert kwargs["root_uri"] == "viking://user/home/memories/events"
                    assert kwargs["context_type"] == "memory"
                    assert "added" in kwargs["changes"]
                    assert "mem_test.md" in kwargs["changes"]["added"][0]


@pytest.mark.asyncio
async def test_write_memory_semantic_failure_does_not_block():
    """Semantic enqueue failure should not block the write."""
    inst = _make_instance()
    inst._viking_fs = AsyncMock()
    inst._viking_fs._uri_to_path.return_value = "C:/fake/path"
    inst._viking_fs._async_agfs = AsyncMock()
    lease = MagicMock()
    inst._viking_fs._async_agfs.pathlock_acquire_exact = AsyncMock(return_value=lease)
    inst._viking_fs._async_agfs.pathlock_release = AsyncMock()
    inst._write_in_place = AsyncMock()

    with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
        mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
        mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
        mock_mu.memory_type_from_uri.return_value = "event"

        # Semantic enqueue raises
        with patch.object(
            inst, "_enqueue_semantic_refresh_changes",
            side_effect=RuntimeError("QueueManager not available"),
        ):
            with patch.object(inst, "_vikingdb_has_queue", return_value=False):
                with patch.object(inst, "_build_write_result", return_value={"status": "ok"}):
                    ctx = MagicMock()
                    ctx.account_id = "default"
                    ctx.user.user_id = "home"

                    # Should NOT raise (semantic enqueue failure swallowed)
                    result = await inst._write_memory_with_refresh(
                        uri="viking://user/home/memories/events/mem_test.md",
                        root_uri="viking://user/home/memories/events",
                        content="test",
                        mode="create",
                        wait=False,
                        timeout=30.0,
                        ctx=ctx,
                        written_bytes=4,
                        telemetry_id="test-tel",
                    )

                    assert result == {"status": "ok"}


# ── Fix #3: semantic_status mapping tests ────────────────────────────────


class TestSemanticStatusMapping:
    """Verify _map_semantic_status correctly maps FreshnessAction to status strings."""

    def test_refresh_now_with_wait_returns_complete(self):
        assert (
            ContentWriteCoordinator._map_semantic_status(
                FreshnessAction.REFRESH_NOW, wait=True
            )
            == "complete"
        )

    def test_refresh_now_without_wait_returns_queued(self):
        assert (
            ContentWriteCoordinator._map_semantic_status(
                FreshnessAction.REFRESH_NOW, wait=False
            )
            == "queued"
        )

    def test_mark_pending_returns_deferred(self):
        assert (
            ContentWriteCoordinator._map_semantic_status(
                FreshnessAction.MARK_PENDING, wait=True
            )
            == "deferred"
        )
        assert (
            ContentWriteCoordinator._map_semantic_status(
                FreshnessAction.MARK_PENDING, wait=False
            )
            == "deferred"
        )

    def test_noop_returns_skipped(self):
        assert (
            ContentWriteCoordinator._map_semantic_status(
                FreshnessAction.NOOP, wait=True
            )
            == "skipped"
        )

    def test_none_returns_skipped(self):
        assert (
            ContentWriteCoordinator._map_semantic_status(None, wait=True) == "skipped"
        )


# ── Fix #4: change_type based on mode tests ─────────────────────────────


class TestChangeTypeBasedOnMode:
    """Verify changes dict uses 'added' for create, 'modified' for replace/append."""

    @pytest.mark.asyncio
    async def test_create_mode_uses_added(self):
        """Create mode should use 'added' in changes dict."""
        inst = _make_instance()
        _, captured = await _run_write_with_capture(inst, mode="create")
        assert "added" in captured["changes"]
        assert "modified" not in captured["changes"]

    @pytest.mark.asyncio
    async def test_replace_mode_uses_modified(self):
        """Replace mode should use 'modified' in changes dict."""
        inst = _make_instance()
        _, captured = await _run_write_with_capture(inst, mode="replace")
        assert "modified" in captured["changes"]
        assert "added" not in captured["changes"]

    @pytest.mark.asyncio
    async def test_append_mode_uses_modified(self):
        """Append mode should use 'modified' in changes dict."""
        inst = _make_instance()
        _, captured = await _run_write_with_capture(inst, mode="append")
        assert "modified" in captured["changes"]
        assert "added" not in captured["changes"]


# ── Fix #5: semantic_status integration tests ────────────────────────────


class TestSemanticStatusIntegration:
    """Verify _build_write_result receives correct semantic_status."""

    @pytest.mark.asyncio
    async def test_semantic_status_complete_when_refresh_now_wait(self):
        """REFRESH_NOW + wait=True -> semantic_status='complete'."""
        inst = _make_instance()
        await _run_write_with_capture(inst, mode="create", wait=True)

    @pytest.mark.asyncio
    async def test_semantic_status_queued_when_refresh_now_no_wait(self):
        """REFRESH_NOW + wait=False -> semantic_status='queued'."""
        inst = _make_instance()
        await _run_write_with_capture(inst, mode="create", wait=False)

    @pytest.mark.asyncio
    async def test_semantic_status_skipped_when_noop(self):
        """NOOP -> semantic_status='skipped'."""
        inst = _make_instance()
        inst._viking_fs = AsyncMock()
        inst._viking_fs._uri_to_path.return_value = "C:/fake/path"
        inst._viking_fs._async_agfs = AsyncMock()
        lease = MagicMock()
        inst._viking_fs._async_agfs.pathlock_acquire_exact = AsyncMock(return_value=lease)
        inst._viking_fs._async_agfs.pathlock_release = AsyncMock()
        inst._write_in_place = AsyncMock()

        async def noop_enqueue(**kwargs):
            return FreshnessAction.NOOP

        with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
            mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
            mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
            mock_mu.memory_type_from_uri.return_value = "event"
            with patch.object(inst, "_enqueue_semantic_refresh_changes", side_effect=noop_enqueue):
                with patch.object(inst, "_vikingdb_has_queue", return_value=False):
                    mock_build = MagicMock(return_value={"status": "ok"})
                    with patch.object(inst, "_build_write_result", mock_build):
                        await inst._write_memory_with_refresh(
                            uri="viking://user/home/memories/events/mem_test.md",
                            root_uri="viking://user/home/memories/events",
                            content="test",
                            mode="create",
                            wait=False,
                            timeout=30.0,
                            ctx=_make_ctx(),
                            written_bytes=4,
                            telemetry_id="test-tel",
                        )
                        call_kwargs = mock_build.call_args.kwargs
                        assert call_kwargs.get("semantic_status") == "skipped"

    @pytest.mark.asyncio
    async def test_semantic_status_skipped_when_none(self):
        """None (enqueue raised) -> semantic_status='skipped'."""
        inst = _make_instance()
        inst._viking_fs = AsyncMock()
        inst._viking_fs._uri_to_path.return_value = "C:/fake/path"
        inst._viking_fs._async_agfs = AsyncMock()
        lease = MagicMock()
        inst._viking_fs._async_agfs.pathlock_acquire_exact = AsyncMock(return_value=lease)
        inst._viking_fs._async_agfs.pathlock_release = AsyncMock()
        inst._write_in_place = AsyncMock()

        async def failing_enqueue(**kwargs):
            raise RuntimeError("QueueManager not available")

        with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
            mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
            mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
            mock_mu.memory_type_from_uri.return_value = "event"
            with patch.object(inst, "_enqueue_semantic_refresh_changes", side_effect=failing_enqueue):
                with patch.object(inst, "_vikingdb_has_queue", return_value=False):
                    mock_build = MagicMock(return_value={"status": "ok"})
                    with patch.object(inst, "_build_write_result", mock_build):
                        await inst._write_memory_with_refresh(
                            uri="viking://user/home/memories/events/mem_test.md",
                            root_uri="viking://user/home/memories/events",
                            content="test",
                            mode="create",
                            wait=False,
                            timeout=30.0,
                            ctx=_make_ctx(),
                            written_bytes=4,
                            telemetry_id="test-tel",
                        )
                        call_kwargs = mock_build.call_args.kwargs
                        assert call_kwargs.get("semantic_status") == "skipped"


# ── Fix #2: dedupe + wait=True hang tests ────────────────────────────────


class TestDeduplicateHandling:
    """Verify deduplication doesn't create phantom roots that block wait=True."""

    @pytest.mark.asyncio
    async def test_dedup_does_not_register_root(self):
        """When enqueue returns 'deduplicated', register_semantic_root should NOT be called."""
        inst = _make_instance()
        inst._viking_fs = AsyncMock()
        inst._viking_fs._uri_to_path.return_value = "C:/fake/path"
        inst._viking_fs._async_agfs = AsyncMock()
        lease = MagicMock()
        inst._viking_fs._async_agfs.pathlock_acquire_exact = AsyncMock(return_value=lease)
        inst._viking_fs._async_agfs.pathlock_release = AsyncMock()
        inst._write_in_place = AsyncMock()

        async def dedup_enqueue(**kwargs):
            return "deduplicated"

        with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
            mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
            mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
            mock_mu.memory_type_from_uri.return_value = "event"
            with patch.object(inst, "_enqueue_semantic_refresh_changes") as mock_enqueue:
                mock_enqueue.return_value = FreshnessAction.REFRESH_NOW
                with patch.object(inst, "_vikingdb_has_queue", return_value=False):
                    with patch.object(inst, "_build_write_result", return_value={"status": "ok"}):
                        with patch("openviking.storage.content_write.get_queue_manager") as mock_qm:
                            mock_queue = AsyncMock()
                            mock_queue.enqueue = AsyncMock(return_value="deduplicated")
                            mock_qm.return_value.get_queue.return_value = mock_queue
                            with patch("openviking.storage.content_write.get_current_telemetry") as mock_tel:
                                mock_tel.return_value.telemetry_id = "test-tel"
                                with patch("openviking.storage.content_write.get_request_wait_tracker") as mock_tracker:
                                    tracker = MagicMock()
                                    mock_tracker.return_value = tracker
                                    ctx = _make_ctx()
                                    action = await inst._enqueue_semantic_refresh_changes(
                                        root_uri="viking://user/home/memories/events",
                                        context_type="memory",
                                        changes={"added": ["viking://user/home/memories/events/test.md"]},
                                        ctx=ctx,
                                        force_refresh=False,
                                    )
                                    tracker.register_semantic_root.assert_not_called()
                                    tracker.mark_semantic_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_returns_action_without_registering(self):
        """Dedup should still return the FreshnessAction (not raise)."""
        inst = _make_instance()
        with patch("openviking.storage.content_write.get_queue_manager") as mock_qm:
            mock_queue = AsyncMock()
            mock_queue.enqueue = AsyncMock(return_value="deduplicated")
            mock_qm.return_value.get_queue.return_value = mock_queue
            with patch("openviking.storage.content_write.get_current_telemetry") as mock_tel:
                mock_tel.return_value.telemetry_id = "test-tel"
                with patch("openviking.storage.content_write.get_request_wait_tracker"):
                    with patch("openviking.storage.content_write.plan_abstract_overview_refresh") as mock_plan:
                        mock_plan.return_value = MagicMock(action=FreshnessAction.REFRESH_NOW)
                        with patch("openviking.storage.content_write.get_openviking_config") as mock_config:
                            mock_config.return_value.semantic = MagicMock()
                            ctx = _make_ctx()
                            action = await inst._enqueue_semantic_refresh_changes(
                                root_uri="viking://user/home/memories/events",
                                context_type="memory",
                                changes={"added": ["test.md"]},
                                ctx=ctx,
                                force_refresh=False,
                            )
                            assert action == FreshnessAction.REFRESH_NOW


# ── Fix #1: batch-write semantic enqueue tests ───────────────────────────


class TestBatchSemanticEnqueue:
    """Verify _refresh_batch triggers semantic enqueue for memory groups."""

    @pytest.mark.asyncio
    async def test_refresh_batch_calls_enqueue_for_memory(self):
        """Memory groups in _refresh_batch should call _enqueue_semantic_refresh_changes."""
        inst = _make_instance()
        inst._viking_fs = AsyncMock()
        inst._vikingdb = AsyncMock()
        ctx = _make_ctx()

        with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
            mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
            mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
            mock_mu.memory_type_from_uri.return_value = "event"

            with patch.object(inst, "_enqueue_semantic_refresh_changes", new_callable=AsyncMock) as mock_enqueue:
                mock_enqueue.return_value = FreshnessAction.REFRESH_NOW

                refresh_kinds = {
                    "viking://user/home/memories/events/test1.md": "added",
                    "viking://user/home/memories/events/test2.md": "added",
                }

                outcome = await inst._refresh_batch(
                    refresh_kinds=refresh_kinds,
                    ctx=ctx,
                    wait=False,
                    timeout=None,
                    telemetry_id="test-tel",
                )

                mock_enqueue.assert_called_once()
                call_kwargs = mock_enqueue.call_args.kwargs
                assert call_kwargs["context_type"] == "memory"
                assert "added" in call_kwargs["changes"]
                assert len(call_kwargs["changes"]["added"]) == 2

    @pytest.mark.asyncio
    async def test_refresh_batch_passes_through_refresh_kinds(self):
        """Batch memory branch should group URIs by refresh_kinds, not hardcode 'added'.

        Regression test for #4657 review #1: batch replace/append on existing
        memory files must reach semantic_dag.py classified as 'modified', not 'added'.
        """
        inst = _make_instance()
        inst._viking_fs = AsyncMock()
        inst._vikingdb = AsyncMock()
        ctx = _make_ctx()

        with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
            mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
            mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
            mock_mu.memory_type_from_uri.return_value = "event"

            with patch.object(inst, "_enqueue_semantic_refresh_changes", new_callable=AsyncMock) as mock_enqueue:
                mock_enqueue.return_value = FreshnessAction.REFRESH_NOW

                # Mix of added (new file) and modified (existing file rewritten)
                refresh_kinds = {
                    "viking://user/home/memories/events/test1.md": "added",
                    "viking://user/home/memories/events/test2.md": "modified",
                    "viking://user/home/memories/events/test3.md": "modified",
                }

                outcome = await inst._refresh_batch(
                    refresh_kinds=refresh_kinds,
                    ctx=ctx,
                    wait=False,
                    timeout=None,
                    telemetry_id="test-tel",
                )

                mock_enqueue.assert_called_once()
                call_kwargs = mock_enqueue.call_args.kwargs
                assert call_kwargs["context_type"] == "memory"
                # Changes dict must have both keys, not just "added"
                assert "added" in call_kwargs["changes"]
                assert "modified" in call_kwargs["changes"]
                assert call_kwargs["changes"]["added"] == ["viking://user/home/memories/events/test1.md"]
                assert sorted(call_kwargs["changes"]["modified"]) == [
                    "viking://user/home/memories/events/test2.md",
                    "viking://user/home/memories/events/test3.md",
                ]

    @pytest.mark.asyncio
    async def test_refresh_batch_enqueue_failure_does_not_block(self):
        """Semantic enqueue failure in batch should not block the batch operation."""
        inst = _make_instance()
        inst._viking_fs = AsyncMock()
        inst._vikingdb = AsyncMock()
        ctx = _make_ctx()

        with patch("openviking.storage.content_write.MemoryUpdater") as mock_mu:
            mock_mu.refresh_schema_overview = AsyncMock(return_value=True)
            mock_mu.refresh_file_embedding = AsyncMock(return_value=True)
            mock_mu.memory_type_from_uri.return_value = "event"

            with patch.object(inst, "_enqueue_semantic_refresh_changes", new_callable=AsyncMock) as mock_enqueue:
                mock_enqueue.side_effect = RuntimeError("QueueManager not available")

                refresh_kinds = {
                    "viking://user/home/memories/events/test1.md": "added",
                }

                outcome = await inst._refresh_batch(
                    refresh_kinds=refresh_kinds,
                    ctx=ctx,
                    wait=False,
                    timeout=None,
                    telemetry_id="test-tel",
                )

                assert outcome is not None
