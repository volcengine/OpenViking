#!/usr/bin/env python3
"""OpenViking server mixed-load benchmark."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence

DEFAULT_DATA_ROOT_URI = "viking://resources/bench/load_test"
DEFAULT_SESSION_PREFIX = "bench-load-"
DEFAULT_QUERIES = [
    "OpenViking server load test",
    "session commit memory extraction",
    "resource ingestion benchmark document",
    "concurrent retrieval latency",
]
DEFAULT_SLOW_THRESHOLDS_MS = (1000, 3000, 5000)
MAX_ERROR_MESSAGE_LEN = 800

PROFILE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "smoke": {
        "resource_count": 6,
        "session_count": 6,
        "messages_per_session": 3,
        "resource_concurrency": 2,
        "search_concurrency": 2,
        "session_concurrency": 2,
        "commit_concurrency": 2,
        "mixed_concurrency": 4,
        "phase_seconds": 8.0,
        "mixed_seconds": 12.0,
        "warmup_seconds": 1.0,
    },
    "standard": {
        "resource_count": 24,
        "session_count": 24,
        "messages_per_session": 6,
        "resource_concurrency": 6,
        "search_concurrency": 8,
        "session_concurrency": 8,
        "commit_concurrency": 6,
        "mixed_concurrency": 16,
        "phase_seconds": 30.0,
        "mixed_seconds": 60.0,
        "warmup_seconds": 3.0,
    },
    "stress": {
        "resource_count": 80,
        "session_count": 80,
        "messages_per_session": 8,
        "resource_concurrency": 16,
        "search_concurrency": 24,
        "session_concurrency": 24,
        "commit_concurrency": 16,
        "mixed_concurrency": 48,
        "phase_seconds": 60.0,
        "mixed_seconds": 180.0,
        "warmup_seconds": 5.0,
    },
}


@dataclass
class BenchmarkConfig:
    server_url: str
    api_key: Optional[str]
    account: Optional[str]
    user: Optional[str]
    agent_id: Optional[str]
    timeout: float
    adapters: List[str]
    profile: str
    resource_count: int
    session_count: int
    messages_per_session: int
    resource_concurrency: int
    search_concurrency: int
    session_concurrency: int
    commit_concurrency: int
    mixed_concurrency: int
    phase_seconds: float
    mixed_seconds: float
    warmup_seconds: float
    drain_timeout: float
    task_poll_interval: float
    request_window_seconds: float
    local_data_dir: str
    output_dir: str
    data_root_uri: str
    session_prefix: str
    clear_before_run: bool
    cleanup_at_end: bool
    ov_bin: str
    seed: int
    find_limit: int


def direct_http_agent_id(config: BenchmarkConfig) -> str:
    """Avoid loading the user's global ovcli.conf when agent_id is unset."""
    return config.agent_id or ""


@dataclass
class AdapterResult:
    success: bool
    result: Any = None
    status_code: Optional[int] = None
    exception_type: Optional[str] = None
    error_message: Optional[str] = None
    raw_stdout: Optional[str] = None
    raw_stderr: Optional[str] = None
    command: Optional[List[str]] = None


@dataclass
class RequestEvent:
    adapter: str
    scenario: str
    operation: str
    started_at: str
    ended_at: str
    elapsed_ms_since_run_start: float
    latency_ms: float
    success: bool
    status_code: Optional[int]
    exception_type: Optional[str]
    error_message: Optional[str]
    session_id: Optional[str] = None
    resource_uri: Optional[str] = None
    task_id: Optional[str] = None
    worker_id: Optional[int] = None
    command: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskEvent:
    adapter: str
    task_id: str
    task_type: str
    origin_scenario: str
    completion_scenario: str
    status: str
    resource_id: Optional[str]
    local_duration_ms: float
    server_duration_ms: Optional[float]
    error_message: Optional[str]
    result: Any
    polled_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PendingTask:
    adapter_name: str
    task_id: str
    task_type: str
    origin_scenario: str
    resource_id: Optional[str]
    local_started_monotonic: float


@dataclass
class PhaseMetadata:
    adapter: str
    scenario: str
    started_at: str
    ended_at: str
    duration_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Recorder:
    request_events: List[RequestEvent] = field(default_factory=list)
    task_events: List[TaskEvent] = field(default_factory=list)
    phases: List[PhaseMetadata] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add_request(self, event: RequestEvent) -> None:
        self.request_events.append(event)

    def add_task(self, event: TaskEvent) -> None:
        self.task_events.append(event)

    def add_phase(self, phase: PhaseMetadata) -> None:
        self.phases.append(phase)

    def add_note(self, note: str) -> None:
        self.notes.append(note)


class LoadAdapter:
    name: str

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def health(self) -> Any: ...

    async def system_status(self) -> Any: ...

    async def observer_queue(self) -> Any: ...

    async def add_resource(
        self, *, path: str, to: str, reason: str, wait: bool, timeout: Optional[float]
    ) -> Any: ...

    async def wait_processed(self, timeout: Optional[float]) -> Any: ...

    async def find(self, *, query: str, target_uri: str, limit: int) -> Any: ...

    async def search(
        self, *, query: str, target_uri: str, session_id: Optional[str], limit: int
    ) -> Any: ...

    async def grep(self, *, uri: str, pattern: str, limit: int) -> Any: ...

    async def glob(self, *, uri: str, pattern: str, limit: int) -> Any: ...

    async def add_message(self, *, session_id: str, role: str, content: str) -> Any: ...

    async def commit_session(self, *, session_id: str) -> Any: ...

    async def get_task(self, *, task_id: str) -> Any: ...


class AsyncHTTPAdapter(LoadAdapter):
    def __init__(self, *, name: str, config: BenchmarkConfig, sdk_import: bool) -> None:
        self.name = name
        self._config = config
        self._sdk_import = sdk_import
        self._client: Any = None

    async def initialize(self) -> None:
        if self._sdk_import:
            import openviking as ov

            client_cls = ov.AsyncHTTPClient
        else:
            from openviking_cli.client.http import AsyncHTTPClient

            client_cls = AsyncHTTPClient
        self._client = client_cls(
            url=self._config.server_url,
            api_key=self._config.api_key,
            account=self._config.account,
            user=self._config.user,
            agent_id=direct_http_agent_id(self._config),
            timeout=self._config.timeout,
            extra_headers={},
        )
        await self._client.initialize()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def health(self) -> Any:
        return {"healthy": await self._client.health()}

    async def system_status(self) -> Any:
        return await self._client._get_system_status()

    async def observer_queue(self) -> Any:
        return await self._client._get_queue_status()

    async def add_resource(
        self, *, path: str, to: str, reason: str, wait: bool, timeout: Optional[float]
    ) -> Any:
        return await self._client.add_resource(
            path=path,
            to=to,
            reason=reason,
            wait=wait,
            timeout=timeout,
            strict=False,
        )

    async def wait_processed(self, timeout: Optional[float]) -> Any:
        return await self._client.wait_processed(timeout=timeout)

    async def find(self, *, query: str, target_uri: str, limit: int) -> Any:
        result = await self._client.find(query=query, target_uri=target_uri, limit=limit)
        return to_jsonable(result)

    async def search(
        self, *, query: str, target_uri: str, session_id: Optional[str], limit: int
    ) -> Any:
        result = await self._client.search(
            query=query,
            target_uri=target_uri,
            session_id=session_id,
            limit=limit,
        )
        return to_jsonable(result)

    async def grep(self, *, uri: str, pattern: str, limit: int) -> Any:
        return await self._client.grep(uri=uri, pattern=pattern, node_limit=limit)

    async def glob(self, *, uri: str, pattern: str, limit: int) -> Any:
        return await self._client.glob(pattern=pattern, uri=uri)

    async def add_message(self, *, session_id: str, role: str, content: str) -> Any:
        return await self._client.add_message(session_id, role, content=content)

    async def commit_session(self, *, session_id: str) -> Any:
        return await self._client.commit_session(session_id)

    async def get_task(self, *, task_id: str) -> Any:
        return await self._client.get_task(task_id)


