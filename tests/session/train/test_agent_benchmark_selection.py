"""Selection, repetition barriers and cache isolation for external benchmarks."""

import httpx
import pytest

from openviking.session.train.batch_runner import (
    BatchTrainEvalConfig,
    _baseline_cache_key,
    _case_loader,
    _effective_eval_index,
    _effective_eval_limit,
    _resolve_benchmark_revision,
    _train_rollout_cache_key_prefix,
)
from openviking.session.train.components.case_loader import ListCaseLoader, TrialCaseLoader
from openviking.session.train.components.dataset_service import case_to_dict
from openviking.session.train.components.remote import RemoteCaseLoader
from openviking.session.train.domain import Case, Rubric


def make_case(i):
    return Case(
        name=str(i),
        task_signature="x",
        input={"i": i},
        rubric=Rubric(name="x", description="x", criteria=[]),
    )


@pytest.mark.asyncio
async def test_seeded_selection_is_stable_across_pages_and_batch_sizes(monkeypatch):
    real = httpx.AsyncClient
    requests = []

    def respond(req):
        import json

        body = json.loads(req.content)
        requests.append(body)
        offset = int(body["cursor"] or 0)
        limit = body["limit"]
        return httpx.Response(
            200,
            json={
                "cases": [
                    case_to_dict(make_case(i)) for i in range(offset, min(offset + limit, 19))
                ],
                "next_cursor": str(offset + limit) if offset + limit < 19 else None,
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(respond))
    )

    async def load(size, seed):
        loader = RemoteCaseLoader(
            service_url="http://local",
            dataset="x",
            domain="all",
            split="train",
            batch_size=size,
            limit=5,
            sample_seed=seed,
        )
        return [c.name async for batch in loader.batches() for c in batch]

    one = await load(2, 42)
    assert one == await load(7, 42)
    assert one != await load(2, 43)
    assert len(one) == len(set(one)) == 5
    assert len(requests) > 3


@pytest.mark.asyncio
async def test_serial_trials_finish_whole_dataset_before_next_repeat():
    loader = TrialCaseLoader(
        ListCaseLoader([make_case(i) for i in range(5)], batch_size=2),
        trial_count=3,
        serial_trials=True,
    )
    seen = []
    async for batch in loader.batches(None):
        assert len({c.input["trial"] for c in batch}) == 1
        seen.extend((c.input["trial"], c.input["i"]) for c in batch)
    assert seen == [(trial, i) for trial in range(3) for i in range(5)]


def test_limits_inherit_only_for_train_split_eval():
    cfg = BatchTrainEvalConfig(
        dataset="skillflow",
        domain="all",
        train_index=[2, 4],
        train_limit=1,
        eval_split="train",
        benchmark_service_url="http://local",
    )
    assert _effective_eval_index(cfg) == [2, 4]
    assert _effective_eval_limit(cfg) == 1
    assert _case_loader(cfg, split="train", sample_index=cfg.train_index, training=True).limit == 1
    cfg.eval_index = [0]
    assert _effective_eval_limit(cfg) is None
    cfg.eval_split = "test"
    assert _effective_eval_index(cfg) == [0]
    cfg.eval_index = None
    assert _effective_eval_index(cfg) is None
    assert _effective_eval_limit(cfg) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"train_limit": 0},
        {"eval_limit": -1},
        {"sample_seed": True},
        {"rollout_options": {"api_key": "secret"}},
    ],
)
def test_reject_invalid_selection_or_secret_options(kwargs):
    with pytest.raises(ValueError):
        BatchTrainEvalConfig(dataset="skillflow", domain="all", **kwargs)


@pytest.mark.parametrize(
    "change",
    [
        {"train_limit": 3, "eval_limit": 3},
        {"sample_seed": 43},
        {"rollout_options": {"model": "other"}},
        {"benchmark_revision": "different"},
        {"benchmark_service_url": "http://different"},
        {"concurrency": 1},
    ],
)
def test_caches_include_runtime_selection_model_and_concurrency(change):
    base = {
        "dataset": "skillflow",
        "domain": "all",
        "train_limit": 2,
        "eval_limit": 2,
        "sample_seed": 42,
        "benchmark_revision": "a",
        "benchmark_service_url": "http://local",
    }
    before = BatchTrainEvalConfig(**base)
    after = BatchTrainEvalConfig(**{**base, **change})
    assert _baseline_cache_key(before) != _baseline_cache_key(after)
    assert _train_rollout_cache_key_prefix(before) != _train_rollout_cache_key_prefix(after)


@pytest.mark.asyncio
async def test_runtime_fingerprint_verified_before_cache_use(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(
            **kw,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"runtime_fingerprint": "ready"})
            ),
        ),
    )
    cfg = BatchTrainEvalConfig(
        dataset="skillflow", domain="all", benchmark_service_url="http://local"
    )
    await _resolve_benchmark_revision(cfg)
    assert cfg.benchmark_revision == "ready"
    cfg.benchmark_revision = "old"
    with pytest.raises(ValueError, match="differs"):
        await _resolve_benchmark_revision(cfg)


@pytest.mark.asyncio
async def test_training_rejects_disabled_learning_before_rollouts():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openviking.session.train.batch_runner import _check_agent_learning_enabled
    from openviking.session.train.components.session_commit import _task_error

    client = SimpleNamespace(
        _request=AsyncMock(
            return_value=httpx.Response(
                200, json={"result": {"enabled": False}}, request=httpx.Request("GET", "http://ov")
            )
        )
    )
    cfg = BatchTrainEvalConfig(dataset="skillflow", domain="all", epochs=1)
    with pytest.raises(ValueError, match="disabled"):
        await _check_agent_learning_enabled(cfg, client)
    assert _task_error({"status": "completed", "result": {"agent_evolution_enabled": False}})
    assert _task_error(
        {
            "status": "completed",
            "result": {
                "agent_evolution_enabled": True,
                "agent_memory_skip_reason": "policy_disabled",
            },
        }
    )
    assert (
        _task_error(
            {
                "status": "completed",
                "result": {"agent_evolution_enabled": True, "memories_extracted": {}},
            }
        )
        is None
    )
    client._request.reset_mock()
    cfg.epochs = 0
    await _check_agent_learning_enabled(cfg, client)
    client._request.assert_not_called()
