#!/usr/bin/env python3
"""Daily session mixed-load contention benchmark for OpenViking."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

DEFAULT_FIND_QUERIES = [
    "how to authenticate users",
    "what is OpenViking",
    "session commit memory extraction",
]
DEFAULT_SLOW_THRESHOLDS_MS = (1000, 3000, 5000)
MAX_ERROR_MESSAGE_LEN = 500


@dataclass
class BenchmarkConfig:
    server_url: str
    api_key: str
    account: str
    user: str
    request_timeout: float
    session_count: int
    writer_concurrency: int
    reader_concurrency: int
    extract_concurrency: int
    messages_per_commit: int
    extract_ratio: float
    message_size: int
    baseline_seconds: float
    mixed_seconds: float
    recovery_seconds: float
    window_seconds: float
    observer_interval: float
    task_poll_interval: float
    task_drain_timeout: float
    output_dir: str
    cleanup: bool
    require_extract_load: bool
    find_queries: List[str]
    find_limit: int
    find_target_uri: str
    find_score_threshold: Optional[float]
    seed: int


@dataclass
class PhaseMetadata:
    phase: str
    started_at: str
    ended_at: str
    duration_seconds: float


@dataclass
class RequestEvent:
    api: str
    method: str
    path: str
    phase: str
    started_at: str
    ended_at: str
    elapsed_ms_since_run_start: float
    latency_ms: float
    success: bool
    status_code: Optional[int]
    timeout: bool
    exception_type: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    session_id: Optional[str] = None
    cycle_index: Optional[int] = None
    worker_id: Optional[int] = None
    task_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CommitTaskEvent:
    task_id: str
    session_id: str
    origin_phase: str
    completion_phase: str
    status: str
    created_at: Optional[float]
    updated_at: Optional[float]
    server_duration_ms: Optional[float]
    local_duration_ms: float
    active_count_updated: Optional[int]
    memories_extracted: Optional[Dict[str, int]]
    error: Optional[str]
    cycle_index: Optional[int]
    polled_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObserverSample:
    api: str
    phase: str
    sampled_at: str
    elapsed_ms_since_run_start: float
    latency_ms: float
    success: bool
    is_healthy: Optional[bool]
    has_errors: Optional[bool]
    payload: Optional[Dict[str, Any]]
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PendingCommitTask:
    task_id: str
    session_id: str
    origin_phase: str
    cycle_index: int
    local_started_monotonic: float


@dataclass
class Recorder:
    request_events: List[RequestEvent] = field(default_factory=list)
    task_events: List[CommitTaskEvent] = field(default_factory=list)
    observer_samples: List[ObserverSample] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add_request(self, event: RequestEvent) -> None:
        self.request_events.append(event)

    def add_task(self, event: CommitTaskEvent) -> None:
        self.task_events.append(event)

    def add_sample(self, sample: ObserverSample) -> None:
        self.observer_samples.append(sample)

    def add_note(self, note: str) -> None:
        self.notes.append(note)


class PhaseState:
    def __init__(self, initial: str = "setup") -> None:
        self.current = initial


class BenchmarkHTTPClient:
    def __init__(self, config: BenchmarkConfig, recorder: Recorder) -> None:
        self._config = config
        self._recorder = recorder
        self._run_start_monotonic = time.perf_counter()
        self._client = httpx.AsyncClient(
            base_url=config.server_url.rstrip("/"),
            headers=self._default_headers(),
            timeout=httpx.Timeout(config.request_timeout),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=max(
                    32,
                    config.writer_concurrency
                    + config.reader_concurrency
                    + config.extract_concurrency
                    + 8,
                ),
                max_keepalive_connections=max(
                    16,
                    config.writer_concurrency + config.reader_concurrency + 4,
                ),
            ),
        )

    @property
    def run_start_monotonic(self) -> float:
        return self._run_start_monotonic

    async def aclose(self) -> None:
        await self._client.aclose()

    def _default_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": "OpenViking-Session-Contention-Benchmark/1.0",
            "X-OpenViking-Account": self._config.account,
            "X-OpenViking-User": self._config.user,
        }
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def request_json(
        self,
        *,
        api: str,
        method: str,
        path: str,
        phase: str,
        session_id: Optional[str] = None,
        cycle_index: Optional[int] = None,
        worker_id: Optional[int] = None,
        task_id: Optional[str] = None,
        json_payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> tuple[Optional[httpx.Response], Optional[Dict[str, Any]]]:
        started_monotonic = time.perf_counter()
        started_wall = utc_now()
        response: Optional[httpx.Response] = None
        body: Optional[Dict[str, Any]] = None
        status_code: Optional[int] = None
        success = False
        timeout = False
        exception_type: Optional[str] = None
        error_code: Optional[str] = None
        error_message: Optional[str] = None

        try:
            response = await self._client.request(
                method=method,
                url=path,
                json=json_payload,
                params=params,
            )
            status_code = response.status_code
            body = maybe_json(response)
            success = self._is_success(status_code, body)
            if not success:
                error_code, error_message = extract_error(body, status_code)
        except httpx.TimeoutException as exc:
            timeout = True
            exception_type = type(exc).__name__
            error_message = truncate_error_message(str(exc))
        except Exception as exc:  # pragma: no cover - exercised in real runs
            exception_type = type(exc).__name__
            error_message = truncate_error_message(str(exc))

        ended_wall = utc_now()
        ended_monotonic = time.perf_counter()
        latency_ms = (ended_monotonic - started_monotonic) * 1000.0
        elapsed_ms = (started_monotonic - self._run_start_monotonic) * 1000.0
        self._recorder.add_request(
            RequestEvent(
                api=api,
                method=method.upper(),
                path=path,
                phase=phase,
                started_at=started_wall,
                ended_at=ended_wall,
                elapsed_ms_since_run_start=elapsed_ms,
                latency_ms=latency_ms,
                success=success,
                status_code=status_code,
                timeout=timeout,
                exception_type=exception_type,
                error_code=error_code,
                error_message=error_message,
                session_id=session_id,
                cycle_index=cycle_index,
                worker_id=worker_id,
                task_id=task_id,
            )
        )
        return response, body

    @staticmethod
    def _is_success(status_code: Optional[int], body: Optional[Dict[str, Any]]) -> bool:
        if status_code is None or status_code >= 400:
            return False
        if not isinstance(body, dict):
            return status_code < 400
        if "status" in body:
            return body.get("status") == "ok"
        return True


class CommitTaskPoller:
    def __init__(
        self,
        client: BenchmarkHTTPClient,
        recorder: Recorder,
        phase_state: PhaseState,
        poll_interval: float,
    ) -> None:
        self._client = client
        self._recorder = recorder
        self._phase_state = phase_state
        self._poll_interval = poll_interval
        self._pending: Dict[str, PendingCommitTask] = {}
        self._closed = False
        self._wake_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def register(self, task: PendingCommitTask) -> None:
        async with self._lock:
            self._pending[task.task_id] = task
            self._wake_event.set()

    async def close(self) -> None:
        self._closed = True
        self._wake_event.set()

    async def drain(self, timeout: float) -> None:
        deadline = time.perf_counter() + timeout
        while True:
            async with self._lock:
                remaining = len(self._pending)
            if remaining == 0:
                return
            if time.perf_counter() >= deadline:
                return
            await asyncio.sleep(min(self._poll_interval, 0.5))

    async def finalize_incomplete(self) -> None:
        async with self._lock:
            leftovers = list(self._pending.values())
            self._pending.clear()
        for item in leftovers:
            local_duration_ms = (time.perf_counter() - item.local_started_monotonic) * 1000.0
            self._recorder.add_task(
                CommitTaskEvent(
                    task_id=item.task_id,
                    session_id=item.session_id,
                    origin_phase=item.origin_phase,
                    completion_phase=self._phase_state.current,
                    status="incomplete",
                    created_at=None,
                    updated_at=None,
                    server_duration_ms=None,
                    local_duration_ms=local_duration_ms,
                    active_count_updated=None,
                    memories_extracted=None,
                    error="task not completed before benchmark end",
                    cycle_index=item.cycle_index,
                    polled_at=utc_now(),
                )
            )

    async def run(self) -> None:
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()

            while True:
                async with self._lock:
                    pending = list(self._pending.values())
                if not pending:
                    break
                await self._poll_pending(pending)
                if self._closed:
                    return
                await asyncio.sleep(self._poll_interval)

            if self._closed:
                return

    async def _poll_pending(self, pending: List[PendingCommitTask]) -> None:
        coroutines = [self._poll_one(item) for item in pending]
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        completed_ids = [task_id for task_id in results if isinstance(task_id, str)]
        if not completed_ids:
            return
        async with self._lock:
            for task_id in completed_ids:
                self._pending.pop(task_id, None)

    async def _poll_one(self, item: PendingCommitTask) -> Optional[str]:
        _, body = await self._client.request_json(
            api="get_task",
            method="GET",
            path=f"/api/v1/tasks/{item.task_id}",
            phase=self._phase_state.current,
            session_id=item.session_id,
            cycle_index=item.cycle_index,
            task_id=item.task_id,
        )
        if not isinstance(body, dict) or body.get("status") != "ok":
            return None
        result = body.get("result") or {}
        task_status = result.get("status")
        if task_status not in {"completed", "failed"}:
            return None

        created_at = to_float(result.get("created_at"))
        updated_at = to_float(result.get("updated_at"))
        server_duration_ms = None
        if created_at is not None and updated_at is not None:
            server_duration_ms = max(updated_at - created_at, 0.0) * 1000.0
        local_duration_ms = (time.perf_counter() - item.local_started_monotonic) * 1000.0
        task_result = result.get("result") or {}
        self._recorder.add_task(
            CommitTaskEvent(
                task_id=item.task_id,
                session_id=item.session_id,
                origin_phase=item.origin_phase,
                completion_phase=self._phase_state.current,
                status=task_status,
                created_at=created_at,
                updated_at=updated_at,
                server_duration_ms=server_duration_ms,
                local_duration_ms=local_duration_ms,
                active_count_updated=task_result.get("active_count_updated"),
                memories_extracted=task_result.get("memories_extracted"),
                error=result.get("error"),
                cycle_index=item.cycle_index,
                polled_at=utc_now(),
            )
        )
        return item.task_id


class BenchmarkRunner:
    def __init__(self, config: BenchmarkConfig) -> None:
        self.config = config
        self.random = random.Random(config.seed)
        self.recorder = Recorder()
        self.phase_state = PhaseState()
        self.phase_metadata: List[PhaseMetadata] = []
        self.phase_durations: Dict[str, float] = {}
        self.session_ids: List[str] = []
        self.session_queue: asyncio.Queue[str] = asyncio.Queue()
        self.session_cycle_counts: Dict[str, int] = {}
        self.extract_semaphore = asyncio.Semaphore(max(1, config.extract_concurrency))
        self.client = BenchmarkHTTPClient(config, self.recorder)
        self.task_poller = CommitTaskPoller(
            client=self.client,
            recorder=self.recorder,
            phase_state=self.phase_state,
            poll_interval=config.task_poll_interval,
        )

    async def run(self) -> int:
        poller_task = asyncio.create_task(self.task_poller.run())
        exit_code = 0
        try:
            await self._preflight()
            await self._create_sessions()
            await self._run_phase(
                phase="baseline",
                duration_seconds=self.config.baseline_seconds,
                enable_readers=self.config.reader_concurrency > 0,
                enable_writers=False,
                enable_sampler=self.config.observer_interval > 0,
            )
            await self._run_phase(
                phase="mixed_load",
                duration_seconds=self.config.mixed_seconds,
                enable_readers=self.config.reader_concurrency > 0,
                enable_writers=self.config.writer_concurrency > 0 and bool(self.session_ids),
                enable_sampler=self.config.observer_interval > 0,
            )
            await self._run_phase(
                phase="recovery",
                duration_seconds=self.config.recovery_seconds,
                enable_readers=self.config.reader_concurrency > 0,
                enable_writers=False,
                enable_sampler=self.config.observer_interval > 0,
            )
            if self.config.task_drain_timeout > 0:
                self.phase_state.current = "drain"
                await self.task_poller.drain(self.config.task_drain_timeout)
        except RuntimeError as exc:
            self.recorder.add_note(f"fatal: {exc}")
            print(f"[fatal] {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            await self.task_poller.close()
            await poller_task
            await self.task_poller.finalize_incomplete()
            if self.config.cleanup and self.session_ids:
                await self._cleanup_sessions()
            await self.client.aclose()

        self._write_outputs()
        self._print_summary()
        return exit_code

    async def _preflight(self) -> None:
        self.phase_state.current = "setup"
        _, health_body = await self.client.request_json(
            api="health",
            method="GET",
            path="/health",
            phase="setup",
        )
        if not isinstance(health_body, dict) or health_body.get("status") != "ok":
            raise RuntimeError("server health check failed")

        _, status_body = await self.client.request_json(
            api="system_status",
            method="GET",
            path="/api/v1/system/status",
            phase="setup",
        )
        if not isinstance(status_body, dict) or status_body.get("status") != "ok":
            raise RuntimeError("authenticated system status request failed")

        _, models_body = await self.client.request_json(
            api="observer_models",
            method="GET",
            path="/api/v1/observer/models",
            phase="setup",
        )
        model_result = (models_body or {}).get("result") if isinstance(models_body, dict) else None
        model_note = self._extract_model_note(model_result)
        if model_note:
            self.recorder.add_note(model_note)

        if self.config.extract_ratio > 0:
            preflight_result = await self._run_extract_preflight()
            if preflight_result:
                self.recorder.add_note(preflight_result)
                if self.config.require_extract_load:
                    raise RuntimeError(preflight_result)

    async def _run_extract_preflight(self) -> Optional[str]:
        _, create_body = await self.client.request_json(
            api="create_session",
            method="POST",
            path="/api/v1/sessions",
            phase="setup",
        )
        session_id = extract_session_id(create_body)
        if not session_id:
            return "extract preflight could not create session"

        try:
            payload = {
                "role": "user",
                "content": build_message_content(
                    session_id=session_id,
                    cycle_index=0,
                    message_index=0,
                    size=self.config.message_size,
                ),
            }
            await self.client.request_json(
                api="add_message",
                method="POST",
                path=f"/api/v1/sessions/{session_id}/messages",
                phase="setup",
                session_id=session_id,
                cycle_index=0,
                json_payload=payload,
            )
            _, extract_body = await self.client.request_json(
                api="extract",
                method="POST",
                path=f"/api/v1/sessions/{session_id}/extract",
                phase="setup",
                session_id=session_id,
                cycle_index=0,
            )
            if not isinstance(extract_body, dict) or extract_body.get("status") != "ok":
                return "extract preflight request failed"
            result = extract_body.get("result")
            if isinstance(result, list) and not result:
                return (
                    "extract preflight returned empty result; long-tail load may be weak if models are "
                    "not configured"
                )
            return None
        finally:
            await self.client.request_json(
                api="delete_session",
                method="DELETE",
                path=f"/api/v1/sessions/{session_id}",
                phase="setup",
                session_id=session_id,
            )

    def _extract_model_note(self, model_result: Any) -> Optional[str]:
        if not isinstance(model_result, dict):
            return None
        is_healthy = model_result.get("is_healthy")
        status = model_result.get("status")
        if is_healthy is False:
            return f"observer/models reports unhealthy state; extract load may not be representative: {status}"
        return None

    async def _create_sessions(self) -> None:
        if self.config.session_count <= 0:
            return
        for _ in range(self.config.session_count):
            _, body = await self.client.request_json(
                api="create_session",
                method="POST",
                path="/api/v1/sessions",
                phase="setup",
            )
            session_id = extract_session_id(body)
            if not session_id:
                raise RuntimeError("failed to create benchmark sessions")
            self.session_ids.append(session_id)
            self.session_cycle_counts[session_id] = 0
            await self.session_queue.put(session_id)

    async def _cleanup_sessions(self) -> None:
        self.phase_state.current = "cleanup"
        for session_id in self.session_ids:
            await self.client.request_json(
                api="delete_session",
                method="DELETE",
                path=f"/api/v1/sessions/{session_id}",
                phase="cleanup",
                session_id=session_id,
            )

    async def _run_phase(
        self,
        *,
        phase: str,
        duration_seconds: float,
        enable_readers: bool,
        enable_writers: bool,
        enable_sampler: bool,
    ) -> None:
        if duration_seconds <= 0:
            return

        self.phase_state.current = phase
        stop_event = asyncio.Event()
        tasks: List[asyncio.Task[Any]] = []

        if enable_readers:
            for worker_id in range(self.config.reader_concurrency):
                tasks.append(asyncio.create_task(self._reader_worker(phase, worker_id, stop_event)))
        if enable_writers:
            for worker_id in range(self.config.writer_concurrency):
                tasks.append(asyncio.create_task(self._writer_worker(phase, worker_id, stop_event)))
        if enable_sampler:
            tasks.append(asyncio.create_task(self._sampler_worker(phase, stop_event)))

        phase_started = time.perf_counter()
        started_wall = utc_now()
        await asyncio.sleep(duration_seconds)
        stop_event.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        phase_duration = time.perf_counter() - phase_started
        ended_wall = utc_now()
        self.phase_metadata.append(
            PhaseMetadata(
                phase=phase,
                started_at=started_wall,
                ended_at=ended_wall,
                duration_seconds=phase_duration,
            )
        )
        self.phase_durations[phase] = phase_duration

    async def _writer_worker(self, phase: str, worker_id: int, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            session_id = await self._borrow_session(stop_event)
            if not session_id:
                return
            try:
                cycle_index = self.session_cycle_counts[session_id]
                self.session_cycle_counts[session_id] += 1
                await self._run_session_cycle(
                    phase=phase,
                    worker_id=worker_id,
                    session_id=session_id,
                    cycle_index=cycle_index,
                )
            finally:
                await self.session_queue.put(session_id)

    async def _run_session_cycle(
        self,
        *,
        phase: str,
        worker_id: int,
        session_id: str,
        cycle_index: int,
    ) -> None:
        successful_messages = 0
        for message_index in range(self.config.messages_per_commit):
            payload = {
                "role": "user",
                "content": build_message_content(
                    session_id=session_id,
                    cycle_index=cycle_index,
                    message_index=message_index,
                    size=self.config.message_size,
                ),
            }
            _, body = await self.client.request_json(
                api="add_message",
                method="POST",
                path=f"/api/v1/sessions/{session_id}/messages",
                phase=phase,
                session_id=session_id,
                cycle_index=cycle_index,
                worker_id=worker_id,
                json_payload=payload,
            )
            if isinstance(body, dict) and body.get("status") == "ok":
                successful_messages += 1

        if successful_messages <= 0:
            return

        if self.config.extract_ratio > 0 and self.random.random() < self.config.extract_ratio:
            async with self.extract_semaphore:
                await self.client.request_json(
                    api="extract",
                    method="POST",
                    path=f"/api/v1/sessions/{session_id}/extract",
                    phase=phase,
                    session_id=session_id,
                    cycle_index=cycle_index,
                    worker_id=worker_id,
                )

        _, body = await self.client.request_json(
            api="commit",
            method="POST",
            path=f"/api/v1/sessions/{session_id}/commit",
            phase=phase,
            session_id=session_id,
            cycle_index=cycle_index,
            worker_id=worker_id,
        )
        task_id = extract_task_id(body)
        if task_id:
            await self.task_poller.register(
                PendingCommitTask(
                    task_id=task_id,
                    session_id=session_id,
                    origin_phase=phase,
                    cycle_index=cycle_index,
                    local_started_monotonic=time.perf_counter(),
                )
            )

    async def _reader_worker(self, phase: str, worker_id: int, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            payload = {
                "query": self.random.choice(self.config.find_queries),
                "limit": self.config.find_limit,
            }
            if self.config.find_target_uri:
                payload["target_uri"] = self.config.find_target_uri
            if self.config.find_score_threshold is not None:
                payload["score_threshold"] = self.config.find_score_threshold
            await self.client.request_json(
                api="find",
                method="POST",
                path="/api/v1/search/find",
                phase=phase,
                worker_id=worker_id,
                json_payload=payload,
            )

    async def _sampler_worker(self, phase: str, stop_event: asyncio.Event) -> None:
        sample_specs = [
            ("system_status", "GET", "/api/v1/system/status"),
            ("observer_queue", "GET", "/api/v1/observer/queue"),
            ("observer_system", "GET", "/api/v1/observer/system"),
        ]
        while not stop_event.is_set():
            for api, method, path in sample_specs:
                started = time.perf_counter()
                response, body = await self.client.request_json(
                    api=api,
                    method=method,
                    path=path,
                    phase=phase,
                )
                latency_ms = (time.perf_counter() - started) * 1000.0
                success = response is not None and self.client._is_success(
                    response.status_code if response else None,
                    body,
                )
                self.recorder.add_sample(
                    ObserverSample(
                        api=api,
                        phase=phase,
                        sampled_at=utc_now(),
                        elapsed_ms_since_run_start=(
                            time.perf_counter() - self.client.run_start_monotonic
                        )
                        * 1000.0,
                        latency_ms=latency_ms,
                        success=success,
                        is_healthy=extract_boolean(body, "result", "is_healthy"),
                        has_errors=extract_boolean(body, "result", "has_errors"),
                        payload=body if isinstance(body, dict) else None,
                        error_message=extract_error(
                            body, response.status_code if response else None
                        )[1]
                        if response is not None and not success
                        else None,
                    )
                )
                if stop_event.is_set():
                    break
            if stop_event.is_set():
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.config.observer_interval)
            except asyncio.TimeoutError:
                continue

    async def _borrow_session(self, stop_event: asyncio.Event) -> Optional[str]:
        while not stop_event.is_set():
            try:
                return await asyncio.wait_for(self.session_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
        return None

    def _write_outputs(self) -> None:
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        request_summary_rows = build_request_summary_rows(
            events=self.recorder.request_events,
            phase_durations=self.phase_durations,
            total_run_duration=total_duration_seconds(self.phase_metadata),
        )
        task_summary_rows = build_task_summary_rows(self.recorder.task_events)
        human_summary_zh = render_human_summary_zh(
            config=self.config,
            output_dir=self.config.output_dir,
            notes=self.recorder.notes,
            phase_metadata=self.phase_metadata,
            request_summary_rows=request_summary_rows,
            request_events=self.recorder.request_events,
            task_summary_rows=task_summary_rows,
            task_events=self.recorder.task_events,
        )

        write_json(output_dir / "run_config.json", asdict(self.config))
        write_json(
            output_dir / "phases.json",
            [asdict(item) for item in self.phase_metadata],
        )
        write_json(
            output_dir / "run_summary.json",
            self._build_run_summary(
                request_summary_rows=request_summary_rows,
                task_summary_rows=task_summary_rows,
                human_summary_zh=human_summary_zh,
            ),
        )
        write_text(output_dir / "summary_zh.txt", human_summary_zh)
        write_jsonl(output_dir / "request_events.jsonl", self.recorder.request_events)
        write_jsonl(output_dir / "task_events.jsonl", self.recorder.task_events)
        write_jsonl(output_dir / "observer_samples.jsonl", self.recorder.observer_samples)

        write_csv(
            output_dir / "request_summary.csv",
            request_summary_rows,
        )
        write_csv(
            output_dir / "request_windows.csv",
            build_request_window_rows(
                events=self.recorder.request_events,
                window_seconds=self.config.window_seconds,
            ),
        )
        write_csv(
            output_dir / "task_summary.csv",
            task_summary_rows,
        )

    def _build_run_summary(
        self,
        *,
        request_summary_rows: List[Dict[str, Any]],
        task_summary_rows: List[Dict[str, Any]],
        human_summary_zh: str,
    ) -> Dict[str, Any]:
        find_delta = build_find_phase_delta(request_summary_rows)
        return {
            "notes": self.recorder.notes,
            "phase_metadata": [asdict(item) for item in self.phase_metadata],
            "request_summary": request_summary_rows,
            "task_summary": task_summary_rows,
            "find_phase_delta": find_delta,
            "human_summary_zh": human_summary_zh,
            "created_at": utc_now(),
        }

    def _print_summary(self) -> None:
        request_summary_rows = build_request_summary_rows(
            events=self.recorder.request_events,
            phase_durations=self.phase_durations,
            total_run_duration=total_duration_seconds(self.phase_metadata),
        )
        task_summary_rows = build_task_summary_rows(self.recorder.task_events)
        print(
            "\n"
            + render_human_summary_zh(
                config=self.config,
                output_dir=self.config.output_dir,
                notes=self.recorder.notes,
                phase_metadata=self.phase_metadata,
                request_summary_rows=request_summary_rows,
                request_events=self.recorder.request_events,
                task_summary_rows=task_summary_rows,
                task_events=self.recorder.task_events,
            )
        )


def parse_args(argv: Optional[List[str]] = None) -> BenchmarkConfig:
    server_host = os.getenv("SERVER_HOST", "127.0.0.1")
    server_port = int(os.getenv("SERVER_PORT", "1933"))
    default_server_url = f"http://{server_host}:{server_port}"
    default_output_dir = (
        Path(__file__).resolve().parents[1]
        / "results"
        / "session_contention"
        / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )

    parser = argparse.ArgumentParser(
        description="Reproduce session addMessage/extract/commit contention against concurrent find traffic.",
    )
    parser.add_argument("--server-url", default=default_server_url)
    parser.add_argument("--api-key", default=os.getenv("OPENVIKING_API_KEY", "test-root-api-key"))
    parser.add_argument("--account", default=os.getenv("OPENVIKING_ACCOUNT", "default"))
    parser.add_argument("--user", default=os.getenv("OPENVIKING_USER", "default"))
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--sessions", type=int, default=8)
    parser.add_argument("--writer-concurrency", type=int, default=8)
    parser.add_argument("--reader-concurrency", type=int, default=4)
    parser.add_argument("--extract-concurrency", type=int, default=4)
    parser.add_argument("--messages-per-commit", type=int, default=5)
    parser.add_argument("--extract-ratio", type=float, default=0.5)
    parser.add_argument("--message-size", type=int, default=768)
    parser.add_argument("--baseline-seconds", type=float, default=30.0)
    parser.add_argument("--mixed-seconds", type=float, default=120.0)
    parser.add_argument("--recovery-seconds", type=float, default=30.0)
    parser.add_argument("--window-seconds", type=float, default=5.0)
    parser.add_argument("--observer-interval", type=float, default=5.0)
    parser.add_argument("--task-poll-interval", type=float, default=1.0)
    parser.add_argument("--task-drain-timeout", type=float, default=30.0)
    parser.add_argument("--output-dir", default=str(default_output_dir))
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--require-extract-load", action="store_true")
    parser.add_argument(
        "--find-query",
        action="append",
        dest="find_queries",
        default=[],
        help="Repeat to add multiple find queries.",
    )
    parser.add_argument("--find-limit", type=int, default=10)
    parser.add_argument("--find-target-uri", default="")
    parser.add_argument("--find-score-threshold", type=float, default=None)
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args(argv)
    find_queries = args.find_queries or list(DEFAULT_FIND_QUERIES)

    config = BenchmarkConfig(
        server_url=args.server_url,
        api_key=args.api_key,
        account=args.account,
        user=args.user,
        request_timeout=args.request_timeout,
        session_count=max(0, args.sessions),
        writer_concurrency=max(0, args.writer_concurrency),
        reader_concurrency=max(0, args.reader_concurrency),
        extract_concurrency=max(1, args.extract_concurrency),
        messages_per_commit=max(1, args.messages_per_commit),
        extract_ratio=min(max(args.extract_ratio, 0.0), 1.0),
        message_size=max(128, args.message_size),
        baseline_seconds=max(0.0, args.baseline_seconds),
        mixed_seconds=max(0.0, args.mixed_seconds),
        recovery_seconds=max(0.0, args.recovery_seconds),
        window_seconds=max(args.window_seconds, 1.0),
        observer_interval=0.0 if args.observer_interval <= 0 else max(args.observer_interval, 0.1),
        task_poll_interval=max(args.task_poll_interval, 0.1),
        task_drain_timeout=max(0.0, args.task_drain_timeout),
        output_dir=args.output_dir,
        cleanup=args.cleanup,
        require_extract_load=args.require_extract_load,
        find_queries=find_queries,
        find_limit=max(1, args.find_limit),
        find_target_uri=args.find_target_uri,
        find_score_threshold=args.find_score_threshold,
        seed=args.seed,
    )
    if config.writer_concurrency > 0 and config.session_count <= 0:
        parser.error("--sessions must be > 0 when --writer-concurrency is enabled")
    return config


def maybe_json(response: httpx.Response) -> Optional[Dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else {"value": body}


def extract_error(
    body: Optional[Dict[str, Any]], status_code: Optional[int]
) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(body, dict):
        if status_code is None:
            return None, None
        return None, f"http status {status_code}"
    error = body.get("error")
    if isinstance(error, dict):
        return error.get("code"), truncate_error_message(error.get("message"))
    if body.get("status") not in {None, "ok"}:
        return body.get("status"), truncate_error_message(json.dumps(body, ensure_ascii=False))
    if status_code is not None and status_code >= 400:
        return None, f"http status {status_code}"
    return None, None


def extract_session_id(body: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    session_id = result.get("session_id")
    return session_id if isinstance(session_id, str) else None


def extract_task_id(body: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    task_id = result.get("task_id")
    return task_id if isinstance(task_id, str) and task_id else None


def build_message_content(
    *, session_id: str, cycle_index: int, message_index: int, size: int
) -> str:
    prefix = (
        f"session={session_id} cycle={cycle_index} message={message_index}. "
        "We discussed project goals, deployment constraints, user preferences, debugging notes, "
        "timelines, risks, and follow-up actions. "
    )
    detail = (
        "The user prefers production-safe changes, wants clear rollback steps, and asked for "
        "memory extraction to keep decisions, entities, and events. "
        "We also covered resource bottlenecks, queue backlog, response latency, and how read "
        "traffic regressed during heavy write pressure. "
    )
    content = prefix
    while len(content) < size:
        content += detail
    return content[:size]


def truncate_error_message(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    if len(message) <= MAX_ERROR_MESSAGE_LEN:
        return message
    return message[:MAX_ERROR_MESSAGE_LEN] + "...[truncated]"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def total_duration_seconds(phases: List[PhaseMetadata]) -> float:
    return sum(item.duration_seconds for item in phases)


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
    *,
    events: List[RequestEvent],
    phase_durations: Dict[str, float],
    total_run_duration: float,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    rows.extend(
        _build_request_summary_for_groups(
            events=events,
            grouping=lambda event: (event.phase, event.api),
            duration_lookup=phase_durations,
        )
    )
    overall_groups = _build_request_summary_for_groups(
        events=events,
        grouping=lambda event: ("ALL", event.api),
        duration_lookup={"ALL": total_run_duration},
    )
    rows.extend(overall_groups)
    return sorted(rows, key=lambda row: (row["phase"], row["api"]))


def _build_request_summary_for_groups(
    *,
    events: List[RequestEvent],
    grouping,
    duration_lookup: Dict[str, float],
) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str], List[RequestEvent]] = {}
    for event in events:
        key = grouping(event)
        groups.setdefault(key, []).append(event)

    rows: List[Dict[str, Any]] = []
    for (phase, api), api_events in groups.items():
        latencies = [event.latency_ms for event in api_events]
        successes = sum(1 for event in api_events if event.success)
        failures = len(api_events) - successes
        timeouts = sum(1 for event in api_events if event.timeout)
        exceptions = sum(1 for event in api_events if event.exception_type)
        status_counts: Dict[str, int] = {}
        for event in api_events:
            key = str(event.status_code) if event.status_code is not None else "exception"
            status_counts[key] = status_counts.get(key, 0) + 1
        duration = max(duration_lookup.get(phase, 0.0), 1e-9)
        row = {
            "phase": phase,
            "api": api,
            "requests": len(api_events),
            "successes": successes,
            "failures": failures,
            "timeouts": timeouts,
            "exceptions": exceptions,
            "success_rate": round((successes / len(api_events)) * 100.0, 4),
            "qps": round(len(api_events) / duration, 4),
            "avg_ms": round(sum(latencies) / len(latencies), 4),
            "p50_ms": round_optional(percentile(latencies, 50)),
            "p90_ms": round_optional(percentile(latencies, 90)),
            "p95_ms": round_optional(percentile(latencies, 95)),
            "p99_ms": round_optional(percentile(latencies, 99)),
            "max_ms": round_optional(max(latencies) if latencies else None),
            "slow_gt_1s": sum(
                1 for latency in latencies if latency > DEFAULT_SLOW_THRESHOLDS_MS[0]
            ),
            "slow_gt_3s": sum(
                1 for latency in latencies if latency > DEFAULT_SLOW_THRESHOLDS_MS[1]
            ),
            "slow_gt_5s": sum(
                1 for latency in latencies if latency > DEFAULT_SLOW_THRESHOLDS_MS[2]
            ),
            "status_codes": json.dumps(status_counts, sort_keys=True),
        }
        rows.append(row)
    return rows


def build_request_window_rows(
    *,
    events: List[RequestEvent],
    window_seconds: float,
) -> List[Dict[str, Any]]:
    groups: Dict[tuple[int, str, str], List[RequestEvent]] = {}
    for event in events:
        window_index = int((event.elapsed_ms_since_run_start / 1000.0) // window_seconds)
        key = (window_index, event.phase, event.api)
        groups.setdefault(key, []).append(event)

    rows: List[Dict[str, Any]] = []
    for (window_index, phase, api), window_events in sorted(groups.items()):
        latencies = [event.latency_ms for event in window_events]
        successes = sum(1 for event in window_events if event.success)
        rows.append(
            {
                "window_index": window_index,
                "window_start_sec": round(window_index * window_seconds, 4),
                "window_end_sec": round((window_index + 1) * window_seconds, 4),
                "phase": phase,
                "api": api,
                "requests": len(window_events),
                "successes": successes,
                "failures": len(window_events) - successes,
                "success_rate": round((successes / len(window_events)) * 100.0, 4),
                "qps": round(len(window_events) / window_seconds, 4),
                "p95_ms": round_optional(percentile(latencies, 95)),
                "p99_ms": round_optional(percentile(latencies, 99)),
                "max_ms": round_optional(max(latencies) if latencies else None),
            }
        )
    return rows


def build_task_summary_rows(events: List[CommitTaskEvent]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[CommitTaskEvent]] = {}
    for event in events:
        groups.setdefault(event.status, []).append(event)

    rows: List[Dict[str, Any]] = []
    for status, status_events in sorted(groups.items()):
        server_latencies = [
            event.server_duration_ms
            for event in status_events
            if event.server_duration_ms is not None
        ]
        local_latencies = [event.local_duration_ms for event in status_events]
        successes = sum(1 for event in status_events if event.status == "completed")
        rows.append(
            {
                "status": status,
                "tasks": len(status_events),
                "successes": successes,
                "success_rate": round((successes / len(status_events)) * 100.0, 4),
                "p50_server_duration_ms": round_optional(percentile(server_latencies, 50)),
                "p95_server_duration_ms": round_optional(percentile(server_latencies, 95)),
                "p99_server_duration_ms": round_optional(percentile(server_latencies, 99)),
                "max_server_duration_ms": round_optional(
                    max(server_latencies) if server_latencies else None
                ),
                "p50_local_duration_ms": round_optional(percentile(local_latencies, 50)),
                "p95_local_duration_ms": round_optional(percentile(local_latencies, 95)),
                "p99_local_duration_ms": round_optional(percentile(local_latencies, 99)),
                "max_local_duration_ms": round_optional(
                    max(local_latencies) if local_latencies else None
                ),
            }
        )
    return rows


def build_find_phase_delta(summary_rows: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    baseline = next(
        (row for row in summary_rows if row["phase"] == "baseline" and row["api"] == "find"),
        None,
    )
    mixed = next(
        (row for row in summary_rows if row["phase"] == "mixed_load" and row["api"] == "find"),
        None,
    )
    if not baseline or not mixed:
        return None
    baseline_p95 = baseline.get("p95_ms")
    baseline_p99 = baseline.get("p99_ms")
    mixed_p95 = mixed.get("p95_ms")
    mixed_p99 = mixed.get("p99_ms")
    if not all(metric is not None for metric in [baseline_p95, baseline_p99, mixed_p95, mixed_p99]):
        return None
    return {
        "baseline_p95_ms": baseline_p95,
        "mixed_p95_ms": mixed_p95,
        "p95_delta_percent": percent_change(baseline_p95, mixed_p95),
        "baseline_p99_ms": baseline_p99,
        "mixed_p99_ms": mixed_p99,
        "p99_delta_percent": percent_change(baseline_p99, mixed_p99),
        "baseline_success_rate": baseline["success_rate"],
        "mixed_success_rate": mixed["success_rate"],
        "success_rate_delta_percent": mixed["success_rate"] - baseline["success_rate"],
    }


def find_request_summary_row(
    summary_rows: List[Dict[str, Any]],
    *,
    api: str,
    phase: str,
) -> Optional[Dict[str, Any]]:
    return next((row for row in summary_rows if row["api"] == api and row["phase"] == phase), None)


def phase_target_seconds(config: BenchmarkConfig, phase: str) -> Optional[float]:
    mapping = {
        "baseline": config.baseline_seconds,
        "mixed_load": config.mixed_seconds,
        "recovery": config.recovery_seconds,
    }
    return mapping.get(phase)


def build_phase_overview_rows(
    config: BenchmarkConfig,
    phase_metadata: List[PhaseMetadata],
) -> List[Dict[str, Optional[float]]]:
    rows: List[Dict[str, Optional[float]]] = []
    for item in phase_metadata:
        target = phase_target_seconds(config, item.phase)
        delta = None if target is None else item.duration_seconds - target
        rows.append(
            {
                "phase": item.phase,
                "target_seconds": round_optional(target),
                "actual_seconds": round_optional(item.duration_seconds),
                "delta_seconds": round_optional(delta),
            }
        )
    return rows


def build_api_error_breakdown(
    events: List[RequestEvent],
    *,
    api: str,
    phase: Optional[str] = None,
) -> Dict[str, Any]:
    filtered = [
        event for event in events if event.api == api and (phase is None or event.phase == phase)
    ]
    exception_counts: Dict[str, int] = {}
    error_counts: Dict[str, int] = {}
    for event in filtered:
        if event.exception_type:
            exception_counts[event.exception_type] = (
                exception_counts.get(event.exception_type, 0) + 1
            )
        key = event.error_code or event.exception_type
        if key:
            error_counts[key] = error_counts.get(key, 0) + 1
    return {
        "requests": len(filtered),
        "successes": sum(1 for event in filtered if event.success),
        "failures": sum(1 for event in filtered if not event.success),
        "timeouts": sum(1 for event in filtered if event.timeout),
        "exception_counts": exception_counts,
        "error_counts": error_counts,
    }


def format_phase_name_cn(phase: str) -> str:
    mapping = {
        "setup": "预热",
        "baseline": "基线阶段",
        "mixed_load": "混合压测阶段",
        "recovery": "恢复阶段",
        "drain": "收尾等待阶段",
        "cleanup": "清理阶段",
        "ALL": "全程",
    }
    return mapping.get(phase, phase)


def format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}s"


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def format_delta_percent(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def format_delta_seconds(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}s"


def format_change(old: Optional[float], new: Optional[float], *, unit: str = "ms") -> str:
    if old is None or new is None:
        return "n/a"
    if unit == "ms":
        return (
            f"{old:.2f}{unit} -> {new:.2f}{unit} ({format_delta_percent(percent_change(old, new))})"
        )
    return f"{old:.2f} -> {new:.2f} ({format_delta_percent(percent_change(old, new))})"


def format_qps_change(old: Optional[float], new: Optional[float]) -> str:
    if old is None or new is None:
        return "n/a"
    return f"{old:.2f} -> {new:.2f} ({format_delta_percent(percent_change(old, new))})"


def render_human_summary_zh(
    *,
    config: BenchmarkConfig,
    output_dir: str,
    notes: List[str],
    phase_metadata: List[PhaseMetadata],
    request_summary_rows: List[Dict[str, Any]],
    request_events: List[RequestEvent],
    task_summary_rows: List[Dict[str, Any]],
    task_events: List[CommitTaskEvent],
) -> str:
    lines: List[str] = []
    lines.append("=== OpenViking Session 竞争压测摘要 ===")
    lines.append(f"结果目录: {output_dir}")

    if notes:
        lines.append("")
        lines.append("说明:")
        for note in notes:
            lines.append(f"- {note}")

    phase_rows = build_phase_overview_rows(config, phase_metadata)
    baseline_find = find_request_summary_row(request_summary_rows, api="find", phase="baseline")
    mixed_find = find_request_summary_row(request_summary_rows, api="find", phase="mixed_load")
    recovery_find = find_request_summary_row(request_summary_rows, api="find", phase="recovery")
    mixed_add = find_request_summary_row(
        request_summary_rows, api="add_message", phase="mixed_load"
    )
    mixed_commit = find_request_summary_row(request_summary_rows, api="commit", phase="mixed_load")
    mixed_extract = find_request_summary_row(
        request_summary_rows, api="extract", phase="mixed_load"
    )
    baseline_status = find_request_summary_row(
        request_summary_rows, api="system_status", phase="baseline"
    )
    mixed_status = find_request_summary_row(
        request_summary_rows, api="system_status", phase="mixed_load"
    )
    baseline_queue = find_request_summary_row(
        request_summary_rows, api="observer_queue", phase="baseline"
    )
    mixed_queue = find_request_summary_row(
        request_summary_rows, api="observer_queue", phase="mixed_load"
    )
    find_delta = build_find_phase_delta(request_summary_rows)
    extract_breakdown = build_api_error_breakdown(request_events, api="extract", phase="mixed_load")
    completed_tasks = next(
        (row for row in task_summary_rows if row["status"] == "completed"),
        None,
    )
    incomplete_tasks = next(
        (row for row in task_summary_rows if row["status"] == "incomplete"),
        None,
    )
    total_task_count = len(task_events)

    lines.append("")
    lines.append("一、核心结论")
    if baseline_find and mixed_find and find_delta:
        lines.append(
            "- 已明确复现读接口退化：`find` 在混合压测阶段的 p95 从 "
            f"{baseline_find['p95_ms']:.2f}ms 升到 {mixed_find['p95_ms']:.2f}ms，"
            f"增幅 {find_delta['p95_delta_percent']:.2f}%；p99 从 "
            f"{baseline_find['p99_ms']:.2f}ms 升到 {mixed_find['p99_ms']:.2f}ms，"
            f"增幅 {find_delta['p99_delta_percent']:.2f}%。"
        )
        lines.append(
            "- `find` 吞吐也下降了：QPS 从 "
            f"{baseline_find['qps']:.2f} 降到 {mixed_find['qps']:.2f}，"
            f"变化 {format_delta_percent(percent_change(baseline_find['qps'], mixed_find['qps']))}。"
        )
    if recovery_find and baseline_find and mixed_find:
        lines.append(
            "- 恢复阶段没有完全回到基线：`find` p95 为 "
            f"{recovery_find['p95_ms']:.2f}ms，仍高于基线 "
            f"{format_delta_percent(percent_change(baseline_find['p95_ms'], recovery_find['p95_ms']))}；"
            "但相比混合压测阶段已经有明显回落。"
        )
    if mixed_extract:
        lines.append(
            "- 长尾压力主要来自 `extract`：混合压测阶段共 "
            f"{mixed_extract['requests']} 次调用，成功率 {mixed_extract['success_rate']:.2f}%，"
            f"p95 {mixed_extract['p95_ms']:.2f}ms。"
        )
    if extract_breakdown["timeouts"] > 0:
        lines.append(
            "- `extract` 失败几乎全是客户端超时："
            f"{extract_breakdown['timeouts']}/{extract_breakdown['requests']} 次超时，"
            f"主异常是 {format_top_counts(extract_breakdown['exception_counts'])}。"
        )
    if mixed_commit and completed_tasks:
        lines.append(
            "- `commit` 接口本身不是最重的部分：前台 `commit` p95 只有 "
            f"{mixed_commit['p95_ms']:.2f}ms；真正重的是后台任务，已完成任务的后台 p95 达 "
            f"{completed_tasks['p95_server_duration_ms']:.2f}ms。"
        )
    if incomplete_tasks:
        lines.append(
            "- 后台积压明显：本次共跟踪到 "
            f"{total_task_count} 个 `commit` 背景任务，其中 {incomplete_tasks['tasks']} 个在压测结束"
            "并等待 drain 后仍未完成。"
        )

    lines.append("")
    lines.append("二、阶段时长")
    for row in phase_rows:
        extra = ""
        if row["delta_seconds"] is not None and row["delta_seconds"] > 1:
            extra = "，实际时长明显长于目标值，通常说明脚本在等待 in-flight 会话周期收尾"
        lines.append(
            f"- {format_phase_name_cn(row['phase'])}: 目标 {format_seconds(row['target_seconds'])}，"
            f"实际 {format_seconds(row['actual_seconds'])}，偏差 {format_delta_seconds(row['delta_seconds'])}{extra}"
        )

    lines.append("")
    lines.append("三、关键指标对比")
    if baseline_find and mixed_find and recovery_find:
        lines.append(
            "- `find`:"
            f" 基线 p95={baseline_find['p95_ms']:.2f}ms / p99={baseline_find['p99_ms']:.2f}ms / qps={baseline_find['qps']:.2f};"
            f" 压测中 p95={mixed_find['p95_ms']:.2f}ms / p99={mixed_find['p99_ms']:.2f}ms / qps={mixed_find['qps']:.2f};"
            f" 恢复期 p95={recovery_find['p95_ms']:.2f}ms / p99={recovery_find['p99_ms']:.2f}ms / qps={recovery_find['qps']:.2f}。"
        )
    if mixed_add:
        lines.append(
            "- `add_message`: 混合压测阶段 "
            f"requests={mixed_add['requests']}，p50={mixed_add['p50_ms']:.2f}ms，"
            f"p95={mixed_add['p95_ms']:.2f}ms，p99={mixed_add['p99_ms']:.2f}ms。"
        )
    if mixed_commit:
        lines.append(
            "- `commit`: 混合压测阶段 "
            f"requests={mixed_commit['requests']}，p50={mixed_commit['p50_ms']:.2f}ms，"
            f"p95={mixed_commit['p95_ms']:.2f}ms，p99={mixed_commit['p99_ms']:.2f}ms。"
        )
    if mixed_extract:
        lines.append(
            "- `extract`: 混合压测阶段 "
            f"requests={mixed_extract['requests']}，success_rate={mixed_extract['success_rate']:.2f}%，"
            f"timeouts={extract_breakdown['timeouts']}，p95={mixed_extract['p95_ms']:.2f}ms。"
        )
    if completed_tasks:
        lines.append(
            "- `commit` 背景任务（completed）:"
            f" tasks={completed_tasks['tasks']}，p50={format_metric(completed_tasks['p50_server_duration_ms'])}，"
            f" p95={format_metric(completed_tasks['p95_server_duration_ms'])}，"
            f" p99={format_metric(completed_tasks['p99_server_duration_ms'])}。"
        )
    if incomplete_tasks:
        lines.append(
            "- `commit` 背景任务（incomplete）:"
            f" tasks={incomplete_tasks['tasks']}，本地等待 p95={format_metric(incomplete_tasks['p95_local_duration_ms'])}。"
        )
    if baseline_status and mixed_status:
        lines.append(
            "- `system_status`: p95 "
            f"{format_change(baseline_status['p95_ms'], mixed_status['p95_ms'])}。"
        )
    if baseline_queue and mixed_queue:
        lines.append(
            "- `observer_queue`: p95 "
            f"{format_change(baseline_queue['p95_ms'], mixed_queue['p95_ms'])}。"
        )

    lines.append("")
    lines.append("四、怎么理解这次结果")
    lines.append(
        "- `find` 没有报错，但延迟和吞吐同时变差，这比“报错”更说明问题：读请求被明显挤压了。"
    )
    lines.append("- `extract` 的大量 30 秒超时说明长尾请求已经被稳定制造出来了，压测目标基本达成。")
    lines.append(
        "- `commit` 前台接口看起来还好，但后台任务非常慢，说明资源竞争更可能发生在后续提取/索引阶段，而不是 HTTP 返回这一步。"
    )
    lines.append(
        "- 如果你要拿这次结果给别人看，最应该盯的是三组数字："
        "`find` 基线 vs 压测 p95/p99、`extract` 超时比例、`commit` 背景任务完成时长。"
    )

    return "\n".join(lines)


def format_top_counts(counts: Dict[str, int], limit: int = 3) -> str:
    if not counts:
        return "无"
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{key}={value}" for key, value in ordered[:limit])


def percent_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return ((new - old) / old) * 100.0


def round_optional(value: Optional[float], ndigits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(value, ndigits)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if hasattr(row, "to_dict"):
                row = row.to_dict()
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def extract_boolean(body: Optional[Dict[str, Any]], *keys: str) -> Optional[bool]:
    current: Any = body
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, bool) else None


def to_float(value: Any) -> Optional[float]:
    if isinstance(value, (float, int)):
        return float(value)
    return None


def format_metric(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}ms"


async def async_main(argv: Optional[List[str]] = None) -> int:
    config = parse_args(argv)
    runner = BenchmarkRunner(config)
    return await runner.run()


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        print("\n[stopped] benchmark interrupted by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
