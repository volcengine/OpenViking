# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for commit Phase 2 extraction scheduling (per-space, per-lane serialization)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.session import session as session_module


def _fake_config(per_space: int = 1, global_cap: int = 0):
    return SimpleNamespace(
        memory=SimpleNamespace(
            phase2_per_space_max_concurrent=per_space,
            phase2_max_concurrent=global_cap,
        )
    )


@pytest.fixture(autouse=True)
def _reset_phase2_state():
    """Isolate module-level semaphore state between tests."""

    def _reset():
        session_module._phase2_space_semaphores = {}
        session_module._phase2_space_limit = None
        session_module._phase2_global_semaphore = None
        session_module._phase2_global_limit = None

    _reset()
    yield
    _reset()


async def _second_starts_while_first_running(key_a: str, key_b: str) -> bool:
    """Run two guarded coroutines; report whether B starts while A is still inside."""
    a_entered = asyncio.Event()
    a_release = asyncio.Event()
    b_started = asyncio.Event()

    async def coro_a():
        a_entered.set()
        await a_release.wait()

    async def coro_b():
        b_started.set()

    gathered = asyncio.gather(
        session_module._run_memory_extraction_with_limit(key_a, coro_a()),
        session_module._run_memory_extraction_with_limit(key_b, coro_b()),
    )
    await a_entered.wait()
    # Give B ample opportunity to start if scheduling allows it.
    for _ in range(20):
        await asyncio.sleep(0)
    overlapped = b_started.is_set()
    a_release.set()
    await gathered
    return overlapped


class TestPhase2ExtractionScheduling:
    @pytest.mark.asyncio
    async def test_same_space_same_lane_serializes(self):
        with patch(
            "openviking_cli.utils.config.get_openviking_config",
            return_value=_fake_config(),
        ):
            overlapped = await _second_starts_while_first_running(
                "acct:user:long_term", "acct:user:long_term"
            )
        assert overlapped is False, "same space + same lane must run one at a time"

    @pytest.mark.asyncio
    async def test_same_space_cross_lane_runs_parallel(self):
        with patch(
            "openviking_cli.utils.config.get_openviking_config",
            return_value=_fake_config(),
        ):
            overlapped = await _second_starts_while_first_running(
                "acct:user:long_term", "acct:user:execution"
            )
        assert overlapped is True, "long_term and execution lanes write disjoint types"

    @pytest.mark.asyncio
    async def test_cross_space_runs_parallel(self):
        with patch(
            "openviking_cli.utils.config.get_openviking_config",
            return_value=_fake_config(),
        ):
            overlapped = await _second_starts_while_first_running(
                "acct:alice:long_term", "acct:bob:long_term"
            )
        assert overlapped is True, "different user spaces must not serialize each other"

    @pytest.mark.asyncio
    async def test_global_cap_serializes_across_spaces(self):
        with patch(
            "openviking_cli.utils.config.get_openviking_config",
            return_value=_fake_config(global_cap=1),
        ):
            overlapped = await _second_starts_while_first_running(
                "acct:alice:long_term", "acct:bob:long_term"
            )
        assert overlapped is False, "phase2_max_concurrent=1 must gate cross-space runs"

    @pytest.mark.asyncio
    async def test_global_cap_disabled_by_default(self):
        with patch(
            "openviking_cli.utils.config.get_openviking_config",
            return_value=_fake_config(),
        ):
            assert session_module._get_phase2_global_semaphore() is None
