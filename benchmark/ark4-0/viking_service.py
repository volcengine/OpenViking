"""Durable local runner protocol backed exclusively by Viking experiments."""

from __future__ import annotations

import asyncio
import json
import math
import time

from adapter_settings import Selection, Viking
from fastapi import FastAPI, HTTPException
from memory_proxy import install_memory_proxy
from run_store import RunStore, digest
from viking_task import launch_request, preflight
from viking_trace import collect_trace

TERMINAL = {"success", "failed", "partially_failed", "cancelled"}
ROW_TERMINAL = {"success", "failed", "cancelled"}


class UnresolvedCreation(RuntimeError):
    pass


class VikingAdapter:
    def __init__(self, client, settings, store):
        self.client, self.settings, self.store = client, settings, store
        self.run_locks, self.batch_locks, self.workers = {}, {}, {}
        self.last_polls = {}
        self.task_admission_lock = asyncio.Lock()

    def run(self, run_id):
        run = self.store.get("runs", run_id)
        if not run:
            raise HTTPException(404, "Unknown run_id; call /v1/runs/start first")
        return run

    async def start(self, request):
        run_id = request["run_id"]
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if request.get("casehub"):
            raise ValueError("CaseHub is retired for this adapter; use viking selection")
        if request.get("dataset") != self.settings.service.dataset:
            raise ValueError("dataset does not match this adapter")
        concurrency = request.get("concurrency", 36)
        if type(concurrency) is not int or not 1 <= concurrency <= 500:
            raise ValueError("concurrency must be an integer in [1, 500]")
        async with self.run_locks.setdefault(run_id, asyncio.Lock()):
            existing = self.store.get("runs", run_id)
            if existing:
                if existing["request"] != request:
                    raise ValueError("run_id already exists with different parameters")
                return self.summary(existing)
            plan = request.get("training_plan") or {}
            overrides = request.get("viking") or {}
            selections = {}
            for split in ("train", "eval"):
                configured = getattr(self.settings.viking, split)
                raw = overrides.get(split) or (configured.model_dump() if configured else None)
                if raw is None:
                    if split == "train" and plan.get("train_epochs", 0) > 0:
                        raise ValueError("viking.train selection is required for training")
                    continue
                selection = Selection.model_validate(raw).model_dump()
                if any(type(row) is not int or row <= 0 for row in selection["row_ids"]):
                    raise ValueError("row_ids must be positive Viking row IDs, not data.case_id")
                rows = await self.client.pages(
                    f"experiment-set/{selection['experiment_set_id']}/versions/{selection['version']}/rows",
                    page_size=500,
                )
                requested = set(selection["row_ids"])
                if requested - {row["id"] for row in rows}:
                    raise ValueError(f"Selected {split} row IDs are absent from this version")
                rows = [row for row in rows if not requested or row["id"] in requested]
                if not rows:
                    raise ValueError(f"Empty {split} selection")
                selections[split] = {**selection, "rows": rows}
            if not selections:
                raise ValueError("Configure viking.train and/or viking.eval")
            draft = await self.client.request(
                "GET", f"task-executions/{self.settings.viking.template_task_id}/clone-draft"
            )
            if draft.get("warnings"):
                raise ValueError(f"Template warnings require review: {draft['warnings']}")
            run = {
                "run_id": run_id,
                "request": request,
                "status": "active",
                "concurrency": concurrency,
                "selections": selections,
                "draft": draft,
                "viking": self.settings.viking.model_dump(exclude={"api_token"}),
                "created_at": time.time(),
            }
            # Never persist the template's old Memory identity or notification targets.
            run["draft"]["launch_request"] = {
                k: v
                for k, v in draft["launch_request"].items()
                if k in {"execution_type", "execution_plan", "operator_steps", "config"}
            }
            self.store.put("runs", run_id, run)
            return self.summary(run)

    def summary(self, run):
        batches = [b for b in self.store.all("batches") if b["run_id"] == run["run_id"]]
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "backend": "viking_direct",
            "config_fingerprint": digest([run["viking"], run["draft"], run["selections"]]),
            "execution_timeout_seconds": run["viking"]["task_time_limit_seconds"]
            * max(
                (run["request"].get("training_plan") or {}).get("eval_trials", 1),
                (run["request"].get("training_plan") or {}).get("train_trials", 1),
            )
            + 600,
            "concurrency": run["concurrency"],
            "task_ids": [b["task_id"] for b in batches if b.get("task_id")],
            "case_count": sum(len(s["rows"]) for s in run["selections"].values()),
            "selections": {
                k: {a: v for a, v in s.items() if a != "rows"} for k, s in run["selections"].items()
            },
            "batches": [
                {
                    k: b.get(k)
                    for k in ("batch_id", "task_id", "state", "name", "row_ids", "creation_error")
                }
                for b in batches
            ],
        }

    def query(self, request):
        filters = request.get("filters") or {}
        run = self.run(filters.get("_openviking_benchmark_run_id"))
        split = request["split"]
        if split in {"test", "validation"}:
            split = "eval"
        selection = run["selections"].get(split)
        if selection is None:
            return {"cases": [], "next_cursor": None}
        rows = selection["rows"]
        indices = filters.get("task_indices")
        if indices is not None:
            if any(type(i) is not int or i < 0 or i >= len(rows) for i in indices):
                raise ValueError("task_indices outside selected rows")
            rows = [rows[i] for i in indices]
        offset, limit = int(request.get("cursor") or 0), int(request.get("limit", 100))
        if offset < 0 or limit <= 0:
            raise ValueError("Invalid cursor or limit")
        page = rows[offset : offset + limit]
        descriptor = {"run_id": run["run_id"], "split": split, "row_ids": [r["id"] for r in page]}
        cases = []
        for row in page:
            data = row["data"]
            query = data.get("query", "")
            reference = data.get("response_gt", "Viking scoring criteria")
            if not isinstance(reference, str):
                reference = json.dumps(reference, ensure_ascii=False)
            identity = f"viking-{selection['experiment_set_id']}-{selection['version']}-{row['id']}"
            cases.append(
                {
                    "name": identity,
                    "task_signature": identity,
                    "input": {"task_id": str(row["id"]), "user_query": query, "query": query},
                    "rubric": {
                        "name": "viking",
                        "description": "Viking operator evaluation",
                        "criteria": [
                            {
                                "name": run["viking"]["score_column"],
                                "description": reference,
                                "weight": 1.0,
                                "required": True,
                            }
                        ],
                    },
                    "metadata": {
                        "viking_row_id": row["id"],
                        "business_case_id": data.get("case_id"),
                        "_viking_batch": descriptor,
                    },
                }
            )
        return {
            "cases": cases,
            "next_cursor": str(offset + len(page)) if offset + len(page) < len(rows) else None,
        }

    def submit(self, request):
        case = request["case"]
        descriptor = case["metadata"]["_viking_batch"]
        run = self.run(descriptor["run_id"])
        context = request["execution_context"]
        batch_id = digest(
            {
                "batch": descriptor,
                "context": context,
                "trial": case["input"].get("trial"),
                "eval_trial": case["input"].get("eval_trial"),
                "train_trial": case["input"].get("train_trial"),
            }
        )
        row_id = case["metadata"]["viking_row_id"]
        allowed = {r["id"] for r in run["selections"][descriptor["split"]]["rows"]}
        if row_id not in descriptor["row_ids"] or not set(descriptor["row_ids"]) <= allowed:
            raise ValueError("Rollout rows do not belong to this run")
        execution_id = digest([batch_id, row_id])
        existing = self.store.get("executions", execution_id)
        if existing:
            self.schedule(existing)
            return existing
        if run["status"] != "active":
            raise ValueError("Run is not active")
        execution = {
            "execution_id": execution_id,
            "run_id": run["run_id"],
            "batch_id": batch_id,
            "row_id": row_id,
            "status": "running",
            "created_at": time.time(),
            "request": request,
        }
        self.store.put("executions", execution_id, execution)
        self.schedule(execution)
        return execution

    def schedule(self, execution):
        key = execution["execution_id"]
        if execution["status"] == "running" and key not in self.workers:
            task = asyncio.create_task(self.work(execution))
            self.workers[key] = task
            task.add_done_callback(lambda _: self.workers.pop(key, None))

    async def ensure_batch(self, execution):
        batch_id = execution["batch_id"]
        async with self.batch_locks.setdefault(batch_id, asyncio.Lock()):
            batch = self.store.get("batches", batch_id)
            if batch and batch.get("task_id"):
                return batch
            run = self.run(execution["run_id"])
            if run["status"] != "active":
                raise ValueError("Run is not active")
            if batch:  # A saved intent may already have reached Viking. Never blindly POST again.
                matches = await self.client.pages(
                    "task-executions",
                    keyword=batch["name"],
                    experiment_set_id=batch["body"]["experiment_set_id"],
                    version=batch["body"]["version"],
                )
                matches = [t for t in matches if t.get("name") == batch["name"]]
                if len(matches) != 1:
                    raise UnresolvedCreation(
                        "创建结果不确定；未重发。请核查同名 Viking 实验：" + batch["name"]
                    )
                detail = await self.client.request("GET", f"task-executions/{matches[0]['id']}")
                recovered = await self.client.request(
                    "GET", f"task-executions/{matches[0]['id']}/clone-draft"
                )
                launch = recovered["launch_request"]
                if (
                    set(launch.get("target_row_ids") or []) != set(batch["row_ids"])
                    or launch.get("experiment_set_id") != batch["body"]["experiment_set_id"]
                    or launch.get("version") != batch["body"]["version"]
                ):
                    raise UnresolvedCreation("同名实验的行 ID 无法核对，拒绝自动绑定")
                batch.update(task_id=detail["id"], state=detail["status"])
            else:
                descriptor = execution["request"]["case"]["metadata"]["_viking_batch"]
                settings = Viking.model_validate({**run["viking"], "api_token": "not-persisted"})
                body = launch_request(
                    run["draft"],
                    selection=run["selections"][descriptor["split"]],
                    row_ids=descriptor["row_ids"],
                    name="ov-" + batch_id[:32],
                    concurrency=run["concurrency"],
                    settings=settings,
                )
                # Bound platform tasks across this adapter, including separate runs.
                # Hold the admission lock through POST so concurrent callers cannot
                # both claim the final slot. Row concurrency remains task-local.
                async with self.task_admission_lock:
                    while True:
                        active = 0
                        for other in self.store.all("batches"):
                            if other["state"] in TERMINAL:
                                continue
                            if not other.get("task_id"):
                                raise UnresolvedCreation("另一个批次创建待核对：" + other["name"])
                            detail = await self.client.request(
                                "GET", f"task-executions/{other['task_id']}"
                            )
                            if detail["status"] not in TERMINAL:
                                active += 1
                            else:
                                other["state"] = detail["status"]
                                self.store.put("batches", other["batch_id"], other)
                        if self.run(run["run_id"])["status"] != "active":
                            raise ValueError("Run is not active")
                        if active < self.settings.service.max_active_tasks:
                            break
                        await asyncio.sleep(settings.poll_interval_seconds)
                    await preflight(self.client, body)
                    batch = {
                        "batch_id": batch_id,
                        "run_id": run["run_id"],
                        "name": body["name"],
                        "row_ids": descriptor["row_ids"],
                        "body": body,
                        "state": "creating",
                        "created_at": time.time(),
                    }
                    self.store.put("batches", batch_id, batch)
                    try:
                        task = await self.client.request("POST", "task-executions", json=body)
                        task_id = int(task["id"])
                    except Exception as exc:
                        batch["creation_error"] = type(exc).__name__ + ": " + str(exc).split("?")[0]
                        self.store.put("batches", batch_id, batch)
                        raise UnresolvedCreation(
                            "创建结果待核对，未自动重发："
                            + body["name"]
                            + "; "
                            + batch["creation_error"]
                        ) from exc
                    batch.update(task_id=task_id, state=task.get("status", "pending"))
                    self.store.put("batches", batch_id, batch)
            self.store.put("batches", batch_id, batch)
            return batch

    async def poll_batch(self, batch):
        key = batch["batch_id"]
        async with self.batch_locks.setdefault(key, asyncio.Lock()):
            if (
                time.monotonic() - self.last_polls.get(key, 0)
                < self.settings.viking.poll_interval_seconds
            ):
                return self.store.get("batches", key)
            task_id = batch["task_id"]
            detail = await self.client.request("GET", f"task-executions/{task_id}")
            rows = await self.client.pages(f"task-executions/{task_id}/results")
            for row in rows:
                self.store.put("rows", f"{task_id}:{row['case_id']}", row)
            batch.update(state=detail["status"], detail=detail)
            self.store.put("batches", key, batch)
            self.last_polls[key] = time.monotonic()
            return batch

    async def work(self, execution):
        try:
            batch = await self.ensure_batch(execution)
            while True:
                batch = await self.poll_batch(batch)
                result = self.store.get("rows", f"{batch['task_id']}:{execution['row_id']}")
                if result and result["status"] in ROW_TERMINAL:
                    break
                if batch["state"] in TERMINAL:
                    result = result or {
                        "case_id": execution["row_id"],
                        "status": "failed",
                        "output": {},
                    }
                    break
                if (
                    time.time() - batch["created_at"]
                    > batch["body"]["task_time_limit_seconds"] + 300
                ):
                    raise TimeoutError(
                        "Viking experiment exceeded local wait deadline; inspect/cancel the remote task explicitly"
                    )
                await asyncio.sleep(self.settings.viking.poll_interval_seconds)
            log_error = None
            try:
                logs = await self.client.pages(
                    f"task-executions/{batch['task_id']}/logs",
                    row_id=execution["row_id"],
                    page_size=500,
                )
            except Exception as exc:
                logs, log_error = [], type(exc).__name__
            self.store.put("traces", execution["execution_id"] + ":logs", logs)
            messages, trace_info = await collect_trace(
                self.client, result, logs, lambda name, trace: self.store.put("traces", name, trace)
            )
            if log_error:
                trace_info["trace_errors"].append(
                    {"error": "Row log download failed: " + log_error}
                )
            request = execution["request"]
            output = result.get("output") or {}
            settings = self.run(execution["run_id"])["viking"]
            score = output.get(settings["score_column"])
            valid = (
                result["status"] == "success"
                and type(score) in {int, float}
                and math.isfinite(score)
                and 0 <= score <= 1
            )
            query = request["case"]["input"].get("query")
            expected_turns = len(query) if isinstance(query, list) else 1
            if len(trace_info["trace_prefixes"]) != expected_turns:
                trace_info["trace_errors"].append(
                    {
                        "error": "Trace turn count mismatch (missing turns or extra retry branches); commit quarantined"
                    }
                )
            metadata = {
                "backend": "viking_direct",
                "benchmark_run_id": execution["run_id"],
                "viking_batch_id": execution["batch_id"],
                "viking_task_id": batch["task_id"],
                "viking_row_id": execution["row_id"],
                "viking_result_id": result.get("id"),
                "agent_main_request_ids": [
                    p.rstrip("/").split("/")[-2] for p in trace_info["trace_prefixes"]
                ],
                "row_status": result["status"],
                "evaluation_valid": valid,
                "commit_eligible": valid and not trace_info["trace_errors"],
                "error_message": result.get("error_message"),
                "raw_result_path": str(
                    self.store.path("rows", f"{batch['task_id']}:{execution['row_id']}")
                ),
                **trace_info,
            }
            evaluation = (
                {
                    "score": score,
                    "passed": score >= settings["pass_threshold"],
                    "criterion_results": [
                        {
                            "criterion_name": settings["score_column"],
                            "score": score,
                            "passed": score >= settings["pass_threshold"],
                            "feedback": [
                                str(
                                    output[
                                        settings["score_column"].removesuffix("_score")
                                        + "_evidence"
                                    ]
                                )
                            ]
                            if output.get(
                                settings["score_column"].removesuffix("_score") + "_evidence"
                            )
                            else [],
                            "evidence": [],
                        }
                    ],
                    "feedback": [],
                    "metadata": {"column": settings["score_column"]},
                }
                if valid
                else None
            )
            execution.update(
                status="completed",
                rollout={
                    "case": request["case"],
                    "messages": messages,
                    "policy_snapshot_id": request["execution_context"]["policy_snapshot_id"],
                    "evaluation": evaluation,
                    "metadata": metadata,
                },
            )
        except asyncio.CancelledError:
            raise  # Persisted running request resumes after adapter restart.
        except UnresolvedCreation as exc:
            execution["error"] = str(exc)
        except Exception as exc:
            # Preserve raw files; no fabricated negative score for transport/evaluator errors.
            execution.update(
                status="completed",
                error=type(exc).__name__ + ": " + str(exc).split("?")[0],
                rollout={
                    "case": execution["request"]["case"],
                    "messages": [],
                    "policy_snapshot_id": execution["request"]["execution_context"][
                        "policy_snapshot_id"
                    ],
                    "evaluation": None,
                    "metadata": {
                        "backend": "viking_direct",
                        "evaluation_valid": False,
                        "commit_eligible": False,
                        "adapter_error": type(exc).__name__,
                    },
                },
            )
        execution["updated_at"] = time.time()
        self.store.put("executions", execution["execution_id"], execution)

    async def finish(self, run_id, *, cancel=False):
        run = self.run(run_id)
        if cancel:
            run["status"] = "cancelling"
            self.store.put("runs", run_id, run)
            # Wait for an already-started POST to record its task ID before cancelling.
            async with self.run_locks.setdefault(run_id, asyncio.Lock()):
                return await self.finish_locked(run_id, cancel=True)
        return await self.finish_locked(run_id, cancel=False)

    async def finish_locked(self, run_id, *, cancel):
        run = self.run(run_id)
        for batch in self.store.all("batches"):
            if batch["run_id"] != run_id:
                continue
            if not batch.get("task_id"):
                raise HTTPException(
                    409,
                    "Creation unresolved; reconcile Viking experiment before completing/cancelling",
                )
            detail = await self.client.request("GET", f"task-executions/{batch['task_id']}")
            if detail["status"] not in TERMINAL:
                if not cancel:
                    return {
                        **self.summary(run),
                        "status": "finalizing",
                        "reason": "Viking experiment still running",
                    }
                await self.client.request("POST", f"task-executions/{batch['task_id']}/cancel")
            batch["state"] = "cancelled" if cancel else detail["status"]
            self.store.put("batches", batch["batch_id"], batch)
        if not cancel and any(
            e["run_id"] == run_id and e["status"] == "running" for e in self.store.all("executions")
        ):
            return {
                **self.summary(run),
                "status": "finalizing",
                "reason": "Local trace/result collection still running",
            }
        run["status"] = "cancelled" if cancel else "completed"
        self.store.put("runs", run_id, run)
        return self.summary(run)


