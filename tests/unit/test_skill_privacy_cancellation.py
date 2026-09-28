# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Skill config writes must settle before their package lock is released."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils.skill_processor import SkillProcessingPreparation, SkillProcessor
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def processing():
    processor = SkillProcessor(vikingdb=None)
    processor.apply_skill_privacy = AsyncMock()
    processor._write_skill_content = AsyncMock()
    processor._write_auxiliary_files = AsyncMock()
    processor._enqueue_skill_package = AsyncMock()
    agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(return_value={"lease_ref": "package"}),
        pathlock_release=AsyncMock(),
    )
    fs = SimpleNamespace(_async_agfs=agfs, _uri_to_path=lambda uri, **kw: uri)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    prepared = SkillProcessingPreparation(
        skill_dict={"name": "config-cancellation", "description": "Example", "content": "body"},
        auxiliary_files=[],
        base_path=None,
        cleanup_path=None,
        privacy_values={"api_key": "test-value"},
    )
    return processor, fs, ctx, prepared


async def test_cancelling_config_write_keeps_package_locked_until_write_exits(processing):
    processor, fs, ctx, prepared = processing
    entered = asyncio.Event()
    finish_write = asyncio.Event()
    write_exited = asyncio.Event()

    async def write_config(*args, **kwargs):
        fs._async_agfs.pathlock_acquire_tree.assert_awaited_once()
        entered.set()
        await finish_write.wait()
        fs._async_agfs.pathlock_release.assert_not_awaited()
        write_exited.set()
        return prepared.skill_dict

    processor.apply_skill_privacy.side_effect = write_config
    task = asyncio.create_task(processor.process_prepared_skill(prepared, fs, ctx))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        # Observe cancellation while the config operation remains in flight.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        fs._async_agfs.pathlock_release.assert_not_awaited()
        finish_write.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert write_exited.is_set()
        fs._async_agfs.pathlock_release.assert_awaited_once_with({"lease_ref": "package"})
        processor._write_skill_content.assert_not_awaited()
        processor._write_auxiliary_files.assert_not_awaited()
        processor._enqueue_skill_package.assert_not_awaited()
    finally:
        finish_write.set()
        await asyncio.gather(task, return_exceptions=True)
