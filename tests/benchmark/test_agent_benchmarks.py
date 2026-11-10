"""Contract tests for native benchmark service and OV memory injection."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from benchmark.agent_benchmarks.catalog import Catalog
from benchmark.agent_benchmarks.memory import recall
from benchmark.agent_benchmarks.prepare import skillflow_splits
from benchmark.agent_benchmarks.service import create_app


def task(root, domain, name):
    p = root / domain / name
    for f in ["instruction.md", "task.toml", "environment/Dockerfile", "tests/test.sh"]:
        dest = p / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("task")
    return {"domain": domain, "task_id": name, "path": str(p)}


def manifest(tmp_path, train, test):
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "dataset": "skillflow",
                "runtime": {},
                "splits": {"train": train, "test": test},
            }
        )
    )
    return p


def test_disjoint_splits_and_checker_changes_invalidate_fingerprint(tmp_path):
    a, b = task(tmp_path, "family", "a"), task(tmp_path, "family", "b")
    p = manifest(tmp_path, [a], [b])
    before = Catalog(p).fingerprint
    (Path(a["path"]) / "tests/test.sh").write_text("different check")
    assert Catalog(p).fingerprint != before
    p = manifest(tmp_path, [a], [a])
    with pytest.raises(ValueError, match="overlap"):
        Catalog(p)


def test_skillflow_split_keeps_singletons_eval_only_and_prefers_repaired(tmp_path):
    task(tmp_path / "repaired", "f", "a")
    task(tmp_path / "original", "f", "a")
    task(tmp_path / "original", "f", "b")
    task(tmp_path / "original", "g", "only")
    splits = skillflow_splits([tmp_path / "repaired", tmp_path / "original"], seed=42, fraction=0.5)
    assert len(splits["train"]) == 1 and len(splits["test"]) == 2
    assert not {x["task_id"] for x in splits["train"]} & {x["task_id"] for x in splits["test"]}
    a = next(x for xs in splits.values() for x in xs if x["task_id"] == "a")
    assert "repaired" in a["path"]


@pytest.mark.asyncio
async def test_service_query_filters_health_and_wrong_dataset(tmp_path):
    p = manifest(
        tmp_path, [task(tmp_path, "f", "a"), task(tmp_path, "f", "b")], [task(tmp_path, "g", "c")]
    )
    app = create_app(p, tmp_path / "outputs", concurrency=2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        health = (await client.get("/health")).json()
        assert len(health["runtime_fingerprint"]) == 64
        assert health["max_rollout_concurrency"] == 2
        query = {
            "dataset": "skillflow",
            "domain": "f",
            "split": "train",
            "limit": 10,
            "filters": {"task_indices": [1]},
        }
        response = await client.post("/v1/cases/query", json=query)
        assert response.status_code == 200
        assert response.json()["cases"][0]["input"]["task_id"] == "b"
        query["filters"] = {"task_indices": [10]}
        assert (await client.post("/v1/cases/query", json=query)).status_code == 400
        query["filters"] = {}
        query["domain"] = "misspelled-family"
        assert (await client.post("/v1/cases/query", json=query)).status_code == 400
        query["domain"] = "f"
        query["dataset"] = "evoagentbench"
        assert (await client.post("/v1/cases/query", json=query)).status_code == 400


@pytest.mark.asyncio
async def test_baseline_does_not_contact_ov_and_training_injects_retrieved_experience(
    tmp_path, monkeypatch
):
    prompt, audit = await recall("task", {}, tmp_path)
    assert prompt == "task" and not audit["enabled"]
    root = "viking://user/u/memories/experiences"
    real = httpx.AsyncClient
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "memories": [
                        {"uri": root + "/lesson.md", "content": "Verify the actual column headers."}
                    ]
                },
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(respond))
    )
    prompt, audit = await recall(
        "task",
        {"openviking_url": "http://ov", "root_uri": root, "openviking_api_key": "private"},
        tmp_path,
    )
    assert "Verify the actual column headers." in prompt
    assert audit["enabled"] and len(audit["matches"]) == 1
    assert "private" not in (tmp_path / "ov-recall.json").read_text()
    assert json.loads(requests[0].content)["target_uri"] == root


@pytest.mark.asyncio
async def test_recall_failure_is_not_silently_treated_as_empty_memory(tmp_path, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(
            **kw,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(500, json={"error": "unavailable"})
            ),
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await recall(
            "task",
            {"openviking_url": "http://ov", "root_uri": "viking://user/u/memories/experiences"},
            tmp_path,
        )


def test_evo_manifest_verifies_official_boundaries_and_checksums(tmp_path):
    repo = tmp_path / "evo"
    split_dir = repo / "benchmark/data/splits"
    split_dir.mkdir(parents=True)
    split = split_dir / "code_implementation.json"
    split.write_text(json.dumps({"train": ["a"], "test": ["b"]}))
    (split_dir / "manifest.json").write_text(
        json.dumps(
            {
                "splits": {
                    "code": {
                        "file": split.name,
                        "sha256": hashlib.sha256(split.read_bytes()).hexdigest(),
                        "train": 1,
                        "test": 1,
                    }
                }
            }
        )
    )
    data = {
        "version": 1,
        "dataset": "evoagentbench",
        "runtime": {"repo": str(repo)},
        "splits": {
            "train": [{"domain": "code_implementation", "task_id": "a"}],
            "test": [{"domain": "code_implementation", "task_id": "b"}],
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    catalog = Catalog(path)
    assert len(catalog.splits["train"]) == 1
    data["splits"]["train"][0]["task_id"] = "b"
    data["splits"]["test"] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="outside official train"):
        Catalog(path)
    split.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        Catalog(path)


@pytest.mark.asyncio
async def test_subprocess_bridge_preserves_epoch_and_trial_identity_without_writing_credentials(
    tmp_path, monkeypatch
):
    import asyncio
    import sys

    from benchmark.agent_benchmarks.service import AdapterExecutor
    from openviking.session.train.context import ExecutionContext
    from openviking.session.train.domain import ExperienceSet

    a = task(tmp_path, "f", "a")
    b = task(tmp_path, "f", "b")
    path = manifest(tmp_path, [a], [b])
    data = json.loads(path.read_text())
    data["runtime"] = {
        "python": sys.executable,
        "timeout_seconds": 10,
    }
    path.write_text(json.dumps(data))
    catalog = Catalog(path)
    case = catalog.case(a, "train")
    real = asyncio.create_subprocess_exec
    script = """import sys,json,pathlib