class CliSubprocessAdapter(LoadAdapter):
    def __init__(self, *, config: BenchmarkConfig, cli_config_path: Path) -> None:
        self.name = "cli-subprocess"
        self._config = config
        self._cli_config_path = cli_config_path
        self._env = os.environ.copy()
        self._env["OPENVIKING_CLI_CONFIG_FILE"] = str(cli_config_path)

    async def initialize(self) -> None:
        self._cli_config_path.parent.mkdir(parents=True, exist_ok=True)
        self._cli_config_path.write_text(
            json.dumps(build_cli_config_payload(self._config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result = await self.health()
        if isinstance(result, AdapterResult) and not result.success:
            raise RuntimeError(result.error_message or "ov health failed")

    async def close(self) -> None:
        return None

    async def health(self) -> AdapterResult:
        return await self._run(["health"], operation_timeout=self._config.timeout)

    async def system_status(self) -> AdapterResult:
        return await self._run(["status"])

    async def observer_queue(self) -> AdapterResult:
        return await self._run(["observer", "queue"])

    async def add_resource(
        self, *, path: str, to: str, reason: str, wait: bool, timeout: Optional[float]
    ) -> AdapterResult:
        args = ["add-resource", path, "--to", to, "--reason", reason]
        if wait:
            args.append("--wait")
            if timeout is not None:
                args.extend(["--timeout", str(timeout)])
        return await self._run(args, operation_timeout=max(self._config.timeout, 60.0))

    async def wait_processed(self, timeout: Optional[float]) -> AdapterResult:
        args = ["wait"]
        if timeout is not None:
            args.extend(["--timeout", str(timeout)])
        return await self._run(args, operation_timeout=timeout or self._config.timeout)

    async def find(self, *, query: str, target_uri: str, limit: int) -> AdapterResult:
        return await self._run(["find", query, "--uri", target_uri, "-n", str(limit)])

    async def search(
        self, *, query: str, target_uri: str, session_id: Optional[str], limit: int
    ) -> AdapterResult:
        args = ["search", query, "--uri", target_uri, "-n", str(limit)]
        if session_id:
            args.extend(["--session-id", session_id])
        return await self._run(args)

    async def grep(self, *, uri: str, pattern: str, limit: int) -> AdapterResult:
        return await self._run(["grep", pattern, "--uri", uri, "-n", str(limit)])

    async def glob(self, *, uri: str, pattern: str, limit: int) -> AdapterResult:
        return await self._run(["glob", pattern, "--uri", uri, "-n", str(limit)])

    async def add_message(self, *, session_id: str, role: str, content: str) -> AdapterResult:
        return await self._run(
            ["session", "add-message", session_id, "--role", role, "--content", content]
        )

    async def commit_session(self, *, session_id: str) -> AdapterResult:
        return await self._run(["session", "commit", session_id])

    async def get_task(self, *, task_id: str) -> AdapterResult:
        return await self._run(["task", "status", task_id])

    def build_command(self, args: Sequence[str]) -> List[str]:
        return [
            self._config.ov_bin,
            "--output",
            "json",
            "--compact",
            "--no-progress",
            *args,
        ]

    async def _run(
        self, args: Sequence[str], *, operation_timeout: Optional[float] = None
    ) -> AdapterResult:
        command = self.build_command(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=operation_timeout or self._config.timeout,
            )
        except asyncio.TimeoutError:
            return AdapterResult(
                success=False,
                exception_type="TimeoutError",
                error_message=f"CLI command timed out after {operation_timeout or self._config.timeout}s",
                command=command,
            )
        except Exception as exc:  # pragma: no cover - exercised in real runs
            return AdapterResult(
                success=False,
                exception_type=type(exc).__name__,
                error_message=truncate_error_message(str(exc)),
                command=command,
            )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        parsed = parse_cli_process_result(proc.returncode, stdout, stderr)
        parsed.command = command
        return parsed


class BenchmarkRunner:
    def __init__(self, config: BenchmarkConfig) -> None:
        self.config = config
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.random = random.Random(config.seed)
        self.recorder = Recorder()
        self.run_start_monotonic = time.perf_counter()
        self.pending_tasks: List[PendingTask] = []
        self.session_ids_by_adapter: Dict[str, List[str]] = {}
        self.adapters: Dict[str, LoadAdapter] = {}
        self.mixed_doc_counter = count()

    async def run(self) -> int:
        exit_code = 0
        try:
            await self._prepare_filesystem()
            await self._prepare_remote_state()
            self.adapters = await self._create_adapters()
            for adapter in self.adapters.values():
                await self._run_adapter(adapter)
            if self.pending_tasks:
                await self._drain_tasks("final_drain", self.config.drain_timeout)
        except Exception as exc:
            self.recorder.add_note(f"fatal: {type(exc).__name__}: {exc}")
            print(f"[fatal] {type(exc).__name__}: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            if self.config.cleanup_at_end:
                try:
                    await self._clear_remote_state()
                except Exception as exc:  # pragma: no cover - best effort cleanup
                    self.recorder.add_note(f"cleanup_at_end failed: {type(exc).__name__}: {exc}")
            for adapter in self.adapters.values():
                await adapter.close()
            self._write_outputs()
            self._print_summary_path()
        return exit_code

    async def _create_adapters(self) -> Dict[str, LoadAdapter]:
        output_dir = Path(self.config.output_dir)
        adapters: Dict[str, LoadAdapter] = {}
        for name in self.config.adapters:
            if name == "sdk":
                adapter: LoadAdapter = AsyncHTTPAdapter(
                    name="sdk", config=self.config, sdk_import=True
                )
            elif name == "cli-http":
                adapter = AsyncHTTPAdapter(name="cli-http", config=self.config, sdk_import=False)
            elif name == "cli-subprocess":
                adapter = CliSubprocessAdapter(
                    config=self.config,
                    cli_config_path=output_dir / "runtime" / "ovcli.conf",
                )
            else:
                raise ValueError(f"Unknown adapter: {name}")
            await adapter.initialize()
            adapters[name] = adapter
        return adapters

    async def _prepare_filesystem(self) -> None:
        local_dir = Path(self.config.local_data_dir)
        if self.config.clear_before_run and local_dir.exists():
            shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        for adapter_name in self.config.adapters:
            adapter_dir = local_dir / "seed" / adapter_name
            adapter_dir.mkdir(parents=True, exist_ok=True)
            for index in range(self.config.resource_count):
                write_benchmark_document(
                    adapter_dir / f"resource-{index:04d}.md",
                    adapter=adapter_name,
                    index=index,
                    run_id=self.run_id,
                )

    async def _prepare_remote_state(self) -> None:
        if self.config.clear_before_run:
            await self._clear_remote_state()
        await self._ensure_remote_tree()

    async def _clear_remote_state(self) -> None:
        from openviking_cli.client.http import AsyncHTTPClient
        from openviking_cli.exceptions import NotFoundError, OpenVikingError

        client = AsyncHTTPClient(
            url=self.config.server_url,
            api_key=self.config.api_key,
            account=self.config.account,
            user=self.config.user,
            agent_id=direct_http_agent_id(self.config),
            timeout=self.config.timeout,
            extra_headers={},
        )
        await client.initialize()
        try:
            try:
                await client.rm(self.config.data_root_uri, recursive=True)
            except NotFoundError:
                pass
            except OpenVikingError as exc:
                if not is_not_found_error(exc):
                    raise
            sessions = await client.list_sessions()
            for session_id in extract_session_ids(sessions):
                if session_id.startswith(self.config.session_prefix):
                    try:
                        await client.delete_session(session_id)
                    except Exception:
                        pass
        finally:
            await client.close()

    async def _ensure_remote_tree(self) -> None:
        from openviking_cli.client.http import AsyncHTTPClient
        from openviking_cli.exceptions import OpenVikingError

        client = AsyncHTTPClient(
            url=self.config.server_url,
            api_key=self.config.api_key,
            account=self.config.account,
            user=self.config.user,
            agent_id=direct_http_agent_id(self.config),
            timeout=self.config.timeout,
            extra_headers={},
        )
        await client.initialize()
        try:
            for uri in iter_resource_tree_uris(self.config.data_root_uri):
                try:
                    await client.mkdir(uri)
                except OpenVikingError as exc:
                    if not is_already_exists_error(exc):
                        raise
            for adapter_name in self.config.adapters:
                try:
                    await client.mkdir(f"{self.config.data_root_uri}/{adapter_name}")
                except OpenVikingError as exc:
                    if not is_already_exists_error(exc):
                        raise
        finally:
            await client.close()

    async def _run_adapter(self, adapter: LoadAdapter) -> None:
        self.session_ids_by_adapter[adapter.name] = [
            f"{self.config.session_prefix}{self.run_id}-{adapter.name}-{i:04d}"
            for i in range(self.config.session_count)
        ]
        await self._run_warmup(adapter)
        await self._run_add_resources(adapter)
        await self._drain_tasks(f"{adapter.name}:resource_drain", self.config.drain_timeout)
        await self._run_session_messages(adapter)
        await self._run_timed_scenario(
            adapter=adapter,
            scenario="retrieval",
            duration_seconds=self.config.phase_seconds,
            concurrency=self.config.search_concurrency,
            worker=self._retrieval_worker,
        )
        await self._run_session_commit(adapter)
        await self._drain_tasks(f"{adapter.name}:commit_drain", self.config.drain_timeout)
        await self._run_timed_scenario(
            adapter=adapter,
            scenario="mixed",
            duration_seconds=self.config.mixed_seconds,
            concurrency=self.config.mixed_concurrency,
            worker=self._mixed_worker,
        )
        await self._drain_tasks(f"{adapter.name}:mixed_drain", self.config.drain_timeout)

    async def _run_warmup(self, adapter: LoadAdapter) -> None:
        if self.config.warmup_seconds <= 0:
            return
        await self._run_timed_scenario(
            adapter=adapter,
            scenario="warmup",
            duration_seconds=self.config.warmup_seconds,
            concurrency=1,
            worker=self._warmup_worker,
        )

    async def _warmup_worker(
        self, adapter: LoadAdapter, scenario: str, worker_id: int, stop_event: asyncio.Event
    ) -> None:
        while not stop_event.is_set():
            await self._record_request(
                adapter,
                scenario,
                "health",
                lambda: adapter.health(),
                worker_id=worker_id,
            )
            await asyncio.sleep(0.2)

    async def _run_add_resources(self, adapter: LoadAdapter) -> None:
        scenario = "add_resources"
        started = time.perf_counter()
        started_wall = utc_now()
        files = sorted((Path(self.config.local_data_dir) / "seed" / adapter.name).glob("*.md"))
        queue: asyncio.Queue[Path] = asyncio.Queue()
        for path in files:
            await queue.put(path)

        async def worker(worker_id: int) -> None:
            while True:
                try:
                    path = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                resource_uri = f"{self.config.data_root_uri}/{adapter.name}/{path.name}"
                result = await self._record_request(
                    adapter,
                    scenario,
                    "add_resource",
                    lambda p=path, uri=resource_uri: adapter.add_resource(
                        path=str(p),
                        to=uri,
                        reason="OpenViking server load benchmark",
                        wait=False,
                        timeout=None,
                    ),
                    resource_uri=resource_uri,
                    worker_id=worker_id,
                )
                self._register_task_if_present(
                    adapter=adapter,
                    scenario=scenario,
                    task_type="add_resource",
                    resource_id=resource_uri,
                    result=result,
                )

        await asyncio.gather(
            *[
                asyncio.create_task(worker(worker_id))
                for worker_id in range(max(1, self.config.resource_concurrency))
            ]
        )
        await self._record_request(
            adapter,
            scenario,
            "wait_processed",
            lambda: adapter.wait_processed(timeout=self.config.drain_timeout),
        )
        self.recorder.add_phase(
            PhaseMetadata(
                adapter=adapter.name,
                scenario=scenario,
                started_at=started_wall,
                ended_at=utc_now(),
                duration_seconds=time.perf_counter() - started,
            )
        )

    async def _run_session_messages(self, adapter: LoadAdapter) -> None:
        scenario = "session_messages"
        started = time.perf_counter()
        started_wall = utc_now()
        units = [
            (session_id, message_index)
            for session_id in self.session_ids_by_adapter[adapter.name]
            for message_index in range(self.config.messages_per_session)
        ]
        queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        for unit in units:
            await queue.put(unit)

        async def worker(worker_id: int) -> None:
            while True:
                try:
                    session_id, message_index = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                role = "user" if message_index % 2 == 0 else "assistant"
                content = build_message_content(
                    session_id=session_id,
                    message_index=message_index,
                    adapter=adapter.name,
                )
                await self._record_request(
                    adapter,
                    scenario,
                    "add_message",
                    lambda sid=session_id, r=role, c=content: adapter.add_message(
                        session_id=sid,
                        role=r,
                        content=c,
                    ),
                    session_id=session_id,
                    worker_id=worker_id,
                )

        await asyncio.gather(
            *[
                asyncio.create_task(worker(worker_id))
                for worker_id in range(max(1, self.config.session_concurrency))
            ]
        )
        self.recorder.add_phase(
            PhaseMetadata(
                adapter=adapter.name,
                scenario=scenario,
                started_at=started_wall,
                ended_at=utc_now(),
                duration_seconds=time.perf_counter() - started,
            )
        )

    async def _run_session_commit(self, adapter: LoadAdapter) -> None:
        scenario = "session_commit"
        started = time.perf_counter()
        started_wall = utc_now()
        queue: asyncio.Queue[str] = asyncio.Queue()
        for session_id in self.session_ids_by_adapter[adapter.name]:
            await queue.put(session_id)

        async def worker(worker_id: int) -> None:
            while True:
                try:
                    session_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                result = await self._record_request(
                    adapter,
                    scenario,
                    "commit_session",
                    lambda sid=session_id: adapter.commit_session(session_id=sid),
                    session_id=session_id,
                    worker_id=worker_id,
                )
                self._register_task_if_present(
                    adapter=adapter,
                    scenario=scenario,
                    task_type="session_commit",
                    resource_id=session_id,
                    result=result,
                )

        await asyncio.gather(
            *[
                asyncio.create_task(worker(worker_id))
                for worker_id in range(max(1, self.config.commit_concurrency))
            ]
        )
        self.recorder.add_phase(
            PhaseMetadata(
                adapter=adapter.name,
                scenario=scenario,
                started_at=started_wall,
                ended_at=utc_now(),
                duration_seconds=time.perf_counter() - started,
            )
        )

    async def _run_timed_scenario(
        self,
        *,
        adapter: LoadAdapter,
        scenario: str,
        duration_seconds: float,
        concurrency: int,
        worker: Callable[[LoadAdapter, str, int, asyncio.Event], Awaitable[None]],
    ) -> None:
        if duration_seconds <= 0 or concurrency <= 0:
            return
        stop_event = asyncio.Event()
        started = time.perf_counter()
        started_wall = utc_now()
        tasks = [
            asyncio.create_task(worker(adapter, scenario, worker_id, stop_event))
            for worker_id in range(concurrency)
        ]
        await asyncio.sleep(duration_seconds)
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.recorder.add_phase(
            PhaseMetadata(
                adapter=adapter.name,
                scenario=scenario,
                started_at=started_wall,
                ended_at=utc_now(),
                duration_seconds=time.perf_counter() - started,
            )
        )

    async def _retrieval_worker(
        self, adapter: LoadAdapter, scenario: str, worker_id: int, stop_event: asyncio.Event
    ) -> None:
        while not stop_event.is_set():
            await self._run_retrieval_operation(adapter, scenario, worker_id)

    async def _mixed_worker(
        self, adapter: LoadAdapter, scenario: str, worker_id: int, stop_event: asyncio.Event
    ) -> None:
        choices = [
            ("find", 35),
            ("search", 20),
            ("add_message", 20),
            ("commit_session", 10),
            ("add_resource", 10),
            ("observer_queue", 3),
            ("system_status", 2),
        ]
        operations = [name for name, weight in choices for _ in range(weight)]
        while not stop_event.is_set():
            operation = self.random.choice(operations)
            if operation in {"find", "search"}:
                await self._run_retrieval_operation(adapter, scenario, worker_id, force=operation)
            elif operation == "add_message":
                session_id = self.random.choice(self.session_ids_by_adapter[adapter.name])
                content = build_message_content(
                    session_id=session_id,
                    message_index=next(self.mixed_doc_counter),
                    adapter=adapter.name,
                )
                await self._record_request(
                    adapter,
                    scenario,
                    "add_message",
                    lambda sid=session_id, c=content: adapter.add_message(
                        session_id=sid,
                        role="user",
                        content=c,
                    ),
                    session_id=session_id,
                    worker_id=worker_id,
                )
            elif operation == "commit_session":
                session_id = self.random.choice(self.session_ids_by_adapter[adapter.name])
                result = await self._record_request(
                    adapter,
                    scenario,
                    "commit_session",
                    lambda sid=session_id: adapter.commit_session(session_id=sid),
                    session_id=session_id,
                    worker_id=worker_id,
                )
                self._register_task_if_present(
                    adapter=adapter,
                    scenario=scenario,
                    task_type="session_commit",
                    resource_id=session_id,
                    result=result,
                )
            elif operation == "add_resource":
                index = next(self.mixed_doc_counter)
                path = (
                    Path(self.config.local_data_dir) / "mixed" / adapter.name / f"mixed-{index}.md"
                )
                write_benchmark_document(
                    path, adapter=adapter.name, index=index, run_id=self.run_id
                )
                resource_uri = f"{self.config.data_root_uri}/{adapter.name}/{path.name}"
                result = await self._record_request(
                    adapter,
                    scenario,
                    "add_resource",
                    lambda p=path, uri=resource_uri: adapter.add_resource(
                        path=str(p),
                        to=uri,
                        reason="OpenViking mixed load benchmark",
                        wait=False,
                        timeout=None,
                    ),
                    resource_uri=resource_uri,
                    worker_id=worker_id,
                )
                self._register_task_if_present(
                    adapter=adapter,
                    scenario=scenario,
                    task_type="add_resource",
                    resource_id=resource_uri,
                    result=result,
                )
            elif operation == "observer_queue":
                await self._record_request(
                    adapter,
                    scenario,
                    "observer_queue",
                    lambda: adapter.observer_queue(),
                    worker_id=worker_id,
                )
            else:
                await self._record_request(
                    adapter,
                    scenario,
                    "system_status",
                    lambda: adapter.system_status(),
                    worker_id=worker_id,
                )

    async def _run_retrieval_operation(
        self,
        adapter: LoadAdapter,
        scenario: str,
        worker_id: int,
        *,
        force: Optional[str] = None,
    ) -> None:
        operation = (
            force
            or self.random.choices(
                ["find", "search", "grep", "glob"], weights=[55, 25, 10, 10], k=1
            )[0]
        )
        query = self.random.choice(DEFAULT_QUERIES)
        if operation == "find":
            await self._record_request(
                adapter,
                scenario,
                "find",
                lambda: adapter.find(
                    query=query,
                    target_uri=self.config.data_root_uri,
                    limit=self.config.find_limit,
                ),
                worker_id=worker_id,
            )
        elif operation == "search":
            session_id = self.random.choice(self.session_ids_by_adapter[adapter.name])
            await self._record_request(
                adapter,
                scenario,
                "search",
                lambda sid=session_id: adapter.search(
                    query=query,
                    target_uri=self.config.data_root_uri,
                    session_id=sid,
                    limit=self.config.find_limit,
                ),
                session_id=session_id,
                worker_id=worker_id,
            )
        elif operation == "grep":
            await self._record_request(
                adapter,
                scenario,
                "grep",
                lambda: adapter.grep(
                    uri=self.config.data_root_uri,
                    pattern="benchmark",
                    limit=self.config.find_limit,
                ),
                worker_id=worker_id,
            )
        else:
            await self._record_request(
                adapter,
                scenario,
                "glob",
                lambda: adapter.glob(
                    uri=self.config.data_root_uri,
                    pattern="*.md",
                    limit=self.config.find_limit,
                ),
                worker_id=worker_id,
            )

    async def _record_request(
        self,
        adapter: LoadAdapter,
        scenario: str,
        operation: str,
        call: Callable[[], Awaitable[Any]],
        *,
        session_id: Optional[str] = None,
        resource_uri: Optional[str] = None,
        worker_id: Optional[int] = None,
    ) -> AdapterResult:
        started_monotonic = time.perf_counter()
        started_wall = utc_now()
        result: AdapterResult
        try:
            value = await call()
            if isinstance(value, AdapterResult):
                result = value
            else:
                result = AdapterResult(success=True, result=to_jsonable(value))
        except Exception as exc:
            result = AdapterResult(
                success=False,
                exception_type=type(exc).__name__,
                error_message=truncate_error_message(str(exc)),
            )

        ended_monotonic = time.perf_counter()
        result_payload = normalize_result_payload(result.result)
        task_id = extract_task_id(result_payload)
        if task_id is None:
            task_id = extract_task_id_from_any(result.result)
        self.recorder.add_request(
            RequestEvent(
                adapter=adapter.name,
                scenario=scenario,
                operation=operation,
                started_at=started_wall,
                ended_at=utc_now(),
                elapsed_ms_since_run_start=(started_monotonic - self.run_start_monotonic) * 1000.0,
                latency_ms=(ended_monotonic - started_monotonic) * 1000.0,
                success=result.success,
                status_code=result.status_code,
                exception_type=result.exception_type,
                error_message=result.error_message,
                session_id=session_id,
                resource_uri=resource_uri or extract_root_uri(result_payload),
                task_id=task_id,
                worker_id=worker_id,
                command=" ".join(result.command) if result.command else None,
            )
        )
        return result

    def _register_task_if_present(
        self,
        *,
        adapter: LoadAdapter,
        scenario: str,
        task_type: str,
        resource_id: Optional[str],
        result: AdapterResult,
    ) -> None:
        if not result.success:
            return
        task_id = extract_task_id_from_any(result.result)
        if not task_id:
            return
        self.pending_tasks.append(
            PendingTask(
                adapter_name=adapter.name,
                task_id=task_id,
                task_type=task_type,
                origin_scenario=scenario,
                resource_id=resource_id,
                local_started_monotonic=time.perf_counter(),
            )
        )

    async def _drain_tasks(self, scenario: str, timeout: float) -> None:
        if not self.pending_tasks or timeout <= 0:
            self._finalize_incomplete_tasks(scenario)
            return
        deadline = time.perf_counter() + timeout
        while self.pending_tasks and time.perf_counter() < deadline:
            remaining: List[PendingTask] = []
            for task in list(self.pending_tasks):
                adapter = self.adapters.get(task.adapter_name)
                if adapter is None:
                    remaining.append(task)
                    continue
                result = await self._record_request(
                    adapter,
                    scenario,
                    "get_task",
                    lambda a=adapter, tid=task.task_id: a.get_task(task_id=tid),
                )
                payload = normalize_result_payload(result.result)
                status = extract_task_status(payload)
                if result.success and status in {"completed", "failed"}:
                    self.recorder.add_task(
                        build_task_event(
                            adapter=task.adapter_name,
                            task=task,
                            completion_scenario=scenario,
                            payload=payload,
                        )
                    )
                else:
                    remaining.append(task)
            self.pending_tasks = remaining
            if self.pending_tasks:
                await asyncio.sleep(self.config.task_poll_interval)
        if scenario == "final_drain":
            self._finalize_incomplete_tasks(scenario)

    def _finalize_incomplete_tasks(self, scenario: str) -> None:
        for task in self.pending_tasks:
            self.recorder.add_task(
                TaskEvent(
                    adapter=task.adapter_name,
                    task_id=task.task_id,
                    task_type=task.task_type,
                    origin_scenario=task.origin_scenario,
                    completion_scenario=scenario,
                    status="incomplete",
                    resource_id=task.resource_id,
                    local_duration_ms=(time.perf_counter() - task.local_started_monotonic) * 1000.0,
                    server_duration_ms=None,
                    error_message="task not completed before benchmark end",
                    result=None,
                    polled_at=utc_now(),
                )
            )
        self.pending_tasks = []

    def _write_outputs(self) -> None:
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_rows = build_request_summary_rows(
            events=self.recorder.request_events,
            phases=self.recorder.phases,
        )
        window_rows = build_request_window_rows(
            events=self.recorder.request_events,
            window_seconds=self.config.request_window_seconds,
        )
        task_rows = build_task_summary_rows(self.recorder.task_events)
        adapter_rows = build_adapter_comparison_rows(summary_rows)
        error_rows = build_error_rows(self.recorder.request_events)
        report = render_report_zh(
            config=self.config,
            run_id=self.run_id,
            notes=self.recorder.notes,
            phases=self.recorder.phases,
            summary_rows=summary_rows,
            task_rows=task_rows,
            adapter_rows=adapter_rows,
            error_rows=error_rows,
            output_dir=str(output_dir),
        )

        write_json(output_dir / "run_config.json", asdict(self.config))
        write_json(output_dir / "phases.json", [phase.to_dict() for phase in self.recorder.phases])
        write_json(
            output_dir / "run_summary.json",
            {
                "run_id": self.run_id,
                "created_at": utc_now(),
                "notes": self.recorder.notes,
                "request_summary": summary_rows,
                "task_summary": task_rows,
                "adapter_comparison": adapter_rows,
                "errors": error_rows,
                "summary_zh": report,
            },
        )
        write_text(output_dir / "summary_zh.md", report)
        write_jsonl(output_dir / "request_events.jsonl", self.recorder.request_events)
        write_jsonl(output_dir / "task_events.jsonl", self.recorder.task_events)
        write_csv(output_dir / "request_summary.csv", summary_rows)
        write_csv(output_dir / "request_windows.csv", window_rows)
        write_csv(output_dir / "task_summary.csv", task_rows)
        write_csv(output_dir / "adapter_comparison.csv", adapter_rows)
        write_csv(output_dir / "errors.csv", error_rows)

    def _print_summary_path(self) -> None:
        summary_path = Path(self.config.output_dir) / "summary_zh.md"
        print(f"\n压测报告: {summary_path}")


def parse_cli_process_result(returncode: int, stdout: str, stderr: str) -> AdapterResult:
    stdout = stdout.strip()
    stderr = stderr.strip()
    parsed: Any = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
    if returncode != 0:
        error_message = stderr or stdout or f"ov exited with code {returncode}"
        if parsed and isinstance(parsed, dict):
            error_message = extract_error_message(parsed) or error_message
        return AdapterResult(
            success=False,
            status_code=returncode,
            exception_type="CliExitError",
            error_message=truncate_error_message(error_message),
            result=parsed,
            raw_stdout=stdout,
            raw_stderr=stderr,
        )
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        return AdapterResult(
            success=False,
            status_code=returncode,
            exception_type="CliApiError",
            error_message=truncate_error_message(extract_error_message(parsed) or stderr),
            result=parsed.get("result"),
            raw_stdout=stdout,
            raw_stderr=stderr,
        )
    if isinstance(parsed, dict) and "result" in parsed and parsed.get("ok") is True:
        return AdapterResult(
            success=True,
            status_code=returncode,
            result=parsed.get("result"),
            raw_stdout=stdout,
            raw_stderr=stderr,
        )
    return AdapterResult(
        success=True,
        status_code=returncode,
        result=parsed if parsed is not None else stdout,
        raw_stdout=stdout,
        raw_stderr=stderr,
    )


def build_cli_config_payload(config: BenchmarkConfig) -> Dict[str, Any]:
    return {
        "url": config.server_url,
        "api_key": config.api_key,
        "account": config.account,
        "user": config.user,
        "agent_id": config.agent_id,
        "timeout": config.timeout,
        "extra_headers": None,
    }


def write_benchmark_document(path: Path, *, adapter: str, index: int, run_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""# OpenViking Load Benchmark Document {index}

Adapter: {adapter}
Run: {run_id}
Keywords: benchmark, concurrent retrieval, resource ingestion, session commit.

This generated document is used to test concurrent add_resource, find, search,
grep, glob, add_message, and commit traffic against a local OpenViking server.

The text intentionally repeats relevant terms so retrieval has stable targets:
OpenViking server benchmark benchmark benchmark. Session commit memory extraction.
Resource ingestion queue latency. Mixed SDK CLI subprocess load.
"""
    path.write_text(content, encoding="utf-8")


def build_message_content(*, session_id: str, message_index: int, adapter: str) -> str:
    return (
        f"[{adapter}] session={session_id} message={message_index}. "
        "The user discussed OpenViking server load testing, concurrent resource ingestion, "
        "retrieval latency, session commit background tasks, and mixed SDK/CLI traffic. "
        "Keep decisions, bottlenecks, follow-up actions, and observed queue behavior."
    )


def extract_session_ids(payload: Any) -> List[str]:
    if isinstance(payload, dict):
        for key in ("sessions", "items", "result"):
            if key in payload:
                return extract_session_ids(payload[key])
        value = payload.get("session_id") or payload.get("id")
        return [value] if isinstance(value, str) else []
    if isinstance(payload, list):
        ids: List[str] = []
        for item in payload:
            ids.extend(extract_session_ids(item))
        return ids
    return []


def iter_resource_tree_uris(root_uri: str) -> List[str]:
    prefix = "viking://resources"
    normalized = root_uri.rstrip("/")
    if not normalized.startswith(prefix):
        return [normalized]
    tail = normalized[len(prefix) :].strip("/")
    uris = [prefix]
    current = prefix
    for part in tail.split("/"):
        if not part:
            continue
        current = f"{current}/{part}"
        uris.append(current)
    return uris[1:]


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [to_jsonable(v) for v in value]
    return value


def normalize_result_payload(value: Any) -> Any:
    if isinstance(value, AdapterResult):
        return normalize_result_payload(value.result)
    if isinstance(value, dict) and value.get("ok") is True and "result" in value:
        return value["result"]
    return value


def extract_task_id_from_any(value: Any) -> Optional[str]:
    return extract_task_id(normalize_result_payload(value))


def extract_task_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        candidate = value.get("task_id")
        if isinstance(candidate, str) and candidate:
            return candidate
        for key in ("result", "task"):
            nested = extract_task_id(value.get(key))
            if nested:
                return nested
    return None


def extract_root_uri(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("root_uri", "uri", "resource_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def extract_task_status(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if isinstance(status, str):
        return status
    task = value.get("task")
    if isinstance(task, dict):
        status = task.get("status")
        if isinstance(status, str):
            return status
    return None


def build_task_event(
    *, adapter: str, task: PendingTask, completion_scenario: str, payload: Any
) -> TaskEvent:
    created_at = to_float(payload.get("created_at")) if isinstance(payload, dict) else None
    updated_at = to_float(payload.get("updated_at")) if isinstance(payload, dict) else None
    server_duration_ms = None
    if created_at is not None and updated_at is not None:
        server_duration_ms = max(updated_at - created_at, 0.0) * 1000.0
    status = extract_task_status(payload) or "unknown"
    error_message = payload.get("error") if isinstance(payload, dict) else None
    task_result = payload.get("result") if isinstance(payload, dict) else None
    return TaskEvent(
        adapter=adapter,
        task_id=task.task_id,
        task_type=task.task_type,
        origin_scenario=task.origin_scenario,
        completion_scenario=completion_scenario,
        status=status,
        resource_id=task.resource_id,
        local_duration_ms=(time.perf_counter() - task.local_started_monotonic) * 1000.0,
        server_duration_ms=server_duration_ms,
        error_message=truncate_error_message(str(error_message)) if error_message else None,
        result=task_result,
        polled_at=utc_now(),
    )


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def build_request_summary_rows(
    *, events: List[RequestEvent], phases: List[PhaseMetadata]
) -> List[Dict[str, Any]]:
    phase_durations = {
        (phase.adapter, phase.scenario): max(phase.duration_seconds, 1e-9) for phase in phases
    }
    groups: Dict[tuple[str, str, str], List[RequestEvent]] = {}
    for event in events:
        groups.setdefault((event.adapter, event.scenario, event.operation), []).append(event)
    rows: List[Dict[str, Any]] = []
    for (adapter, scenario, operation), group_events in sorted(groups.items()):
        duration = phase_durations.get((adapter, scenario)) or estimate_event_span_seconds(
            group_events
        )
        rows.append(build_summary_row(adapter, scenario, operation, group_events, duration))
    overall: Dict[tuple[str, str], List[RequestEvent]] = {}
    for event in events:
        overall.setdefault((event.adapter, event.operation), []).append(event)
    for (adapter, operation), group_events in sorted(overall.items()):
        rows.append(build_summary_row(adapter, "ALL", operation, group_events, total_span(events)))
    return rows


def build_summary_row(
    adapter: str,
    scenario: str,
    operation: str,
    events: List[RequestEvent],
    duration_seconds: float,
) -> Dict[str, Any]:
    latencies = [event.latency_ms for event in events]
    successes = sum(1 for event in events if event.success)
    failures = len(events) - successes
    status_counts: Dict[str, int] = {}
    for event in events:
        key = (
            str(event.status_code)
            if event.status_code is not None
            else (event.exception_type or "ok")
        )
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "adapter": adapter,
        "scenario": scenario,
        "operation": operation,
        "requests": len(events),
        "successes": successes,
        "failures": failures,
        "success_rate": round((successes / len(events)) * 100.0, 4) if events else 0.0,
        "qps": round(len(events) / max(duration_seconds, 1e-9), 4),
        "avg_ms": round_optional(sum(latencies) / len(latencies) if latencies else None),
        "p50_ms": round_optional(percentile(latencies, 50)),
        "p90_ms": round_optional(percentile(latencies, 90)),
        "p95_ms": round_optional(percentile(latencies, 95)),
        "p99_ms": round_optional(percentile(latencies, 99)),
        "max_ms": round_optional(max(latencies) if latencies else None),
        "slow_gt_1s": sum(1 for latency in latencies if latency > DEFAULT_SLOW_THRESHOLDS_MS[0]),
        "slow_gt_3s": sum(1 for latency in latencies if latency > DEFAULT_SLOW_THRESHOLDS_MS[1]),
        "slow_gt_5s": sum(1 for latency in latencies if latency > DEFAULT_SLOW_THRESHOLDS_MS[2]),
        "status_codes": json.dumps(status_counts, sort_keys=True),
    }


def build_request_window_rows(
    *, events: List[RequestEvent], window_seconds: float
) -> List[Dict[str, Any]]:
    groups: Dict[tuple[int, str, str, str], List[RequestEvent]] = {}
    for event in events:
        window_index = int((event.elapsed_ms_since_run_start / 1000.0) // window_seconds)
        groups.setdefault(
            (window_index, event.adapter, event.scenario, event.operation), []
        ).append(event)
    rows: List[Dict[str, Any]] = []
    for (window_index, adapter, scenario, operation), window_events in sorted(groups.items()):
        latencies = [event.latency_ms for event in window_events]
        successes = sum(1 for event in window_events if event.success)
        rows.append(
            {
                "window_index": window_index,
                "window_start_sec": round(window_index * window_seconds, 4),
                "window_end_sec": round((window_index + 1) * window_seconds, 4),
                "adapter": adapter,
                "scenario": scenario,
                "operation": operation,
                "requests": len(window_events),
                "successes": successes,
                "failures": len(window_events) - successes,
                "success_rate": round((successes / len(window_events)) * 100.0, 4),
                "qps": round(len(window_events) / max(window_seconds, 1e-9), 4),
                "p95_ms": round_optional(percentile(latencies, 95)),
                "p99_ms": round_optional(percentile(latencies, 99)),
                "max_ms": round_optional(max(latencies) if latencies else None),
            }
        )
    return rows


def build_task_summary_rows(events: List[TaskEvent]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str, str], List[TaskEvent]] = {}
    for event in events:
        groups.setdefault((event.adapter, event.task_type, event.status), []).append(event)
    rows: List[Dict[str, Any]] = []
    for (adapter, task_type, status), task_events in sorted(groups.items()):
        local_latencies = [event.local_duration_ms for event in task_events]
        server_latencies = [
            event.server_duration_ms
            for event in task_events
            if event.server_duration_ms is not None
        ]
        rows.append(
            {
                "adapter": adapter,
                "task_type": task_type,
                "status": status,
                "tasks": len(task_events),
                "p50_local_ms": round_optional(percentile(local_latencies, 50)),
                "p95_local_ms": round_optional(percentile(local_latencies, 95)),
                "p99_local_ms": round_optional(percentile(local_latencies, 99)),
                "max_local_ms": round_optional(max(local_latencies) if local_latencies else None),
                "p50_server_ms": round_optional(percentile(server_latencies, 50)),
                "p95_server_ms": round_optional(percentile(server_latencies, 95)),
                "p99_server_ms": round_optional(percentile(server_latencies, 99)),
                "max_server_ms": round_optional(
                    max(server_latencies) if server_latencies else None
                ),
            }
        )
    return rows


def build_adapter_comparison_rows(summary_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [
        row
        for row in summary_rows
        if row["scenario"] == "ALL"
        and row["operation"] in {"add_resource", "find", "search", "add_message", "commit_session"}
    ]
    return sorted(rows, key=lambda row: (row["operation"], row["adapter"]))


def build_error_rows(events: List[RequestEvent]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str, str, str], int] = {}
    for event in events:
        if event.success:
            continue
        key = (
            event.adapter,
            event.scenario,
            event.operation,
            event.exception_type or event.error_message or "unknown",
        )
        groups[key] = groups.get(key, 0) + 1
    return [
        {
            "adapter": adapter,
            "scenario": scenario,
            "operation": operation,
            "error": error,
            "count": count_value,
        }
        for (adapter, scenario, operation, error), count_value in sorted(
            groups.items(), key=lambda item: item[1], reverse=True
        )
    ]


def render_report_zh(
    *,
    config: BenchmarkConfig,
    run_id: str,
    notes: List[str],
    phases: List[PhaseMetadata],
    summary_rows: List[Dict[str, Any]],
    task_rows: List[Dict[str, Any]],
    adapter_rows: List[Dict[str, Any]],
    error_rows: List[Dict[str, Any]],
    output_dir: str,
) -> str:
    lines: List[str] = []
    lines.append("# OpenViking Server 压测报告")
    lines.append("")
    lines.append(f"- 运行 ID: `{run_id}`")
    lines.append(f"- Server: `{config.server_url}`")
    lines.append(f"- 调用路径: `{', '.join(config.adapters)}`")
    lines.append(f"- 测试数据根目录: `{config.data_root_uri}`")
    lines.append(f"- 输出目录: `{output_dir}`")
    if notes:
        lines.append("")
        lines.append("## 说明")
        for note in notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append("## 核心结论")
    total_requests = sum(row["requests"] for row in summary_rows if row["scenario"] != "ALL")
    total_failures = sum(row["failures"] for row in summary_rows if row["scenario"] != "ALL")
    success_rate = (
        (total_requests - total_failures) / total_requests * 100.0 if total_requests else 0.0
    )
    lines.append(
        f"- 本次共记录 {total_requests} 次请求，失败 {total_failures} 次，整体成功率 {success_rate:.2f}%。"
    )
    for adapter in config.adapters:
        mixed_find = find_summary(summary_rows, adapter=adapter, scenario="mixed", operation="find")
        retrieval_find = find_summary(
            summary_rows, adapter=adapter, scenario="retrieval", operation="find"
        )
        if mixed_find and retrieval_find:
            lines.append(
                "- "
                f"`{adapter}` find p95: retrieval {fmt_ms(retrieval_find['p95_ms'])} -> "
                f"mixed {fmt_ms(mixed_find['p95_ms'])}，变化 "
                f"{fmt_delta_percent(percent_change(retrieval_find['p95_ms'], mixed_find['p95_ms']))}。"
            )
    incomplete = [row for row in task_rows if row["status"] == "incomplete"]
    if incomplete:
        total_incomplete = sum(row["tasks"] for row in incomplete)
        lines.append(f"- 后台任务存在积压：{total_incomplete} 个任务在 drain 后仍未完成。")
    if error_rows:
        top = error_rows[0]
        lines.append(
            f"- Top 错误: `{top['adapter']}/{top['scenario']}/{top['operation']}` "
            f"`{top['error']}` 共 {top['count']} 次。"
        )
    lines.append("")
    lines.append("## 阶段耗时")
    lines.append(markdown_table([phase.to_dict() for phase in phases]))
    lines.append("")
    lines.append("## 请求汇总")
    lines.append(
        markdown_table(
            [
                pick(
                    row,
                    [
                        "adapter",
                        "scenario",
                        "operation",
                        "requests",
                        "success_rate",
                        "qps",
                        "p50_ms",
                        "p95_ms",
                        "p99_ms",
                        "max_ms",
                    ],
                )
                for row in summary_rows
                if row["scenario"] != "ALL"
            ]
        )
    )
    lines.append("")
    lines.append("## SDK/CLI 对比")
    lines.append(
        markdown_table(
            [
                pick(
                    row,
                    ["adapter", "operation", "requests", "success_rate", "qps", "p95_ms", "p99_ms"],
                )
                for row in adapter_rows
            ]
        )
    )
    lines.append("")
    lines.append("## 后台任务")
    lines.append(markdown_table(task_rows) if task_rows else "无后台任务记录。")
    lines.append("")
    lines.append("## 错误 Top")
    lines.append(markdown_table(error_rows[:20]) if error_rows else "无请求错误。")
    lines.append("")
    lines.append("## 输出文件")
    lines.append("- `run_summary.json`: 汇总数据")
    lines.append("- `request_events.jsonl`: 每次请求明细")
    lines.append("- `task_events.jsonl`: 后台任务明细")
    lines.append("- `request_summary.csv`: 请求聚合")
    lines.append("- `request_windows.csv`: 时间窗口聚合")
    lines.append("- `adapter_comparison.csv`: SDK/CLI 对比")
    return "\n".join(lines) + "\n"


def markdown_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "_无数据_"
    columns = list(rows[0].keys())
    output = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(format_cell(row.get(col)) for col in columns) + " |")
    return "\n".join(output)


def pick(row: Dict[str, Any], columns: List[str]) -> Dict[str, Any]:
    return {column: row.get(column) for column in columns}


def format_cell(value: Any) -> str:
    if value is None:
        return "n/a"
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def find_summary(
    rows: List[Dict[str, Any]], *, adapter: str, scenario: str, operation: str
) -> Optional[Dict[str, Any]]:
    return next(
        (
            row
            for row in rows
            if row["adapter"] == adapter
            and row["scenario"] == scenario
            and row["operation"] == operation
        ),
        None,
    )


def estimate_event_span_seconds(events: List[RequestEvent]) -> float:
    if len(events) < 2:
        return 1e-9
    starts = [event.elapsed_ms_since_run_start for event in events]
    ends = [event.elapsed_ms_since_run_start + event.latency_ms for event in events]
    return max((max(ends) - min(starts)) / 1000.0, 1e-9)


def total_span(events: List[RequestEvent]) -> float:
    if not events:
        return 1e-9
    starts = [event.elapsed_ms_since_run_start for event in events]
    ends = [event.elapsed_ms_since_run_start + event.latency_ms for event in events]
    return max((max(ends) - min(starts)) / 1000.0, 1e-9)


def percent_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None or old == 0:
        return None
    return ((new - old) / old) * 100.0


def fmt_delta_percent(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def fmt_ms(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.2f}ms"


def round_optional(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def truncate_error_message(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    if len(message) <= MAX_ERROR_MESSAGE_LEN:
        return message
    return message[:MAX_ERROR_MESSAGE_LEN] + "...[truncated]"


def extract_error_message(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("code")
        return str(message) if message is not None else None
    if error:
        return str(error)
    return None


def is_not_found_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "not_found" in text or "not found" in text


def is_already_exists_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "already_exists" in text or "already exists" in text or "exist" in text


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def write_jsonl(path: Path, values: Iterable[Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            payload = value.to_dict() if hasattr(value, "to_dict") else value
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: Optional[List[str]] = None) -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Run SDK/CLI mixed load benchmark against a running OpenViking server."
    )
    parser.add_argument(
        "--server-url",
        default=os.getenv("OPENVIKING_SERVER_URL", "http://127.0.0.1:1935"),
    )
    parser.add_argument("--api-key", default=os.getenv("OPENVIKING_API_KEY", "test-root-api-key"))
    parser.add_argument("--account", default=os.getenv("OPENVIKING_ACCOUNT", "default"))
    parser.add_argument("--user", default=os.getenv("OPENVIKING_USER", "default"))
    parser.add_argument("--agent-id", default=os.getenv("OPENVIKING_AGENT_ID"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--adapters",
        default="sdk,cli-http,cli-subprocess",
        help="Comma separated: sdk,cli-http,cli-subprocess",
    )
    parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="standard")
    parser.add_argument("--resource-count", type=int)
    parser.add_argument("--session-count", type=int)
    parser.add_argument("--messages-per-session", type=int)
    parser.add_argument("--resource-concurrency", type=int)
    parser.add_argument("--search-concurrency", type=int)
    parser.add_argument("--session-concurrency", type=int)
    parser.add_argument("--commit-concurrency", type=int)
    parser.add_argument("--mixed-concurrency", type=int)
    parser.add_argument("--phase-seconds", type=float)
    parser.add_argument("--mixed-seconds", type=float)
    parser.add_argument("--warmup-seconds", type=float)
    parser.add_argument("--drain-timeout", type=float, default=60.0)
    parser.add_argument("--task-poll-interval", type=float, default=1.0)
    parser.add_argument("--request-window-seconds", type=float, default=5.0)
    parser.add_argument("--data-root-uri", default=DEFAULT_DATA_ROOT_URI)
    default_results_root = (
        Path(__file__).resolve().parents[1] / "results" / "openviking_server_load"
    )
    parser.add_argument("--local-data-dir", default=str(default_results_root / "data"))
    parser.add_argument(
        "--output-dir",
        default=str(default_results_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")),
    )
    parser.add_argument("--clear-before-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--cleanup-at-end", action="store_true")
    parser.add_argument("--ov-bin", default=os.getenv("OV_BIN", "ov"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--find-limit", type=int, default=10)
    args = parser.parse_args(argv)

    profile = PROFILE_DEFAULTS[args.profile]

    def get_int(name: str) -> int:
        return max(
            0, int(getattr(args, name) if getattr(args, name) is not None else profile[name])
        )

    def get_float(name: str) -> float:
        return max(
            0.0, float(getattr(args, name) if getattr(args, name) is not None else profile[name])
        )

    adapters = [item.strip() for item in args.adapters.split(",") if item.strip()]
    allowed = {"sdk", "cli-http", "cli-subprocess"}
    invalid = sorted(set(adapters) - allowed)
    if invalid:
        parser.error(f"Unsupported adapters: {', '.join(invalid)}")
    if not adapters:
        parser.error("--adapters must not be empty")

    return BenchmarkConfig(
        server_url=args.server_url.rstrip("/"),
        api_key=args.api_key or None,
        account=args.account or None,
        user=args.user or None,
        agent_id=args.agent_id or None,
        timeout=max(0.1, args.timeout),
        adapters=adapters,
        profile=args.profile,
        resource_count=get_int("resource_count"),
        session_count=max(1, get_int("session_count")),
        messages_per_session=max(1, get_int("messages_per_session")),
        resource_concurrency=max(1, get_int("resource_concurrency")),
        search_concurrency=max(1, get_int("search_concurrency")),
        session_concurrency=max(1, get_int("session_concurrency")),
        commit_concurrency=max(1, get_int("commit_concurrency")),
        mixed_concurrency=max(1, get_int("mixed_concurrency")),
        phase_seconds=get_float("phase_seconds"),
        mixed_seconds=get_float("mixed_seconds"),
        warmup_seconds=get_float("warmup_seconds"),
        drain_timeout=max(0.0, args.drain_timeout),
        task_poll_interval=max(0.1, args.task_poll_interval),
        request_window_seconds=max(1.0, args.request_window_seconds),
        local_data_dir=args.local_data_dir,
        output_dir=args.output_dir,
        data_root_uri=args.data_root_uri.rstrip("/"),
        session_prefix=DEFAULT_SESSION_PREFIX,
        clear_before_run=bool(args.clear_before_run),
        cleanup_at_end=bool(args.cleanup_at_end),
        ov_bin=args.ov_bin,
        seed=args.seed,
        find_limit=max(1, args.find_limit),
    )


async def async_main(argv: Optional[List[str]] = None) -> int:
    config = parse_args(argv)
    runner = BenchmarkRunner(config)
    return await runner.run()


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
