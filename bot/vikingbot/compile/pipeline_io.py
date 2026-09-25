"""Bounded model calls, task-local shards and worker queues for Compile."""

from __future__ import annotations

import asyncio
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
from pydantic import BaseModel, ValidationError

from vikingbot.compile.models import COMPILE_STAGING_ROOT
from vikingbot.compile.plan import (
    DEFAULT_MAX_TOKENS,
    PROCESSING_VERSION,
    FileResponse,
    RouteBatchResponse,
    digest,
    result_schema,
)
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.utils.helpers import cal_str_tokens

T = TypeVar("T")
R = TypeVar("R", bound=BaseModel)
ROOT = f"{COMPILE_STAGING_ROOT}/pipeline"


def parse_file_result(text: str) -> Any:
    """Parse file-result JSON, escaping only unambiguous bare quotes in files[].content.

    Existing escapes and all other fields remain unchanged. A quote followed by
    a comma or closing object ends the content. Malformed results raise ValueError
    for the caller's normal retry; alternative content boundaries are not searched.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    stack, parts = [], []
    key, start, pos = None, 0, 0
    while pos < len(text):
        char = text[pos]
        if char == '"':
            if key == "content" and stack == [("{", None), ("[", "files"), ("{", None)]:
                pos += 1
                while pos < len(text):
                    if text[pos] == "\\":
                        pos += 2
                        continue
                    if text[pos] == '"':
                        end = pos + 1
                        while end < len(text) and text[end] in " \t\r\n":
                            end += 1
                        if end == len(text) or text[end] in ",}":
                            break
                        parts.append(text[start:pos] + "\\")
                        start = pos
                    pos += 1
                key = None
            else:
                value, pos = decoder.raw_decode(text, pos)
                end = pos
                while end < len(text) and text[end] in " \t\r\n":
                    end += 1
                key = value if end < len(text) and text[end] == ":" else None
                continue
        elif char in "{[":
            stack.append((char, key))
            key = None
        elif char in "}]":
            if not stack:
                raise ValueError("Unexpected closing delimiter in file result")
            stack.pop()
            key = None
        elif char == ",":
            key = None
        pos += 1
    parts.append(text[start:])
    return json.loads("".join(parts))


def retry_allowed(failures: Counter, error: str) -> bool:
    """Allow three retries per diagnostic within one assignment, independent of other errors.

    Call once after each failed attempt. Counts persist for recurring diagnostics;
    callers retain their existing cancellation, iteration and wall-clock limits.
    """
    failures[error] += 1
    return failures[error] <= 3


class ModelCallError(OSError):
    """A model timeout or provider failure; output validation errors remain separate."""


class TaskFiles:
    """Small independently replaced shards, accessed through the sandbox's file API.

    The task owns these paths. Same-directory rename publishes a complete JSON file;
    host-accessible sandboxes use native replacement, remote ones use exec/mv.
    A missing or interrupted cache shard is a cache miss, never successful work.
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
            return json.loads(
                await self.sandbox.read_file_bytes(
                    f"{ROOT}/{path}.json", max_bytes=16 * 1024 * 1024
                )
            )
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
    """Direct structured calls sharing the existing Compile provider and usage counters.

    Character estimates guide batching and evidence inlining, never reject requests. Usage
    estimates reuse AgentLoop's mixed-text estimator; provider usage is authoritative.
    Cache identity includes model settings, Skill/contract and processing version.
    Only validated responses enter the cache. Each distinct validation failure
    receives the shared per-error retry allowance.
    """

    def __init__(self, provider, model, temperature, files, limits, usage, metrics):
        self.provider, self.model, self.temperature = provider, model, temperature
        self.files, self.usage, self.metrics = files, usage, metrics
        self.limits = limits
        self.budget = limits.merge_input_chars
        self.output_tokens = min(4096, max(128, self.budget // 8))
        self.reserve = self.output_tokens * 4
        self.identity = {"version": PROCESSING_VERSION, "model": model, "temperature": temperature}
        self.identity.update(
            batch_target_chars=self.budget, output_reserve_tokens=self.output_tokens
        )
        self.identity["thinking"] = False
        underlying = getattr(provider, "_provider", provider)
        backend = getattr(underlying, "_vlm", underlying)
        self.identity["backend_settings"] = {
            name: getattr(backend, name, None)
            for name in ("model", "thinking", "temperature", "max_tokens", "reasoning_effort")
        }
        configured_tokens = self.identity["backend_settings"]["max_tokens"]
        self.max_tokens = (
            configured_tokens
            if isinstance(configured_tokens, int) and configured_tokens > 0
            else DEFAULT_MAX_TOKENS
        )
        self.identity["max_tokens"] = self.max_tokens
        self.identity["provider"] = type(underlying).__name__
        self.identity["endpoint"] = digest(str(getattr(underlying, "api_base", "")))
        self.agent_runner = None
        self.resources = None

    def fits(self, system: str, data: Any, schema: type[BaseModel]) -> bool:
        """Check a soft batching target, including schema and estimated output space."""
        size = len(system) + len(json.dumps(data, ensure_ascii=False))
        size += len(json.dumps(result_schema(schema, data))) + 1200
        return size + self.reserve <= self.budget

    async def call(self, stage, messages, tools, *, max_tokens):
        """Measure one real request, including provider retries and semaphore waits.

        Diagnostics contain budgets, usage and failure categories, never reasoning.
        A cancelled or timed-out request cannot be accepted as a partial response.
        """
        self.metrics[f"{stage}_calls"] += 1
        number = self.metrics[f"{stage}_calls"]
        started = time.monotonic()
        report = {
            "stage": stage,
            "call": number,
            "max_tokens": max_tokens,
            "timeout_seconds": self.limits.pipeline_call_seconds,
            "thinking": False,
            "message_chars": sum(len(json.dumps(m, ensure_ascii=False)) for m in messages),
            "tool_schema_chars": len(json.dumps(tools, ensure_ascii=False)),
            "tool_result_chars": sum(
                len(str(m.get("content", ""))) for m in messages if m.get("role") == "tool"
            ),
            "skill_attachment_chars": sum(len(value) for value in self.resources.snapshots.values())
            if self.resources and stage not in {"plan", "route", "combine"}
            else 0,
        }
        self.metrics["estimated_input_tokens"] += cal_str_tokens(
            json.dumps([messages, tools], ensure_ascii=False), text_type="mixed"
        )
        try:
            async with asyncio.timeout(self.limits.pipeline_call_seconds):
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    thinking=report["thinking"],
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
                raise OSError(
                    response.content or "Model transport failure; see provider diagnostics"
                )
            return response
        except TimeoutError as exc:
            report["error"] = "TimeoutError"
            raise ModelCallError(
                f"{stage}: model call wall-clock budget exhausted ({self.limits.pipeline_call_seconds:g}s)"
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

    async def ask(
        self, stage: str, system: str, data: Any, schema: type[R], validate=None, *, agent=False
    ) -> R:
        """Bound the whole assignment, including reads, repairs and backend retry sleeps."""
        seconds = (
            self.limits.pipeline_plan_seconds
            if stage == "plan"
            else self.limits.pipeline_agent_seconds
        )
        try:
            async with asyncio.timeout(seconds):
                return await self._ask(stage, system, data, schema, validate, agent=agent)
        except TimeoutError as exc:
            raise ModelCallError(f"{stage}: wall-clock budget exhausted ({seconds:g}s)") from exc

    async def _ask(self, stage, system, data, schema, validate, *, agent):
        if schema is FileResponse:
            # Inline original excerpts when located; unlocated records retain full shards.
            # The scoped reader can expand partial evidence without catalog access.
            evidence = EvidenceReader(self.files, data)
            for reference in sorted(evidence.allowed - evidence.delivered):
                source = await evidence.read(reference, evidence.spans.get(reference, ()))
                candidate = {
                    **data,
                    "original_evidence": {**data.get("original_evidence", {}), reference: source},
                }
                if self.fits(system, candidate, schema):
                    data = candidate
        # Combine consolidates supplied evidence without loading Skill instructions or attachments.
        resources = self.resources if stage not in {"plan", "route", "combine"} else None
        if resources and resources.snapshots:
            system += "\nComplete Skill attachments (authoritative data):\n" + json.dumps(
                resources.snapshots, ensure_ascii=False
            )
        dependencies = dict(resources.hashes) if resources else {}
        key = digest(
            [self.identity, stage, system, data, schema.model_json_schema(), agent, dependencies]
        )
        cached = None if schema is RouteBatchResponse else await self.files.get(f"cache/{key}")
        if cached is not None and (not resources or await resources.valid(cached["dependencies"])):
            try:
                result = schema.model_validate(cached["result"])
                if validate:
                    validate(result)
                self.metrics["model_cache_hits"] += 1
                return result
            except ValueError:
                pass
        if agent:
            if self.agent_runner is None:
                raise ValueError("This Compile runtime cannot execute a required agent transform")
            self.metrics[f"{stage}_agent_jobs"] += 1
            result = await self.agent_runner(
                system, data, schema, validate, self, stage=f"{stage}_agent"
            )
        else:
            result = await self.direct(stage, system, data, schema, validate, key)
        if schema is RouteBatchResponse:
            return result  # Shuffle persists only individually validated routing decisions.
        await self.files.put(
            f"cache/{key}",
            {
                "result": result.model_dump(),
                "dependencies": dict(resources.hashes) if resources else {},
            },
        )
        return result

    async def direct(self, stage, system, data, schema, validate, key):
        """Allow scoped reads and three repairs for each distinct output failure."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "emit",
                    "description": "Submit a complete structured result.",
                    "parameters": result_schema(schema, data),
                },
            }
        ]
        readers = {}
        if stage not in {"plan", "combine"}:
            evidence = EvidenceReader(self.files, data)
            if evidence.allowed - evidence.delivered:
                readers["read_evidence"] = evidence
        if self.resources and stage not in {"plan", "route", "combine"}:
            readers[self.resources.name] = self.resources
        for reader in readers.values():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": reader.name,
                        "description": reader.description,
                        "parameters": reader.parameters,
                    },
                }
            )
        if stage not in {"plan", "route", "combine"}:
            system += (
                "\nSupplied complete attachments fulfill the Skill's reading requirements. "
                "Only read additional resources whose contents are missing; never reread supplied text."
            )
        messages = [
            {
                "role": "system",
                "content": system
                + "\nSubmit the final result with one `emit` tool call, without extra prose.",
            },
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]
        failures = 0
        retries = Counter()
        while True:
            response = await self.call(stage, messages, tools, max_tokens=self.max_tokens)
            raw = None
            category = "format"
            try:
                calls = response.tool_calls
                raw = calls[0].arguments if calls else response.content
                if response.finish_reason in {"length", "max_tokens"}:
                    category = "truncated"
                    raise ValueError(
                        "Output limit reached; reduce result size or use file references for agent files"
                    )
                if calls and all(c.name in readers for c in calls):
                    messages.append(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": c.id,
                                    "type": "function",
                                    "function": {
                                        "name": c.name,
                                        "arguments": json.dumps(c.arguments),
                                    },
                                }
                                for c in calls
                            ],
                        }
                    )
                    for call in calls:
                        try:
                            value = await readers[call.name].execute(**call.arguments)
                        except (ValueError, OSError) as exc:
                            value = "Error: " + str(exc)[:800]
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": value})
                    continue
                if calls:
                    if len(calls) != 1 or calls[0].name != "emit":
                        raise ValueError("Expected one emit call, or Skill resource reads")
                    raw = calls[0].arguments
                else:
                    raw = json_repair.loads(response.content or "", stream_stable=True)
                if (
                    isinstance(raw, dict)
                    and set(raw) == {"raw"}
                    and "raw" not in schema.model_fields
                    and isinstance(raw["raw"], str)
                ):
                    # Repair provider-preserved text before validating structure and provenance.
                    raw = raw["raw"]
                    if schema is FileResponse and stage in {"reduce", "reduce_merge"}:
                        raw = parse_file_result(raw)
                    else:
                        raw = json_repair.loads(raw, stream_stable=True)
                category = "schema"
                result = schema.model_validate(raw)
                category = "semantic"
                if validate:
                    validate(result)
                return result
            except (ValueError, TypeError) as exc:
                if isinstance(exc, ValidationError):
                    error = "\n".join(
                        f"{'.'.join(str(part) for part in item['loc']) or '$'}: "
                        f"{item['type']}: {item['msg']}"
                        for item in exc.errors(include_url=False, include_input=False)
                    )[:1600]
                else:
                    error = str(exc)[:1600]
                failures += 1
                candidate = json.dumps(raw, ensure_ascii=False) if raw is not None else ""
                await self.files.put(
                    f"candidates/{key}-{failures}",
                    {
                        "stage": stage,
                        "category": category,
                        "error": error,
                        "candidate": raw,
                        "candidate_truncated": False,
                        "response_content": response.content,
                        "response_tool_calls": [vars(call) for call in (response.tool_calls or [])],
                        "finish_reason": response.finish_reason,
                    },
                )
                self.metrics["validation_failures"] += 1
                # Routing owns retries per primary record, including malformed responses.
                if schema is RouteBatchResponse or not retry_allowed(retries, error):
                    raise ValueError(f"{stage}: {category}: {error}") from exc
                self.metrics["repairs"] += 1
                messages.append(
                    {
                        "role": "user",
                        "content": "Correct this rejected result while preserving valid content. "
                        "Rejected candidate:\n"
                        + candidate
                        + "\nError: "
                        + error
                        + "\nSubmit the COMPLETE corrected result with one emit tool call matching "
                        "the provided schema, including unchanged valid entries."
                        + (
                            "\nAll contract settings belong directly inside the contract object. "
                            "The plan is a DSL string; do not wrap the result or contract in a JSON string."
                            if stage == "plan"
                            else ""
                        ),
                    }
                )
