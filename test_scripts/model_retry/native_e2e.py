#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native service/QueueFS E2E with a loopback-only fake model provider.

Run one case per process. Never imports repository test conftest or MockLocalAGFS.
The only instrumentation is a copy of already-emitted model retry metric events.
"""

import argparse
import asyncio
import json
import tempfile
import threading
import time
import traceback
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CASES = (
    "resource-success",
    "resource-embedding-429",
    "resource-embedding-401",
    "session-success",
    "session-vlm-429",
    "session-vlm-401",
    "resource-cancel",
)


def native_preflight():
    from openviking.pyagfs import get_binding_client
    from openviking.storage.vectordb import engine

    binding, _ = get_binding_client()
    assert binding is not None, "native RAGFSBindingClient is unavailable"
    assert hasattr(engine, "PersistStore"), "native engine PersistStore is unavailable"
    assert engine.ENGINE_VARIANT != "unavailable", "native vector engine is unavailable"
    return {
        "ragfs_binding": f"{binding.__module__}.{binding.__name__}",
        "engine_variant": engine.ENGINE_VARIANT,
    }


class FakeProvider:
    def __init__(self, case):
        self.case = case
        self.armed = False
        self.requests = []
        self.lock = threading.Lock()
        self.blocked = threading.Event()
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model_type = "embedding" if self.path.endswith("/embeddings") else "vlm"
                with owner.lock:
                    owner.requests.append(
                        {"model_type": model_type, "path": self.path, "at": time.time()}
                    )
                if owner.armed and owner.case == "resource-cancel" and model_type == "vlm":
                    owner.blocked.set()
                    owner.release.wait(60)
                code = None
                if (
                    owner.armed
                    and owner.case.startswith("resource-embedding-")
                    and model_type == "embedding"
                ):
                    code = int(owner.case.rsplit("-", 1)[1])
                if owner.armed and owner.case.startswith("session-vlm-") and model_type == "vlm":
                    code = int(owner.case.rsplit("-", 1)[1])
                if code:
                    payload = {
                        "error": {
                            "message": "Rate limit exceeded" if code == 429 else "Unauthorized",
                            "type": "rate_limit_error" if code == 429 else "authentication_error",
                            "code": str(code),
                        }
                    }
                elif model_type == "embedding":
                    inputs = body.get("input", "")
                    count = (
                        len(inputs)
                        if isinstance(inputs, list) and inputs and isinstance(inputs[0], str)
                        else 1
                    )
                    payload = {
                        "object": "list",
                        "model": body.get("model"),
                        "data": [
                            {"object": "embedding", "index": i, "embedding": [0.1] * 16}
                            for i in range(count)
                        ],
                        "usage": {"prompt_tokens": 4, "total_tokens": 4},
                    }
                elif self.path.endswith("/chat/completions"):
                    payload = {
                        "id": "chatcmpl-fixture",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body.get("model"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "# Summary\n\nThis fixture verifies bounded model retries and native queue completion.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12},
                    }
                else:
                    code = 400
                    payload = {"error": {"message": "Unexpected local fixture endpoint"}}
                raw = json.dumps(payload).encode()
                try:
                    self.send_response(code or 200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


async def wait_task(tracker, task_id, ctx, timeout=120):
    async with asyncio.timeout(timeout):
        while True:
            task = await tracker.get(task_id, ctx.account_id, ctx.user.user_id)
            if task and task.status.value in {"completed", "failed", "cancelled"}:
                return task.to_dict()
            await asyncio.sleep(0.05)


def assert_resource_stage_contract(terminal, events):
    """Check new retry stages independently of the existing token accounting."""
    retry_stages = {}
    for event_kind in ("logical_call", "attempt"):
        stages = {
            event["stage"]
            for event in events
            if event["event"] == event_kind
            and event.get("model_type") == "vlm"
            and event.get("operation") == "add_resource"
        }
        assert {"file_summary", "directory_overview"} <= stages, (event_kind, stages)
        assert "semantic_execute" not in stages, (event_kind, stages)
        retry_stages[event_kind] = sorted(stages)

    # Exact paths verified against the native resource-success result and the
    # AddResourceProcessor telemetry-to-usage serialization contract.
    tokens = terminal["result"]["usage"]["tokens"]
    telemetry_tokens = terminal["result"]["telemetry"]["summary"]["tokens"]
    assert telemetry_tokens == tokens, "Task telemetry and usage token summaries diverged"
    legacy_stages = {
        stage
        for stage, sources in tokens["stages"].items()
        if sources.get("llm", {}).get("total", 0) > 0
    }
    assert legacy_stages == {"semantic_execute"}, legacy_stages
    llm_total = tokens["llm"]["total"]
    assert llm_total > 0
    assert tokens["stages"]["semantic_execute"]["llm"]["total"] == llm_total
    return {
        "retry_vlm_stages": retry_stages,
        "legacy_llm_stages": sorted(legacy_stages),
        "legacy_llm_tokens": llm_total,
    }


async def run_case(case, workspace, evidence):
    from openviking.message import TextPart
    from openviking.metrics.datasources.model_retry import ModelRetryEventDataSource
    from openviking.server.identity import RequestContext, Role
    from openviking.service.core import OpenVikingService
    from openviking.service.task_tracker import get_task_tracker
    from openviking.storage.queuefs import get_queue_manager
    from openviking.storage.viking_fs import get_viking_fs
    from openviking_cli.session.user_id import UserIdentifier
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    provider = FakeProvider(case)
    events = []
    original_descriptor = ModelRetryEventDataSource.__dict__["record"]
    original_record = ModelRetryEventDataSource.record

    def observe(cls, event, **fields):
        events.append({"event": event, **fields})
        original_record(event, **fields)

    ModelRetryEventDataSource.record = classmethod(observe)
    config = {
        "storage": {
            "workspace": str(workspace),
            "agfs": {
                "backend": "local",
                "queuefs": {"backend": "sqlite"},
                "pathlock": {"provider": "filesystem"},
            },
            "vectordb": {"backend": "local", "name": "retry_e2e"},
        },
        "embedding": {
            "max_retries": 3,
            "max_concurrent": 1,
            "dense": {
                "provider": "openai",
                "model": "fixture-embedding",
                "api_key": "fixture-not-a-secret",
                "api_base": provider.url,
                "dimension": 16,
            },
        },
        "vlm": {
            "provider": "openai",
            "model": "fixture-chat",
            "api_key": "fixture-not-a-secret",
            "api_base": provider.url,
            "max_retries": 3,
            "max_concurrent": 1,
        },
        "memory": {"extraction_enabled": False},
    }
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(config_dict=config)
    user = UserIdentifier.the_default_user("retry-e2e")
    ctx = RequestContext(user=user, role=Role.USER)
    service = OpenVikingService(path=str(workspace), user=user)
    initialized = False
    try:
        await service.initialize()
        initialized = True
        fs = get_viking_fs()
        qm = get_queue_manager()
        evidence["agfs_runtime_type"] = f"{type(qm._agfs).__module__}.{type(qm._agfs).__name__}"
        assert "mock" not in evidence["agfs_runtime_type"].lower()
        # Native initialization creates directory vectors. Drain those genuine
        # bootstrap deliveries before fault injection or operation accounting.
        # Otherwise a resource fault also poisons unrelated namespace setup.
        await qm.wait_complete(timeout=120)
        with provider.lock:
            evidence["bootstrap_http_counts"] = dict(
                Counter(request["model_type"] for request in provider.requests)
            )
            provider.requests.clear()
        evidence["bootstrap_model_event_counts"] = dict(Counter(event["event"] for event in events))
        events.clear()
        provider.armed = True
        tracker = get_task_tracker()
        if case.startswith("session-"):
            session = await service.sessions.create(ctx, session_id="retry-e2e-session")
            await session.add_message_async(
                "user", [TextPart("Please remember this retry fixture.")]
            )
            await session.add_message_async(
                "assistant", [TextPart("This is a deterministic test response.")]
            )
            submitted = await session.commit_async(
                memory_policy={"working_memory": {"enabled": True}}
            )
        else:
            source = workspace.parent / "retry-fixture.md"
            source.write_text(
                "# Retry fixture\n\nOne local file verifies bounded retry ownership.\n"
            )
            submitted = await service.resources.add_resource(
                str(source), ctx=ctx, to="viking://resources/retry-e2e", summarize=True, wait=False
            )
        evidence["submitted"] = submitted
        task_id = submitted["task_id"]
        if case == "resource-cancel":
            assert await asyncio.to_thread(provider.blocked.wait, 30), (
                "No VLM HTTP request reached cancellation fixture"
            )
            await tracker.cancel(task_id, ctx.account_id, ctx.user.user_id)
            provider.release.set()
        terminal = await wait_task(tracker, task_id, ctx)
        evidence["task"] = terminal
        await qm.wait_complete(timeout=120)
        queue_status = await qm.check_status()
        evidence["queues"] = {
            name: {
                field: getattr(status, field)
                for field in ("pending", "in_progress", "processed", "requeue_count", "error_count")
            }
            for name, status in queue_status.items()
        }
        expected_status = (
            "cancelled"
            if case == "resource-cancel"
            else "failed"
            if case.endswith(("429", "401"))
            else "completed"
        )
        assert terminal["status"] == expected_status, (terminal["status"], expected_status)
        for status in evidence["queues"].values():
            assert status["pending"] == status["in_progress"] == 0
        if case.startswith("session-"):
            archive = submitted["archive_uri"]
            markers = {
                name: await fs.exists(f"{archive}/{name}", ctx=ctx)
                for name in (".done", ".failed.json")
            }
            evidence["markers"] = markers
            assert markers[".done"] == (expected_status == "completed")
            assert markers[".failed.json"] == (expected_status == "failed")
            assert evidence["queues"].get("SessionCommit", {}).get("processed", 0) >= 1, (
                "Real SessionCommit consumer was not observed"
            )
        else:
            # A fresh native tree lease proves the producer/semantic handoff was
            # settled. This intentionally runs after actual queue drain.
            root_uri = submitted["root_uri"]
            path = fs._uri_to_path(root_uri, ctx=ctx)
            lease = await fs._async_agfs.pathlock_acquire_batch(
                [{"path": path, "kind": "tree"}], timeout_secs=1
            )
            await fs._async_agfs.pathlock_release(lease)
            evidence["tree_lock_reacquired"] = True
        if case.endswith(("429", "401")):
            model_type = "embedding" if case.startswith("resource-") else "vlm"
            attempts = [
                event
                for event in events
                if event["event"] == "attempt" and event.get("model_type") == model_type
            ]
            logical = [
                event
                for event in events
                if event["event"] == "logical_call" and event.get("model_type") == model_type
            ]
            factor = 4 if case.endswith("429") else 1
            physical = sum(request["model_type"] == model_type for request in provider.requests)
            assert logical, "No terminal logical model call was observed"
            assert physical == len(attempts) == factor * len(logical), (
                physical,
                len(attempts),
                len(logical),
                factor,
            )
            await asyncio.sleep(0.5)
            assert physical == sum(
                request["model_type"] == model_type for request in provider.requests
            ), "Model traffic continued after terminal task/queue drain"
            for queue in ("Embedding", "Semantic"):
                assert evidence["queues"].get(queue, {}).get("requeue_count", 0) == 0
        else:
            assert provider.requests, "No real SDK-to-loopback HTTP request was observed"
        if case == "resource-success":
            evidence["stage_contract"] = assert_resource_stage_contract(terminal, events)
        evidence["status"] = "passed"
    finally:
        evidence["http_counts"] = dict(
            Counter(request["model_type"] for request in provider.requests)
        )
        evidence["model_events"] = events
        provider.release.set()
        if initialized:
            await service.close()
        provider.close()
        ModelRetryEventDataSource.record = original_descriptor
        OpenVikingConfigSingleton.reset_instance()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES, default="resource-success")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    evidence = {
        "case": args.case,
        "scope": "native in-process service, SQLite QueueFS, filesystem PathLock, local vector engine, real OpenAI SDK to loopback HTTP fixture; no OV server HTTP ingress or multi-pod claim",
        "status": "failed",
    }
    exit_code = 1
    try:
        evidence["native"] = native_preflight()
        if args.preflight_only:
            evidence["status"] = "preflight_passed"
        elif args.workspace:
            workspace = Path(args.workspace)
            workspace.mkdir(parents=True, exist_ok=False)
            asyncio.run(run_case(args.case, workspace, evidence))
        else:
            with tempfile.TemporaryDirectory(prefix="ov-native-retry-") as directory:
                asyncio.run(run_case(args.case, Path(directory) / "workspace", evidence))
        exit_code = 0
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}: {error}"
        evidence["traceback"] = traceback.format_exc()
        if "native" not in evidence:
            evidence["status"] = "blocked_native_preflight"
            exit_code = 2
    Path(args.output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: evidence[key] for key in ("case", "status")}, ensure_ascii=False))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
