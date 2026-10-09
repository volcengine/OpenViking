import asyncio
import copy
import json

import httpx
import pytest
from adapter_settings import AdapterSettings, Viking
from run_store import RunStore
from viking_client import VikingClient
from viking_service import create_app
from viking_task import launch_request
from viking_trace import normalize_messages, trace_prefixes


def canonical(text="answer", output="result"):
    return {
        "node_list": [
            {
                "trace_info": {
                    "session_trace": {
                        "messages": [
                            {
                                "info": {"id": "m1", "sessionID": "s", "role": "user"},
                                "parts": [{"type": "text", "text": "question"}],
                            },
                            {
                                "info": {"id": "m2", "sessionID": "s", "role": "assistant"},
                                "parts": [
                                    {"type": "text", "text": text},
                                    {
                                        "type": "tool",
                                        "tool": "memsearch",
                                        "callID": "c1",
                                        "state": {
                                            "input": {"query": "q"},
                                            "output": output,
                                            "status": "completed",
                                        },
                                    },
                                ],
                            },
                        ]
                    }
                }
            }
        ]
    }


def draft():
    return {
        "warnings": [],
        "launch_request": {
            "name": "OLD",
            "target_row_ids": [999],
            "watcher_emails": ["old@example.com"],
            "sandbox_config": {"extra_payload": {"memory": {"user_id": "old-lease"}}},
            "execution_type": "operator_group",
            "operator_steps": [{"operator_id": 76}],
            "config": {"concurrency": 2},
            "execution_plan": {
                "groups": [
                    {"key": "g1", "step_range": [0, 1], "execution_overrides": {"concurrency": 1}},
                    {"key": "g2", "step_range": [1, 2], "execution_overrides": {"concurrency": 1}},
                ]
            },
        },
    }


def settings():
    return AdapterSettings(
        viking=Viking(
            api_token="test",
            template_task_id=3327,
            train={"experiment_set_id": 300, "version": "V11"},
            eval={"experiment_set_id": 300, "version": "V11"},
            poll_interval_seconds=0.001,
            sandbox_config={
                "extra_headers": {"x-tt-backend": "evolving", "x-vaka-request-source": "ark-lx"}
            },
        )
    )


class FakeClient:
    def __init__(self, *, ambiguous=False, failed=False):
        self.creates, self.tasks = [], {}
        self.ambiguous, self.failed = ambiguous, failed
        self.list_visible = True

    async def close(self):
        pass

    async def request(self, method, path, **kwargs):
        if path.endswith("clone-draft"):
            source_id = int(path.split("/")[1])
            if source_id in self.tasks:
                return {"warnings": [], "launch_request": copy.deepcopy(self.tasks[source_id])}
            return copy.deepcopy(draft())
        if path == "task-executions" and method == "POST":
            body = kwargs["json"]
            self.creates.append(body)
            task = {
                **body,
                "id": 4000 + len(self.creates),
                "status": "partially_failed" if self.failed else "success",
            }
            self.tasks[task["id"]] = task
            if self.ambiguous:
                self.ambiguous = False
                raise httpx.ReadTimeout("reply lost")
            return task
        if path.startswith("task-executions/"):
            return self.tasks[int(path.split("/")[1])]
        raise AssertionError((method, path, kwargs))

    async def pages(self, path, **params):
        if path.startswith("experiment-set/"):
            return [{"id": i, "data": {"case_id": i + 900, "query": "question"}} for i in (10, 11)]
        if path == "task-executions":
            return list(self.tasks.values()) if self.list_visible else []
        if path.endswith("/logs"):
            return []
        if path.endswith("/results"):
            task_id = int(path.split("/")[1])
            return [
                {
                    "id": i + 50,
                    "case_id": i,
                    "status": "failed" if self.failed else "success",
                    "error_message": "recall_routes failed" if self.failed else None,
                    "output": {
                        "answer_score": 0.8,
                        "answer_evidence": "Judge explanation",
                        "trace_tos_path": f"tos://bucket/vaka_operator/{task_id}/abc/traces",
                    },
                }
                for i in self.tasks[task_id]["target_row_ids"]
            ]
        raise AssertionError(path)

    async def tos_files(self, prefix):
        return [{"name": "async.json", "key": prefix + "/async.json", "kind": "file"}]

    async def tos_json(self, path):
        return canonical()


