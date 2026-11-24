# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Recovery retry events reach the exporter with bounded labels."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.metrics.global_api import _build_event_router
from openviking.observability.events import (
    make_payload_subscriber,
    register_event_subscriber,
    unregister_event_subscriber,
)
from openviking.storage.errors import HandoffRecoveryBlocked
from openviking.storage.pathlock_recovery import prepare_handoff_retry


@pytest.mark.asyncio
async def test_blocked_retry_and_exhaustion_export_alarm_counter(
    registry, render_prometheus, monkeypatch
):
    router = _build_event_router(registry)
    register_event_subscriber("test-pathlock", make_payload_subscriber(router.dispatch))
    monkeypatch.setattr("openviking.storage.pathlock_recovery.sleep", AsyncMock())
    try:
        for retry in [0, 8]:
            msg = SimpleNamespace(
                task_id="sensitive-task", lock_handoff_retry=retry, to_dict=lambda: {}
            )
            await prepare_handoff_retry(msg, HandoffRecoveryBlocked("busy"), queue="Reindex")
        router.dispatch("pathlock.recovery_blocked", {"queue": "arbitrary", "outcome": "retry"})
        text = render_prometheus(registry)
        assert (
            'openviking_pathlock_recovery_blocked_total{outcome="retry",queue="Reindex"} 1' in text
        )
        assert (
            'openviking_pathlock_recovery_blocked_total{outcome="exhausted",queue="Reindex"} 1'
            in text
        )
        assert "sensitive-task" not in text and "arbitrary" not in text
    finally:
        unregister_event_subscriber("test-pathlock")
