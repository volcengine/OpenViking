# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native TTL initialization preserves the default-off and reader boundaries."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.config import TTLAdapterConfig
from openviking.ttl.wiring import build_ttl_adapter


@pytest.mark.parametrize("config", [None, TTLAdapterConfig(), TTLAdapterConfig(enabled=False)])
def test_disabled_does_not_load_runtime(monkeypatch, config):
    monkeypatch.setitem(sys.modules, "openviking.ttl.runtime", None)
    assert build_ttl_adapter(config) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", [True, False])
async def test_native_lifecycle_is_lazy_and_reader_has_no_worker(writer):
    from openviking.ttl.runtime import DirectoryTTL

    engine = build_ttl_adapter(
        TTLAdapterConfig(enabled=True, config={"resource_id": "native-test", "writer": writer})
    )
    assert isinstance(engine, DirectoryTTL)
    try:
        await engine.start()
        assert engine._task is None  # Work starts only after registration or a due task.
        assert engine.redis is None
        assert not engine._pending and not engine._retry
    finally:
        await engine.aclose()


@pytest.mark.parametrize("writer", [False, True])
def test_ttl_only_publisher_configuration(tmp_path, writer):
    import json

    from openviking_cli.expiry_bootstrap import load_expiry_config

    config = {
        "storage": {"workspace": str(tmp_path)},
        "server": {"ttl_adapter": {"enabled": True, "config": {"writer": writer}}},
        "expiry": {"service_name": "ttl-only", "redis": {"endpoints": ["localhost:6379"]}},
    }
    path = tmp_path / "ov.conf"
    path.write_text(json.dumps(config))
    if writer:
        assert load_expiry_config(path).service_name == "ttl-only"
    else:
        with pytest.raises(ValueError, match="requires"):
            load_expiry_config(path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attempts,attempt,action", [(None, 0, None), (3, 0, "advance_retry"), (3, 2, "ack")]
)
async def test_recurring_retry_retains_claim_and_session_retry_is_unchanged(
    attempts, attempt, action
):
    from openviking.scheduling.expiry.api import HandlerOptions
    from openviking.scheduling.expiry.config import ExpiryConfig
    from openviking.scheduling.expiry.consumer import ExpiryConsumer
    from openviking.scheduling.expiry.metrics import ExpiryMetrics
    from openviking.scheduling.expiry.models import BucketItem, BucketRef, Claim, encode_claim_score
    from openviking.scheduling.expiry.registry import HandlerRegistry

    kind = "ttl" if attempts is None else "session"
    registry = HandlerRegistry()
    registry.register(
        kind, SimpleNamespace(handle=AsyncMock()), HandlerOptions(max_attempts=attempts)
    )
    store = SimpleNamespace(ack=AsyncMock(), advance_retry=AsyncMock())
    consumer = ExpiryConsumer(
        store, Mock(), kind, registry.get(kind), ExpiryConfig(), ExpiryMetrics()
    )
    claim = Claim(BucketRef(kind, 0, 60000), "item", encode_claim_score(attempt, 1000))
    await consumer._finish_failed_attempt(Mock(), claim, "test")
    assert store.ack.await_count == (action == "ack")
    assert store.advance_retry.await_count == (action == "advance_retry")
    if attempts is None:
        assert consumer._ready_at_ms(BucketItem(claim.item_id, claim.claim_score), 0) == 1120000