async def start(app):
    adapter = app.state.adapter
    await adapter.start(
        {
            "run_id": "r1",
            "dataset": "ark4-0",
            "domain": "ark",
            "concurrency": 36,
            "training_plan": {"train_epochs": 0, "eval_trials": 2, "train_trials": 1},
        }
    )
    cases = adapter.query({"split": "test", "filters": {"_openviking_benchmark_run_id": "r1"}})[
        "cases"
    ]
    return adapter, cases


def request(case, trial=0):
    case = copy.deepcopy(case)
    case["input"]["eval_trial"] = trial
    return {
        "case": case,
        "policy_set": {"root_uri": "viking://agent/memories/experience", "policies": []},
        "execution_context": {
            "policy_snapshot_id": "s1",
            "metadata": {"training": False, "epoch": 0},
        },
        "options": {},
    }


async def drain(adapter):
    while adapter.workers:
        await asyncio.gather(*list(adapter.workers.values()))


def test_launch_no_inherited_identity_and_all_groups_concurrent():
    source = draft()
    body = launch_request(
        source,
        selection={"experiment_set_id": 300, "version": "V11"},
        row_ids=[10],
        name="new",
        concurrency=36,
        settings=settings().viking,
    )
    assert body["target_row_ids"] == [10]
    assert body["run_times"] == 1
    assert "watcher_emails" not in body
    assert "old-lease" not in json.dumps(body)
    assert body["sandbox_config"]["extra_headers"]["x-tt-backend"] == "evolving"
    assert all(
        g["execution_overrides"]["concurrency"] == 36 for g in body["execution_plan"]["groups"]
    )
    assert source["launch_request"]["config"]["concurrency"] == 2


def test_launch_group_timeout_override_preserves_other_settings():
    source = draft()
    for group in source["launch_request"]["execution_plan"]["groups"]:
        group["execution_overrides"].update(timeout_seconds=3200, max_retries=3)
    original = copy.deepcopy(source)
    configured = Viking(api_token="test", template_task_id=1, group_timeout_seconds={"g1": 1800})
    body = launch_request(
        source,
        selection={"experiment_set_id": 421, "version": "V2"},
        row_ids=[10],
        name="new",
        concurrency=36,
        settings=configured,
    )
    overrides = [g["execution_overrides"] for g in body["execution_plan"]["groups"]]
    assert [g["timeout_seconds"] for g in overrides] == [1800, 3200]
    assert all(g["max_retries"] == 3 and g["concurrency"] == 36 for g in overrides)
    assert source == original


@pytest.mark.parametrize("value", [0, 604801, True, "1800"])
def test_group_timeout_requires_bounded_integer(value):
    with pytest.raises(ValueError):
        Viking(api_token="test", template_task_id=1, group_timeout_seconds={"g1": value})


def test_unknown_group_timeout_rejected():
    configured = Viking(api_token="test", template_task_id=1, group_timeout_seconds={"typo": 1800})
    with pytest.raises(ValueError, match="Unknown group_timeout_seconds"):
        launch_request(
            draft(),
            selection={"experiment_set_id": 421, "version": "V2"},
            row_ids=[10],
            name="new",
            concurrency=36,
            settings=configured,
        )


def test_launch_group_concurrency_overrides():
    source = draft()
    source["launch_request"]["execution_plan"]["groups"].extend(
        {"key": key, "execution_overrides": {"concurrency": 1, "timeout_seconds": 1800}}
        for key in ("g3", "g4")
    )
    configured = Viking(
        **{**settings().viking.model_dump(), "group_concurrency": {"g2": 20, "g3": 20, "g4": 20}}
    )
    body = launch_request(
        source,
        selection={"experiment_set_id": 300, "version": "V11"},
        row_ids=[10],
        name="new",
        concurrency=36,
        settings=configured,
    )
    overrides = [g["execution_overrides"] for g in body["execution_plan"]["groups"]]
    assert [g["concurrency"] for g in overrides] == [36, 20, 20, 20]
    assert [g["ramp_up_concurrency_per_minute"] for g in overrides] == [36, 20, 20, 20]
    assert overrides[-1]["timeout_seconds"] == 1800
    assert (
        source["launch_request"]["execution_plan"]["groups"][1]["execution_overrides"][
            "concurrency"
        ]
        == 1
    )