def create_app(client, settings, *, state_dir, memory_proxy=None):
    app = FastAPI(title="Ark Viking direct adapter")
    store = RunStore(state_dir)
    adapter = VikingAdapter(client, settings, store)
    app.state.adapter = adapter
    if memory_proxy is not None:
        install_memory_proxy(app, memory_proxy)

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    @app.get("/healthz")
    async def health():
        return {"status": "ok", "backend": "viking_direct"}

    @app.post("/v1/runs/start")
    async def start(request: dict):
        return await adapter.start(request)

    @app.get("/v1/runs/{run_id}")
    async def status(run_id: str):
        return adapter.summary(adapter.run(run_id))

    @app.post("/v1/runs/{run_id}/complete")
    async def complete(run_id: str):
        return await adapter.finish(run_id)

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        return await adapter.finish(run_id, cancel=True)

    @app.post("/v1/cases/query")
    async def query(request: dict):
        return adapter.query(request)

    def public_execution(execution):
        return {k: v for k, v in execution.items() if k != "request"}

    @app.post("/v1/rollouts/execute")
    async def execute(request: dict):
        return public_execution(adapter.submit(request))

    @app.get("/v1/rollouts/executions/{execution_id}")
    async def result(execution_id: str):
        execution = store.get("executions", execution_id)
        if execution is None:
            raise HTTPException(404, "Execution not found")
        adapter.schedule(execution)
        return public_execution(execution)

    @app.on_event("shutdown")
    async def shutdown():
        workers = list(adapter.workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await client.close()
        store.close()

    return app
