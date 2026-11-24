"""Bounded model calls, task-local shards and worker queues for Compile."""

from __future__ import annotations

import asyncio
import inspect
import json
import posixpath
import shlex
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, TypeVar

import json_repair
from loguru import logger

from openviking.models.model_error import ModelError
from openviking.utils.media_limits import MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
from openviking_cli.exceptions import NotFoundError
from vikingbot.agent.tools.base import MultimodalToolResult
from vikingbot.compile.models import COMPILE_STAGING_ROOT, CompileFailure
from vikingbot.utils.helpers import cal_str_tokens

T = TypeVar("T")
ROOT = f"{COMPILE_STAGING_ROOT}/pipeline"
# Output-token fallback when the configured VLM provides no value.
DEFAULT_MAX_TOKENS = 32_000
# Total attempts per operator job or routing record, including the initial execution.
MAX_JOB_ATTEMPTS = 4


def retry_allowed(failures: Counter, error: str) -> bool:
    """Allow three retries per diagnostic within one assignment, independent of other errors.

    Call once after each failed attempt. Counts persist for recurring diagnostics;
    callers retain their existing cancellation, iteration and wall-clock limits.
    """
    failures[error] += 1
    return failures[error] <= 3


class ModelCallError(OSError):
    """A failed model request or stalled read loop; output validation errors remain separate."""


class ModelCallLimitError(ModelCallError):
    """A model deadline or bounded control-data read loop cannot complete."""