@pytest.mark.parametrize("value", [0, 501, True, "20"])
def test_group_concurrency_requires_bounded_integer(value):
    with pytest.raises(ValueError):
        Viking(api_token="test", template_task_id=1, group_concurrency={"g2": value})


def test_unknown_group_concurrency_rejected():
    configured = Viking(api_token="test", template_task_id=1, group_concurrency={"typo": 20})
    with pytest.raises(ValueError, match="Unknown group_concurrency"):
        launch_request(
            draft(),
            selection={"experiment_set_id": 300, "version": "V11"},
            row_ids=[10],
            name="new",
            concurrency=36,
            settings=configured,
        )


def test_trace_only_canonical_and_latest_tool_snapshot():
    first, last = canonical(output="partial"), canonical(output="complete")
    first["internal"] = {"messages": [{"role": "system", "content": "FULL SKILL NOISE"}]}
    messages = normalize_messages([first, last])
    assert len(messages) == 2
    assert messages[1]["parts"][1]["tool_output"] == "complete"
    assert "FULL SKILL" not in json.dumps(messages)


def test_multiturn_prefixes_include_all_turns():
    p = "tos://bucket/vaka_operator/3332/"
    assert trace_prefixes(
        {"output": {"trace_tos_path": p + "c/traces"}},
        [{"message": p + "c/traces"}, {"message": p + "b/traces"}, {"message": p + "a/traces"}],
    ) == [p + c + "/traces" for c in "abc"]


@pytest.mark.asyncio
async def test_batch_one_create_per_trial_and_no_fake_zero(tmp_path):
    client = FakeClient(failed=True)
    app = create_app(client, settings(), state_dir=tmp_path)
    adapter, cases = await start(app)
    executions = [adapter.submit(request(case)) for case in cases]
    await drain(adapter)
    assert len(client.creates) == 1
    assert client.creates[0]["target_row_ids"] == [10, 11]
    for execution in executions:
        result = adapter.store.get("executions", execution["execution_id"])
        assert result["rollout"]["evaluation"] is None
        assert len(result["rollout"]["messages"]) == 2  # evaluator failed, Agent trace retained
        assert result["rollout"]["metadata"]["commit_eligible"] is False
    adapter.submit(request(cases[0], trial=1))
    await drain(adapter)
    assert len(client.creates) == 2
    await app.router.shutdown()


@pytest.mark.asyncio
async def test_restart_reconciles_lost_create_response_without_duplicate(tmp_path):
    client = FakeClient(ambiguous=True)
    app = create_app(client, settings(), state_dir=tmp_path)
    adapter, cases = await start(app)
    execution = adapter.submit(request(cases[0]))
    await drain(adapter)
    assert adapter.store.get("executions", execution["execution_id"])["status"] == "running"
    await app.router.shutdown()
    app = create_app(client, settings(), state_dir=tmp_path)
    adapter = app.state.adapter
    resumed = adapter.submit(request(cases[0]))
    assert resumed["execution_id"] == execution["execution_id"]
    await drain(adapter)
    result = adapter.store.get("executions", execution["execution_id"])
    assert result["status"] == "completed"
    assert result["rollout"]["evaluation"]["score"] == 0.8
    assert len(client.creates) == 1
    await app.router.shutdown()


@pytest.mark.asyncio
async def test_invisible_ambiguous_creation_does_not_repost(tmp_path):
    client = FakeClient(ambiguous=True)
    client.list_visible = False
    app = create_app(client, settings(), state_dir=tmp_path)
    adapter, cases = await start(app)
    execution = adapter.submit(request(cases[0]))
    await drain(adapter)
    adapter.submit(request(cases[0]))
    await drain(adapter)
    assert len(client.creates) == 1
    assert adapter.store.get("executions", execution["execution_id"])["status"] == "running"
    await app.router.shutdown()


@pytest.mark.asyncio
async def test_api_auth_envelope_and_paging():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test"
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json={"code": 0, "data": {"list": [{"id": page}], "total": 2}})

    client = VikingClient(
        "https://viking-exp.byted.org", "test", transport=httpx.MockTransport(handler)
    )
    assert await client.pages("task-executions") == [{"id": 1}, {"id": 2}]
    assert calls[0].url.path == "/api/v1/task-executions"
    await client.close()


