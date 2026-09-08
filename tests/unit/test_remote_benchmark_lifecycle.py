# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openviking.session.train import batch_runner
from openviking.session.train.components.remote import RemoteBenchmarkLifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize("epochs", [0, 5])
async def test_lifecycle_sends_training_plan(monkeypatch, epochs):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"task_id": "task-test"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    plan = {"train_epochs": epochs, "train_trials": 2, "eval_trials": 10}
    result = await RemoteBenchmarkLifecycle("http://adapter.test").start(
        run_id="run-test",
        dataset="ark",
        domain="ark",
        concurrency=30,
        training_plan=plan,
    )

    assert result == {"task_id": "task-test"}
    assert bodies == [
        {
            "run_id": "run-test",
            "dataset": "ark",
            "domain": "ark",
            "concurrency": 30,
            "training_plan": plan,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 404, 405])
async def test_lifecycle_training_plan_remains_optional(monkeypatch, status_code):
    def handler(request):
        assert "training_plan" not in json.loads(request.content)
        return httpx.Response(status_code, json={"task_id": "task-test"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await RemoteBenchmarkLifecycle("http://adapter.test").start(
        run_id="run-test", dataset="ark", domain="ark"
    )
    assert result == ({"task_id": "task-test"} if status_code == 200 else None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"train_epochs": -1, "train_trials": 1, "eval_trials": 1},
        {"train_epochs": 0, "train_trials": 0, "eval_trials": 1},
        {"train_epochs": 0, "train_trials": 1, "eval_trials": 0},
        {"train_epochs": True, "train_trials": 1, "eval_trials": 1},
        {"train_epochs": 0.5, "train_trials": 1, "eval_trials": 1},
    ],
)
async def test_lifecycle_rejects_invalid_training_plan_before_http(plan):
    with pytest.raises(ValueError, match="training_plan"):
        await RemoteBenchmarkLifecycle("http://adapter.invalid").start(
            run_id="run-test", dataset="ark", domain="ark", training_plan=plan
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("epochs", [0, 5])
async def test_batch_runner_passes_actual_run_plan(monkeypatch, tmp_path, epochs):
    class StopAfterStart(RuntimeError):
        pass

    client = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
    recorder = SimpleNamespace(record=AsyncMock(), default_fields={})
    start = AsyncMock(side_effect=StopAfterStart)
    monkeypatch.setattr(batch_runner, "_git_notes_reporter", lambda config: None)
    monkeypatch.setattr(batch_runner, "_configure_openviking_config", lambda path: None)
    monkeypatch.setattr(batch_runner, "_clean_result_dir", lambda config: None)
    monkeypatch.setattr(batch_runner, "_build_http_client", lambda config: client)
    monkeypatch.setattr(batch_runner, "_policy_set_metadata", lambda config, client: {})
    monkeypatch.setattr(batch_runner, "_run_output_dir", lambda config: tmp_path)
    monkeypatch.setattr(batch_runner, "_git_metadata", lambda: {})
    monkeypatch.setattr(batch_runner, "_write_run_metadata", lambda *args: None)
    monkeypatch.setattr(batch_runner, "_events_path", lambda config: tmp_path / "events.jsonl")
    monkeypatch.setattr(batch_runner, "JsonlEventRecorder", lambda **kwargs: recorder)
    monkeypatch.setattr(
        batch_runner,
        "SessionCommitPolicyTrainer",
        lambda **kwargs: SimpleNamespace(run_id="run-test"),
    )
    monkeypatch.setattr(batch_runner.RemoteBenchmarkLifecycle, "start", start)
    config = batch_runner.BatchTrainEvalConfig(
        dataset="ark",
        domain="ark",
        epochs=epochs,
        train_trials=2,
        trials=10,
        concurrency=30,
        benchmark_service_url="http://adapter.test",
        skip_baseline_eval=True,
    )

    with pytest.raises(StopAfterStart):
        await batch_runner.run_batch_train_eval(config)

    assert start.await_args.kwargs["concurrency"] == 30
    assert start.await_args.kwargs["training_plan"] == {
        "train_epochs": epochs,
        "train_trials": 2,
        "eval_trials": 10,
    }
    client.close.assert_awaited_once()