class TaskFiles:
    """Small independently replaced shards, accessed through the sandbox's file API.

    The task owns these paths. Same-directory rename publishes a complete JSON file;
    host-accessible sandboxes use native replacement, remote ones use exec/mv.
    A missing or interrupted shard is absent state, never successful work.
    """

    def __init__(self, sandbox: Any):
        self.sandbox = sandbox

    async def put(self, path: str, value: Any) -> str:
        """Atomically replace one runtime-owned shard and return its relative reference."""
        reference = f"{ROOT}/{path}.json"
        temporary = reference + "." + uuid.uuid4().hex + ".tmp"
        await self.sandbox.write_file(temporary, json.dumps(value, ensure_ascii=False))
        local_path = self.sandbox.local_file_path(temporary)
        if local_path is not None:
            # Both names share the sandbox-validated parent; only the suffix differs.
            await asyncio.to_thread(
                local_path.replace, local_path.with_name(posixpath.basename(reference))
            )
            return reference
        paths = [
            shlex.quote(posixpath.join(self.sandbox.sandbox_cwd, p)) for p in (temporary, reference)
        ]
        output = await self.sandbox.execute(f"mv -f -- {paths[0]} {paths[1]}")
        self.sandbox._ensure_command_succeeded(output, "Compile state publication")
        return reference

    async def get(self, path: str) -> Any:
        """Read an optional shard; transport/permission errors remain failures."""
        try:
            return json.loads(await self.sandbox.read_file_bytes(f"{ROOT}/{path}.json"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None


async def bounded_jobs(
    items: Iterable[T],
    run: Callable[[T], Awaitable[Any]],
    *,
    concurrency: int,
    metrics: Counter,
    failures: list[str] | None = None,
) -> list[Any]:
    """Run a finite collection with at most C workers and 2C queued assignments.

    Every queued input is visited after an ordinary failure. Cancellation cancels
    producer and workers together; caller-owned manifests retain pending inputs.
    Results preserve input order. An explicit failure sink retains successful jobs
    for partial delivery; otherwise failures raise with a bounded error preview.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=2 * concurrency)
    results: dict[int, Any] = {}
    errors: list[str] = []
    failed = 0

    async def produce() -> None:
        for index, item in enumerate(items):
            await queue.put((index, item))
            metrics["queue_peak"] = max(metrics["queue_peak"], queue.qsize())
        for _ in range(concurrency):
            await queue.put(None)

    async def worker() -> None:
        nonlocal failed
        while True:
            assignment = await queue.get()
            try:
                if assignment is None:
                    return
                index, item = assignment
                try:
                    results[index] = await run(item)
                except Exception as exc:
                    # Diagnostic I/O can mask CancelledError; cancelled workers must still exit.
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError() from exc
                    failed += 1
                    if len(errors) < 4:
                        errors.append(str(exc)[:500])
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(produce())]
    tasks.extend(asyncio.create_task(worker()) for _ in range(concurrency))
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if failed:
        message = f"{failed} pipeline jobs failed: {errors}"
        if failures is None:
            raise ValueError(message)
        failures.append(message)
    return [results[i] for i in sorted(results)]


class JsonModel:
    """Meter model calls and parse JSON control responses with scoped read tools.

    File-generation agents share the same timeout, usage and provider-slot accounting.
    Character estimates guide assignment sizing rather than imposing collection limits.
    """

    def __init__(self, provider, model, temperature, files, limits, usage, metrics):
        self.provider, self.model, self.temperature = provider, model, temperature
        self.files, self.usage, self.metrics = files, usage, metrics
        self.limits = limits
        self.thinking = {"map": False, "route": False, "reduce": False}
        underlying = getattr(provider, "_provider", provider)
        backend = getattr(underlying, "_vlm", underlying)
        backend = getattr(backend, "primary", backend)
        self.timeout = getattr(backend, "timeout", 600.0)
        self.backend_settings = {
            name: getattr(backend, name, None)
            for name in ("model", "thinking", "temperature", "max_tokens", "reasoning_effort")
        }
        configured_tokens = self.backend_settings["max_tokens"]
        self.max_tokens = (
            configured_tokens
            if isinstance(configured_tokens, int) and configured_tokens > 0
            else DEFAULT_MAX_TOKENS
        )
        self.resources = None
        self.agent_runner = None

    async def call(self, stage, messages, tools, *, max_tokens):
        """Measure one real request, including provider retries and semaphore waits.

        Diagnostics contain budgets, usage and failure categories, never reasoning.
        A cancelled or timed-out request cannot be accepted as a partial response.
        """
        from vikingbot.agent.loop import _inline_media_bytes

        media_bytes = _inline_media_bytes(messages)
        if media_bytes > MAX_INLINE_TOOL_RESULT_MEDIA_BYTES:
            raise CompileFailure(
                "MEDIA_LIMIT", "Compile image request exceeds the inline media budget", stage=stage
            )
        self.metrics[f"{stage}_calls"] += 1
        number = self.metrics[f"{stage}_calls"]
        started = time.monotonic()
        seconds = max(600, self.timeout)
        thinking = self.thinking.get(stage.split("_", 1)[0])
        report = {
            "stage": stage,
            "call": number,
            "max_tokens": max_tokens,
            "timeout_seconds": seconds,
            "thinking": thinking if thinking is not None else self.backend_settings["thinking"],
            "reasoning_effort": self.backend_settings["reasoning_effort"],
            "message_chars": sum(len(json.dumps(m, ensure_ascii=False)) for m in messages),
            "tool_schema_chars": len(json.dumps(tools, ensure_ascii=False)),
            "tool_result_chars": sum(
                len(str(m.get("content", ""))) for m in messages if m.get("role") == "tool"
            ),
            "skill_attachment_chars": sum(len(value) for value in self.resources.snapshots.values())
            if self.resources and stage not in {"plan", "review", "route"}
            else 0,
        }
        self.metrics["estimated_input_tokens"] += cal_str_tokens(
            json.dumps([messages, tools], ensure_ascii=False), text_type="mixed"
        )
        try:
            async with asyncio.timeout(seconds):
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    thinking=thinking,
                )
            report.update(
                finish_reason=response.finish_reason,
                usage={k: v for k, v in (response.usage or {}).items() if isinstance(v, int)},
                tools_called=[call.name for call in (response.tool_calls or [])],
            )
            for name, value in (response.usage or {}).items():
                if isinstance(value, int):
                    self.usage[name] = self.usage.get(name, 0) + value
                    self.metrics[f"{stage}_{name}"] += value
            if response.finish_reason == "error":
                error_kind = getattr(response, "error_kind", None)
                if error_kind is not None:
                    raise ModelError(
                        error_kind,
                        response.content or "Model transport failure; see provider diagnostics",
                    )
                raise OSError(
                    response.content or "Model transport failure; see provider diagnostics"
                )
            return response
        except TimeoutError as exc:
            report["error"] = "TimeoutError"
            raise ModelCallLimitError(
                f"{stage}: model call wall-clock budget exhausted ({seconds:g}s)"
            ) from exc
        except Exception as exc:
            report["error"] = type(exc).__name__
            raise ModelCallError(str(exc)) from exc
        except BaseException as exc:
            report["error"] = type(exc).__name__
            raise
        finally:
            report["milliseconds"] = round((time.monotonic() - started) * 1000)
            await self.files.put(f"calls/{stage}-{number}", report)
            logger.info("[COMPILE_CALL] {}", json.dumps(report))

    async def ask(self, stage, system, data, schema, validate=None, *, readers=None):
        """Parse an ordinary JSON response; only scoped read tools are offered.

        Invalid plans and routing references receive bounded correction attempts.
        File generation runs through AgentLoop rather than this control-data path.
        """
        readers = dict(readers or {})
        tools = [
            {
                "type": "function",
                "function": {
                    "name": reader.name,
                    "description": reader.description,
                    "parameters": reader.parameters,
                },
            }
            for reader in readers.values()
        ]
        messages = [
            {
                "role": "system",
                "content": system
                + "\nReturn one JSON object matching this schema:\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False),
            },
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]
        failures = Counter()
        for _ in range(self.limits.subagent_iterations):
            response = await self.call(stage, messages, tools, max_tokens=self.max_tokens)
            try:
                if response.finish_reason in {"length", "max_tokens"}:
                    raise ValueError("Model response was truncated")
                if response.tool_calls:
                    if any(c.name not in readers for c in response.tool_calls):
                        raise ValueError("Only the supplied read tools are allowed")
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content,
                            "tool_calls": [
                                {
                                    "id": c.id,
                                    "type": "function",
                                    "function": {
                                        "name": c.name,
                                        "arguments": json.dumps(c.arguments),
                                    },
                                }
                                for c in response.tool_calls
                            ],
                        }
                    )
                    for c in response.tool_calls:
                        try:
                            result = await readers[c.name].execute(**c.arguments)
                        except (ValueError, OSError, NotFoundError) as exc:
                            if not retry_allowed(failures, str(exc)):
                                raise ModelCallLimitError(str(exc)) from exc
                            result = "Error: " + str(exc)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": c.id,
                                "content": result.content
                                if isinstance(result, MultimodalToolResult)
                                else result,
                            }
                        )
                    continue
                result = schema.model_validate(json_repair.loads(response.content or ""))
                if validate:
                    checked = validate(result)
                    if inspect.isawaitable(checked):
                        await checked
                return result
            except (ValueError, TypeError) as exc:
                self.metrics["validation_failures"] += 1
                if not retry_allowed(failures, str(exc)):
                    raise ValueError(f"{stage}: {exc}") from exc
                messages.append({"role": "assistant", "content": response.content or ""})
                messages.append({"role": "user", "content": f"Correct the JSON result: {exc}"})
        raise ModelCallLimitError(f"{stage}: read/correction round limit reached")