def test_state_process_lock_and_strict_settings(tmp_path):
    store = RunStore(tmp_path)
    with pytest.raises(RuntimeError):
        RunStore(tmp_path)
    store.close()
    with pytest.raises(ValueError):
        AdapterSettings.model_validate(
            {"platform": {}, "viking": {"api_token": "x", "template_task_id": 1}}
        )


@pytest.mark.asyncio
async def test_unknown_score_skips_commit_without_negative_feedback():
    from openviking.session.train.components.dataset_service import rollout_from_dict
    from openviking.session.train.components.session_commit import SessionCommitPolicyTrainer
    from openviking.session.train.pipeline import _analyses_from_rollout_evaluations

    rollout = rollout_from_dict(
        {
            "case": {
                "name": "x",
                "task_signature": "x",
                "input": {},
                "rubric": {"name": "viking", "criteria": []},
            },
            "policy_snapshot_id": "s",
            "messages": [],
            "evaluation": None,
            "metadata": {"evaluation_valid": False, "commit_eligible": False},
        }
    )
    trainer = SessionCommitPolicyTrainer(client=None)
    result = await trainer._commit_one(rollout, 0)
    assert result["score"] is None
    assert result["skipped_reason"] == "invalid_evaluation_or_incomplete_trace"
    assert _analyses_from_rollout_evaluations([rollout]) == []


@pytest.mark.asyncio
async def test_trials_cannot_multiply_remote_concurrency(tmp_path):
    class RunningClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.created = asyncio.Event()
            self.release = asyncio.Event()

        async def request(self, method, path, **kwargs):
            result = await super().request(method, path, **kwargs)
            if path == "task-executions" and method == "POST":
                result["status"] = "running"
                self.created.set()
            elif path.startswith("task-executions/") and not path.endswith("clone-draft"):
                result["status"] = "success" if self.release.is_set() else "running"
            return result

    client = RunningClient()
    app = create_app(client, settings(), state_dir=tmp_path)
    adapter, cases = await start(app)

    # Directly exercise launch gating without per-row workers finishing immediately.
    def execution(trial):
        return {"run_id": "r1", "batch_id": f"batch-{trial}", "request": request(cases[0], trial)}

    first = await adapter.ensure_batch(execution(0))
    assert first["task_id"]
    second = asyncio.create_task(adapter.ensure_batch(execution(1)))
    for _ in range(5):
        await asyncio.sleep(0.001)
    assert len(client.creates) == 1
    client.release.set()
    await asyncio.wait_for(second, timeout=1)
    assert len(client.creates) == 2
    await app.router.shutdown()


@pytest.mark.asyncio
async def test_two_active_tasks_share_slots_across_runs(tmp_path):
    class RunningClient(FakeClient):
        async def request(self, method, path, **kwargs):
            result = await super().request(method, path, **kwargs)
            if path == "task-executions" and method == "POST":
                result["status"] = "running"
            return result

    config = settings()
    config.service.max_active_tasks = 2
    client = RunningClient()
    app = create_app(client, config, state_dir=tmp_path)
    adapter, cases = await start(app)
    original = adapter.run("r1")
    second_run = copy.deepcopy(original)
    second_run["run_id"] = "r2"
    adapter.store.put("runs", "r2", second_run)

    def execution(trial, run="r1"):
        return {"run_id": run, "batch_id": f"batch-{trial}", "request": request(cases[0], trial)}

    first, second = await asyncio.gather(
        adapter.ensure_batch(execution(0)), adapter.ensure_batch(execution(1, "r2"))
    )
    assert len(client.creates) == 2
    third = asyncio.create_task(adapter.ensure_batch(execution(2)))
    await asyncio.sleep(0.02)
    assert len(client.creates) == 2
    # Either task finishing frees one slot; it need not be the oldest task.
    client.tasks[second["task_id"]]["status"] = "success"
    await asyncio.wait_for(third, timeout=1)
    assert len(client.creates) == 3
    assert client.tasks[first["task_id"]]["status"] == "running"
    assert sum(t["status"] == "running" for t in client.tasks.values()) == 2
    await app.router.shutdown()