p=json.loads(sys.stdin.read());out=pathlib.Path(p['output'])
r={'case':p['case'],'messages':[],'policy_snapshot_id':p['snapshot_id'],'evaluation':{'passed':True,'score':1.,'criterion_results':[],'feedback':[],'metadata':{}},'metadata':{'memory_enabled':bool(p['memory'])}}
(out/'rollout.json').write_text(json.dumps(r))
"""

    async def launch(*args, **kw):
        return await real(sys.executable, "-c", script, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    executor = AdapterExecutor(catalog, tmp_path / "out", {"model": "m", "max_iterations": 5})
    executor.output.mkdir()
    policies = ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[],
        metadata={"openviking_api_key": "private"},
    )
    first = (
        await executor.execute(
            [case],
            policies,
            ExecutionContext(
                policy_snapshot_id="s",
                metadata={"epoch": 0, "training": True, "stage": "train_rollout"},
            ),
        )
    )[0]
    second = (
        await executor.execute(
            [case],
            policies,
            ExecutionContext(
                policy_snapshot_id="s",
                metadata={"epoch": 1, "training": True, "stage": "train_rollout"},
            ),
        )
    )[0]
    from openviking.session.train.components.session_commit import _session_id_for_rollout

    assert _session_id_for_rollout(first, run_id="r") != _session_id_for_rollout(second, run_id="r")
    assert _session_id_for_rollout(first, run_id="r").startswith("skillflow_train_")
    assert first.case is case and first.metadata["execution_metadata"]["epoch"] == 0
    assert second.metadata["execution_metadata"]["epoch"] == 1
    for f in executor.output.rglob("*"):
        if f.is_file():
            assert "private" not in f.read_text()