@pytest.mark.parametrize("value", [0, 3, True, "2"])
def test_active_task_limit_validation(value):
    from adapter_settings import Service

    with pytest.raises(ValueError):
        Service(max_active_tasks=value)


@pytest.mark.asyncio
async def test_http_protocol_retains_terminal_case_and_run_ids(tmp_path):
    app = create_app(FakeClient(), settings(), state_dir=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter"
    ) as client:
        response = await client.post(
            "/v1/runs/start", json={"run_id": "r1", "dataset": "ark4-0", "concurrency": 36}
        )
        assert response.status_code == 200
        rows = await client.post(
            "/v1/cases/query",
            json={"split": "test", "filters": {"_openviking_benchmark_run_id": "r1"}, "limit": 1},
        )
        case = rows.json()["cases"][0]
        execution = (await client.post("/v1/rollouts/execute", json=request(case))).json()
        await drain(app.state.adapter)
        result = (await client.get("/v1/rollouts/executions/" + execution["execution_id"])).json()
        assert result["status"] == "completed"
        assert result["rollout"]["metadata"]["agent_main_request_ids"] == ["abc"]
        from openviking.session.train.components.dataset_service import rollout_from_dict

        assert rollout_from_dict(result["rollout"]).evaluation.score == 0.8
        assert rollout_from_dict(result["rollout"]).evaluation.criterion_results[0].feedback == [
            "Judge explanation"
        ]
        completion = (await client.post("/v1/runs/r1/complete")).json()
        assert completion["task_ids"] == [4001]
    await app.router.shutdown()


@pytest.mark.asyncio
async def test_truncated_trace_is_not_used_as_complete_json():
    def handler(request):
        data = {"truncated": True, "text": '{"partial":', "encoding": "utf-8"}
        if request.url.path.endswith("presign"):
            data = {"url": "https://untrusted.example/trace.json"}
        return httpx.Response(200, json={"code": 0, "data": data})

    client = VikingClient(
        "https://viking-exp.byted.org", "secret", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ValueError, match="Untrusted TOS"):
        await client.tos_json("tos://bucket/trace.json")
    await client.close()


@pytest.mark.asyncio
async def test_http_200_business_error_is_not_success():
    client = VikingClient(
        "https://viking-exp.byted.org",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 500, "msg": "operator missing"})
        ),
    )
    with pytest.raises(ValueError, match="operator missing"):
        await client.request("POST", "task-executions", json={})
    await client.close()


def test_all_invalid_evaluations_report_null_not_zero():
    from openviking.session.train.components.report_builder import PipelineReportBuilder
    from openviking.session.train.domain import PipelineEvaluationResult

    result = PipelineEvaluationResult(
        epoch=0,
        analyses=[],
        policy_snapshot_ids=[],
        metadata={
            "invalid_evaluation_count": 2,
            "invalid_evaluations": [{"trial": 0}, {"trial": 1}],
        },
    )
    report = PipelineReportBuilder().trial_evaluation_report(result, trial_count=2)
    assert report["average_reward"] is None
    assert report["accuracy"] is None
    assert report["requested_case_count"] == report["total_rollout_count"] == 2
    assert [t["invalid_evaluation_count"] for t in report["trials"]] == [1, 1]


@pytest.mark.asyncio
async def test_cancel_calls_viking_and_prevents_new_trials(tmp_path):
    class Cancellable(FakeClient):
        def __init__(self):
            super().__init__()
            self.cancelled = []

        async def request(self, method, path, **kwargs):
            if path.endswith("/cancel"):
                task_id = int(path.split("/")[1])
                self.cancelled.append(task_id)
                self.tasks[task_id]["status"] = "cancelled"
                return {}
            result = await super().request(method, path, **kwargs)
            if path == "task-executions" and method == "POST":
                result["status"] = "running"
            return result

    client = Cancellable()
    app = create_app(client, settings(), state_dir=tmp_path)
    adapter, cases = await start(app)
    await adapter.ensure_batch({"run_id": "r1", "batch_id": "first", "request": request(cases[0])})
    assert (await adapter.finish("r1"))["status"] == "finalizing"
    assert (await adapter.finish("r1", cancel=True))["status"] == "cancelled"
    assert client.cancelled == [4001]
    with pytest.raises(ValueError, match="not active"):
        adapter.submit(request(cases[0], trial=1))
    await app.router.shutdown()
