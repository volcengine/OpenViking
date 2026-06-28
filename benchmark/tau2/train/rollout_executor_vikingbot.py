#!/usr/bin/env python3
"""Tau2 RolloutExecutor implementation for batch policy training."""

from __future__ import annotations

import asyncio
import json
import posixpath
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.tau2.train._rollout_helpers import (
    _as_tool_input,
    _case_trial,
    _communicate_text_from_tool_input,
    _is_communicate_with_user,
    _message,
    _metadata_message,
    _stringify,
    _to_jsonable,
)
from benchmark.tau2.train._rollout_helpers import (
    _tau2_evaluation as _tau2_evaluation_helper,
)
from openviking.message import Message, ToolPart
from openviking.session.train import (
    Case,
    ExecutionContext,
    ExperienceSet,
    Rollout,
    RubricEvaluation,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _tau2_policy_current_time_match(policy: str) -> re.Match[str] | None:
    return re.search(
        r"(?im)\bcurrent\s+time\s+is\s+"
        r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*([A-Z]{2,5})?",
        policy or "",
    )


def _tau2_policy_current_time_display(policy: str) -> str | None:
    """Return tau2's authoritative business clock for prompt display."""
    match = _tau2_policy_current_time_match(policy)
    if not match:
        return None
    date_part, time_part, tz_name = match.groups()
    suffix = f" ({tz_name}; from tau2 policy)" if tz_name else " (from tau2 policy)"
    return f"{date_part} {time_part}{suffix}"


def _tau2_policy_current_time_iso(policy: str) -> str | None:
    """Return tau2's authoritative business clock as an ISO timestamp.

    Tau2 airline embeds the authoritative business clock in the policy, e.g.
    ``The current time is 2024-05-15 15:00:00 EST.``  Rollout artifacts should
    use that clock for message ``created_at`` so downstream trajectory/experience
    extraction does not treat the wall-clock run timestamp as business time.
    """
    match = _tau2_policy_current_time_match(policy)
    if not match:
        return None

    date_part, time_part, tz_name = match.groups()
    tz_offsets = {
        "UTC": "+00:00",
        "GMT": "+00:00",
        "EST": "-05:00",
        "EDT": "-04:00",
        "CST": "-06:00",
        "CDT": "-05:00",
        "MST": "-07:00",
        "MDT": "-06:00",
        "PST": "-08:00",
        "PDT": "-07:00",
    }
    offset = tz_offsets.get((tz_name or "").upper())
    if offset is not None:
        return f"{date_part}T{time_part}{offset}"
    return f"{date_part}T{time_part}"


def _viking_is_tool_result_success(result: Any) -> bool:
    # Mirror vikingbot.agent.loop._is_tool_result_success locally to avoid importing
    # private names from the bot package.
    if result is None or isinstance(result, Exception):
        return False
    text = str(result).lstrip()
    return bool(text) and not text.startswith("Error:")


def _tool_provider_cls():
    from benchmark.tau2.common.tau2_env.tau2_tool_provider import Tau2BenchToolProvider

    return Tau2BenchToolProvider


def _vikingbot_imports() -> dict[str, Any]:
    try:
        from vikingbot.agent.context import ContextBuilder
        from vikingbot.agent.loop import (
            AgentLoop,
            _PlainTextContext,
            _PlainTextDelivered,
            _PlainTextFinal,
        )
        from vikingbot.agent.tools.base import Tool
        from vikingbot.bus.queue import MessageBus
        from vikingbot.cli.commands import _init_bot_data, _make_provider
        from vikingbot.config.loader import ensure_config
        from vikingbot.config.schema import SessionKey
        from vikingbot.sandbox.manager import SandboxManager
        from vikingbot.session.manager import SessionManager
        from vikingbot.utils.helpers import get_source_workspace_path
    except ImportError as exc:  # pragma: no cover - benchmark environment dependency
        raise RuntimeError(
            "Failed to import vikingbot. Source benchmark/tau2/vikingbot/setup_env.sh first."
        ) from exc

    return {
        "AgentLoop": AgentLoop,
        "ContextBuilder": ContextBuilder,
        "_PlainTextContext": _PlainTextContext,
        "_PlainTextDelivered": _PlainTextDelivered,
        "_PlainTextFinal": _PlainTextFinal,
        "Tool": Tool,
        "MessageBus": MessageBus,
        "_init_bot_data": _init_bot_data,
        "_make_provider": _make_provider,
        "ensure_config": ensure_config,
        "SessionKey": SessionKey,
        "SandboxManager": SandboxManager,
        "SessionManager": SessionManager,
        "get_source_workspace_path": get_source_workspace_path,
    }


def _make_tau2_tool(
    schema: dict[str, Any],
    provider: Any,
    *,
    tool_lock: "_AsyncRWLock | None" = None,
    is_write_tool: bool = False,
    record_tool_timing: Callable[[str, float], None] | None = None,
):
    Tool = _vikingbot_imports()["Tool"]

    class Tau2Tool(Tool):
        """Bridge tau2 tool schema into VikingBot Tool interface."""

        def __init__(self, tool_schema: dict[str, Any], tool_provider: Any):
            self._schema = tool_schema
            self._provider = tool_provider
            function_def = tool_schema.get("function", {}) if isinstance(tool_schema, dict) else {}
            self._name = function_def.get("name", "")
            self._description = function_def.get("description", "")
            self._parameters = function_def.get("parameters", {})

        @property
        def name(self) -> str:
            return self._name

        @property
        def description(self) -> str:
            return self._description

        @property
        def parameters(self) -> dict[str, Any]:
            return self._parameters

        async def execute(self, tool_context: Any, **kwargs: Any) -> str:
            del tool_context
            started_at = time.perf_counter()

            try:
                if tool_lock is None:
                    return await asyncio.to_thread(self._provider.call_tool, self._name, kwargs)

                if is_write_tool:
                    async with tool_lock.writer():
                        return await asyncio.to_thread(self._provider.call_tool, self._name, kwargs)

                # Read path: acquire a shared (reader) lock so concurrent read tools
                # don't block each other.
                async with tool_lock.reader():
                    return await asyncio.to_thread(self._provider.call_tool, self._name, kwargs)
            finally:
                if record_tool_timing is not None:
                    record_tool_timing(self._name, _elapsed_ms(started_at))

    return Tau2Tool(schema, provider)


def _make_search_experience_tool():
    Tool = _vikingbot_imports()["Tool"]

    class SearchExperienceTool(Tool):
        @property
        def name(self) -> str:
            return "search_experience"

        @property
        def description(self) -> str:
            return (
                "Search OpenViking case memories under the current user, read each matched "
                "case's Linked Experiences section, and return candidate case summaries plus "
                "linked experience URIs. Use read_experience to open selected experience URIs."
            )

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language query describing the current task intent, target object, operation, policy/tool keywords.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum candidate cases to inspect and return.",
                        "default": 10,
                    },
                },
                "required": ["query"],
            }

        async def execute(
            self, tool_context: Any, query: str, limit: int = 10, **kwargs: Any
        ) -> str:
            del kwargs
            client = None
            try:
                from vikingbot.openviking_mount.ov_server import VikingClient

                client = await VikingClient.create()
                target_uri = _current_cases_uri(client)
                result = await client.search(query, target_uri=target_uri, limit=max(1, int(limit)))
                memories = result.get("memories", []) if isinstance(result, dict) else []
                candidates = [
                    await _experience_search_summary(client, item, rank)
                    for rank, item in enumerate(memories, start=1)
                ]
                return json.dumps(
                    {
                        "query": query,
                        "target_uri": target_uri,
                        "count": len(candidates),
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as exc:
                logger.warning("search_experience failed: %s", exc)
                return f"Error searching experience candidates: {exc}"
            finally:
                if client is not None:
                    await client.close()

    return SearchExperienceTool()


def _make_read_experience_tool():
    Tool = _vikingbot_imports()["Tool"]

    class ReadExperienceTool(Tool):
        @property
        def name(self) -> str:
            return "read_experience"

        @property
        def description(self) -> str:
            return "Read one OpenViking experience memory by full URI. Returns Markdown."

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "experience_uri": {
                        "type": "string",
                        "description": "Full Viking URI of the experience memory to read.",
                    },
                },
                "required": ["experience_uri"],
            }

        async def execute(self, tool_context: Any, experience_uri: str, **kwargs: Any) -> str:
            del tool_context, kwargs
            client = None
            try:
                from vikingbot.openviking_mount.ov_server import VikingClient

                client = await VikingClient.create()
                experience_uri = str(experience_uri or "").strip()
                if "/memories/experiences/" not in experience_uri:
                    return f"Error: URI is not an experience memory: {experience_uri}"
                content = await client.read_content(experience_uri, level="read")
                if not content:
                    return (
                        "# Loaded Experience\n\n"
                        f"Experience URI: `{experience_uri}`\n\n"
                        "Error: experience content not found."
                    )
                return "\n".join(
                    [
                        "# Loaded Experience",
                        "",
                        f"Experience URI: `{experience_uri}`",
                        "",
                        content.rstrip(),
                    ]
                ).rstrip()
            except Exception as exc:
                logger.warning("read_experience failed: %s", exc)
                return f"Error reading experience memory: {exc}"
            finally:
                if client is not None:
                    await client.close()

    return ReadExperienceTool()


def _current_cases_uri(client: Any) -> str:
    return f"{client._memory_target_uri(None).rstrip('/')}/cases"


def _case_uri(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("uri") or "")
    return str(getattr(item, "uri", "") or "")


def _case_score(item: Any) -> float:
    value = item.get("score", 0.0) if isinstance(item, dict) else getattr(item, "score", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _case_abstract(item: Any) -> str:
    return str(
        item.get("abstract", "") if isinstance(item, dict) else getattr(item, "abstract", "") or ""
    )


def _filename_name(uri: str) -> str:
    return str(uri or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")


def _markdown_section(content: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        content or "",
    )
    return match.group(1).strip() if match else ""


def _parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "").strip())
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _shorten(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _linked_experience_count(content: str) -> int:
    section = _markdown_section(content, "Linked Experiences")
    if not section:
        return 0
    links = re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", section)
    if links:
        return len(links)
    return sum(1 for line in section.splitlines() if line.strip().startswith("- "))


async def _experience_search_summary(client: Any, item: Any, rank: int) -> dict[str, Any]:
    case_uri = _case_uri(item)
    summary: dict[str, Any] = {
        "rank": rank,
        "score": round(_case_score(item), 6),
        "case_name": _filename_name(case_uri),
        "case_uri": case_uri,
        "case_abstract": _shorten(_case_abstract(item), 360),
        "experiences": [],
    }
    if not case_uri:
        return summary
    try:
        content = await client.read_content(case_uri, level="read")
    except Exception:
        return summary
    input_text = _markdown_section(content, "Input")
    input_obj = _parse_json_object(input_text)
    exp_uris = _linked_experience_uris(content, source_uri=case_uri)
    # ponytail: fetch Situation snippet per experience so agent can gate read_experience on applicability.
    experiences: list[dict[str, Any]] = []
    for idx, exp_uri in enumerate(exp_uris, start=1):
        exp_entry: dict[str, Any] = {
            "index": idx,
            "name": _filename_name(exp_uri),
            "uri": exp_uri,
            "situation": "",
        }
        try:
            exp_content = await client.read_content(exp_uri, level="read")
        except Exception:
            exp_content = ""
        situation = _markdown_section(exp_content, "Situation") if exp_content else ""
        # ponytail: cap at ~600 chars per exp to bound search-result tokens; exclusions ("不适用于"/"not apply") are preserved.
        exp_entry["situation"] = _shorten(situation, 600)
        experiences.append(exp_entry)
    summary.update(
        {
            "task_signature": _shorten(_markdown_section(content, "Task Signature")),
            "input_summary": _shorten(input_obj.get("summary") if input_obj else input_text),
            "experiences": experiences,
        }
    )
    return summary


def _linked_experience_uris(content: str, *, source_uri: str) -> list[str]:
    section = _markdown_section(content, "Linked Experiences")
    if not section:
        return []
    targets = re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", section)
    if not targets:
        targets = [
            line.lstrip("- ").strip()
            for line in section.splitlines()
            if line.strip().startswith("- ")
        ]
    uris: list[str] = []
    for target in targets:
        uri = _resolve_case_link_uri(target, source_uri=source_uri)
        if "/memories/experiences/" in uri and uri not in uris:
            uris.append(uri)
    return uris


def _resolve_case_link_uri(target: str, *, source_uri: str) -> str:
    target = str(target or "").strip()
    if not target:
        return ""
    if "://" in target:
        return target
    if "/" not in target:
        target = f"../experiences/{target.removesuffix('.md')}.md"
    if not source_uri.startswith("viking://"):
        return target
    source_dir = source_uri.removeprefix("viking://").rsplit("/", 1)[0]
    return "viking://" + posixpath.normpath(f"{source_dir}/{target}")


class _AsyncRWLock:
    """A simple asyncio reader/writer lock.

    - Multiple readers may hold the lock concurrently.
    - Writers get exclusive access; new readers are blocked while a writer is waiting
      to avoid writer starvation.
    - Not reentrant.
    """

    def __init__(self) -> None:
        self._readers = 0
        self._writers_waiting = 0
        self._writing = False
        self._lock = asyncio.Lock()
        self._readers_ok = asyncio.Condition(self._lock)
        self._writer_ok = asyncio.Condition(self._lock)

    def reader(self) -> "_ReaderCtx":
        return _ReaderCtx(self)

    def writer(self) -> "_WriterCtx":
        return _WriterCtx(self)

    async def _acquire_reader(self) -> None:
        async with self._lock:
            while self._writing or self._writers_waiting > 0:
                await self._readers_ok.wait()
            self._readers += 1

    async def _release_reader(self) -> None:
        async with self._lock:
            self._readers -= 1
            if self._readers == 0:
                self._writer_ok.notify()

    async def _acquire_writer(self) -> None:
        async with self._lock:
            self._writers_waiting += 1
            try:
                while self._readers > 0 or self._writing:
                    await self._writer_ok.wait()
                self._writing = True
            finally:
                self._writers_waiting -= 1

    async def _release_writer(self) -> None:
        async with self._lock:
            self._writing = False
            if self._writers_waiting > 0:
                self._writer_ok.notify()
            else:
                self._readers_ok.notify_all()


class _ReaderCtx:
    __slots__ = ("_rw",)

    def __init__(self, rw: _AsyncRWLock) -> None:
        self._rw = rw

    async def __aenter__(self) -> "_ReaderCtx":
        await self._rw._acquire_reader()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._rw._release_reader()


class _WriterCtx:
    __slots__ = ("_rw",)

    def __init__(self, rw: _AsyncRWLock) -> None:
        self._rw = rw

    async def __aenter__(self) -> "_WriterCtx":
        await self._rw._acquire_writer()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._rw._release_writer()


@dataclass(slots=True)
class VikingBotTau2RolloutExecutor:
    """Execute tau2 cases with VikingBot agent loop and tau2 tools."""

    config_path: str | None = None
    concurrency: int = 20
    keep_default_tools: bool = True
    max_iterations: int = 30
    log_timings: bool = True
    rollout_language: str = "default"

    def __post_init__(self) -> None:
        if self.rollout_language not in {"default", "zh"}:
            raise ValueError("rollout_language must be 'default' or 'zh'")

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        del policy_set
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(case: Case) -> Rollout:
            async with semaphore:
                return await self._execute_one(case, context)

        return list(await asyncio.gather(*(run_one(case) for case in cases)))

    async def _execute_one(self, case: Case, context: ExecutionContext) -> Rollout:
        return await self._execute_one_async(case, context)

    async def _execute_one_async(self, case: Case, context: ExecutionContext) -> Rollout:
        domain = str(case.input["domain"])
        task_id = str(case.input["task_id"])
        task_no = int(case.input["task_no"])
        data_split = str(case.input["data_split"])
        data_root = case.input.get("data_root")
        trial = _case_trial(case)

        timings = _RolloutTiming(case=case.name, enabled=self.log_timings)
        total_started_at = time.perf_counter()

        stage_started_at = time.perf_counter()
        Tau2BenchToolProvider = _tool_provider_cls()
        provider = Tau2BenchToolProvider(domain, task_id, data_root=data_root)
        await asyncio.to_thread(provider.reset)
        timings.record("provider_reset", stage_started_at)

        stage_started_at = time.perf_counter()
        agent = await asyncio.to_thread(
            _build_agent,
            self.config_path,
            max_iterations=self.max_iterations,
        )
        timings.record("build_agent", stage_started_at)

        stage_started_at = time.perf_counter()
        _configure_tools(
            agent,
            provider,
            keep_default_tools=self.keep_default_tools,
            record_tool_timing=timings.record_tool,
            task_id=task_id,
            task_no=task_no,
            data_split=data_split,
        )
        timings.record("configure_tools", stage_started_at)

        stage_started_at = time.perf_counter()
        system_prompt = _build_system_prompt(
            provider.policy,
            keep_default_tools=self.keep_default_tools,
            rollout_language=self.rollout_language,
        )
        user_prompt = provider.user_query
        SessionKey = _vikingbot_imports()["SessionKey"]
        trial_suffix = "" if trial is None else f"_r{int(trial)}"
        stage = _safe_session_fragment(str(context.metadata.get("stage") or "rollout"))
        session_key = SessionKey(
            type="cli",
            channel_id="tau2",
            chat_id=f"tau2_{stage}_{data_split}_{task_no}{trial_suffix}",
        )
        timings.record("prepare_prompt", stage_started_at)

        (
            final_content,
            final_reasoning_content,
            tools_used,
            token_usage,
            iteration,
            memory_content,
            experience_reminder,
            experience_loader_skill,
        ) = await _run_agent(
            agent=agent,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_key=session_key,
            sender_id="tau2_user",
            keep_default_tools=self.keep_default_tools,
            timings=timings,
            case_lookup=_tau2_case_lookup(case),
        )

        reward = None
        evaluation_result = None
        stage_started_at = time.perf_counter()
        if provider.env is not None:
            try:
                # Customer-facing content should be sent before `done`; do not append
                # the post-done final response to tau2's simulator/evaluator.
                reward, evaluation_result = await asyncio.to_thread(provider.env._get_reward)
                reward = _to_jsonable(reward)
                evaluation_result = _to_jsonable(evaluation_result)
            except Exception as exc:
                logger.exception(
                    "tau2 reward calculation failed case=%s domain=%s task_id=%s",
                    case.name,
                    domain,
                    task_id,
                )
                evaluation_result = {"error": str(exc), "type": type(exc).__name__}
        timings.record("reward", stage_started_at)

        stage_started_at = time.perf_counter()
        rollout = Rollout(
            case=case,
            messages=_build_rollout_messages(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_used=tools_used,
                final_content=final_content,
                evaluation_result=evaluation_result,
                reward=reward,
                experience_reminder=experience_reminder,
                artifact_created_at=_tau2_policy_current_time_iso(system_prompt),
            ),
            policy_snapshot_id=context.policy_snapshot_id,
            evaluation=_tau2_evaluation(reward=reward, evaluation_result=evaluation_result),
            metadata={
                "domain": domain,
                "data_split": data_split,
                "task_no": task_no,
                "task_id": task_id,
                "eval_trial": case.input.get("eval_trial"),
                "eval_trial_count": case.input.get("eval_trial_count"),
                "train_trial": case.input.get("train_trial"),
                "train_trial_count": case.input.get("train_trial_count"),
                "original_case_name": case.input.get("original_case_name"),
                "reward": reward,
                "evaluation_result": evaluation_result,
                "tools_used": tools_used,
                "token_usage": token_usage,
                "iterations": iteration,
                "memory": memory_content,
                "system_prompt": system_prompt,
                "business_current_time": _tau2_policy_current_time_iso(system_prompt),
                "user_prompt": user_prompt,
                "final_content": final_content,
                "final_reasoning_content": final_reasoning_content,
                "keep_default_tools": self.keep_default_tools,
                "ov_tools_enable": False,
                "experience_recall_enable": self.keep_default_tools,
                "experience_loader_skill": experience_loader_skill,
                "execution_metadata": dict(context.metadata),
            },
        )
        timings.record("build_rollout", stage_started_at)
        timings.log_summary(
            total_ms=_elapsed_ms(total_started_at),
            task_id=task_id,
            task_no=task_no,
            data_split=data_split,
            iterations=iteration,
            reward=reward,
            message_count=len(rollout.messages),
        )
        rollout.metadata["timing_ms"] = timings.snapshot(
            total_ms=_elapsed_ms(total_started_at),
            iterations=iteration,
        )
        return rollout


def _tau2_case_lookup(case: Case) -> dict[str, Any]:
    case_input = dict(case.input or {})
    domain = case_input.get("domain")
    split = case_input.get("split")
    task_id = case_input.get("task_id")
    # Trial cases append a trial suffix to Case.task_signature; case memories are
    # keyed by the stable tau2题目 identity, so use the base signature.
    task_signature = (
        f"tau2:{domain}:{split}:{task_id}"
        if domain is not None and split is not None and task_id is not None
        else case.task_signature
    )
    data_split = case_input.get("data_split")
    task_no = case_input.get("task_no")
    case_name = case_input.get("original_case_name") or case.name
    case_names = [case_name]
    if data_split is not None and task_no is not None:
        case_names.append(f"tau2_{data_split}_{task_no}")
    return {
        "benchmark": "tau2",
        "strict": True,
        "case_names": case_names,
        "domain": domain,
        "split": split,
        "data_split": data_split,
        "task_no": task_no,
        "task_id": task_id,
        "case_name": case_name,
        "task_signature": task_signature,
        "original_case_name": case_input.get("original_case_name"),
        "expected_fields": {
            "input.domain": domain,
            "input.split": split,
            "input.data_split": data_split,
            "input.task_no": task_no,
            "input.task_id": task_id,
        },
    }


def _append_final_answer_for_tau2_evaluation(provider_env: Any, final_content: str | None) -> None:
    if not final_content or not str(final_content).strip():
        return
    target = getattr(provider_env, "_impl", provider_env)
    append_message = getattr(target, "append_agent_message", None)
    if callable(append_message):
        append_message(str(final_content))


# Tokens tau2's user simulator emits to signal that the conversation should end.
_TAU2_USER_STOP_TOKENS = ("###STOP###",)
_TAU2_USER_TRANSFER_TOKENS = ("###TRANSFER###",)


def _tau2_user_reply_terminates(reply: Any) -> bool:
    text = str(reply or "")
    return any(tok in text for tok in _TAU2_USER_STOP_TOKENS + _TAU2_USER_TRANSFER_TOKENS)


def _make_tau2_plain_text_router(*, publish_events: bool, bus: Any, session_key: Any):
    """Build an `on_plain_text` callback that forwards assistant text via communicate_with_user.

    In tau2 bench, plain assistant text is semantically equivalent to calling
    `communicate_with_user`: both should be delivered to the user simulator so the
    simulated user can reply and the conversation can continue. This router is owned by
    the tau2 executor so vikingbot's generic AgentLoop stays benchmark-agnostic.
    """
    imports = _vikingbot_imports()
    PlainTextContext = imports["_PlainTextContext"]
    PlainTextDelivered = imports["_PlainTextDelivered"]
    PlainTextFinal = imports["_PlainTextFinal"]
    OutboundMsgType = imports["MessageBus"]  # only used for type/attr access
    del OutboundMsgType

    async def _route(
        ctx: PlainTextContext,  # type: ignore[valid-type]
    ):
        text = ctx.text
        # If the assistant text itself contains STOP (unlikely in tau2), treat as final.
        if any(tok in text for tok in _TAU2_USER_STOP_TOKENS):
            return PlainTextFinal(content=text)
        if not ctx.tools.has("communicate_with_user"):
            return PlainTextFinal(content=text)

        messages = list(ctx.messages)
        # Record the assistant text using the same dict shape vikingbot uses elsewhere.
        assistant_entry: dict[str, Any] = {"role": "assistant", "content": text}
        if ctx.reasoning_content:
            assistant_entry["reasoning_content"] = ctx.reasoning_content
        messages.append(assistant_entry)
        from vikingbot.utils.helpers import cal_str_tokens as _cal

        started_at = time.perf_counter()
        user_reply = await ctx.tools.execute(
            "communicate_with_user",
            {"content": text},
            session_key=ctx.session_key,
            sandbox_manager=ctx.sandbox_manager,
            sender_id=ctx.sender_id,
            memory_peer_ids=ctx.memory_peer_ids,
            memory_owner_user_ids=ctx.memory_owner_user_ids,
            openviking_connection=ctx.openviking_connection,
        )
        duration_ms = (time.perf_counter() - started_at) * 1000
        args_str = json.dumps({"content": text}, ensure_ascii=False)
        logger.info("[TAU2_PLAIN_TEXT]: routed assistant text through communicate_with_user")
        logger.info(f"[TOOL_CALL]: communicate_with_user({args_str[:200]})")
        logger.info(f"[RESULT]: {str(user_reply)[:600]}")
        if publish_events:
            from vikingbot.bus.events import OutboundEventType, OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    session_key=session_key,
                    content=f"communicate_with_user({args_str})",
                    event_type=OutboundEventType.TOOL_CALL,
                )
            )
            await bus.publish_outbound(
                OutboundMessage(
                    session_key=session_key,
                    content=str(user_reply),
                    event_type=OutboundEventType.TOOL_RESULT,
                )
            )
        tools_used = [
            {
                "tool_name": "communicate_with_user",
                "args": args_str,
                "result": user_reply,
                "duration": duration_ms,
                "execute_success": _viking_is_tool_result_success(user_reply),
                "input_token": 0,
                "output_token": _cal(user_reply, text_type="mixed"),
                "auto": True,
            }
        ]
        messages.append({"role": "user", "content": str(user_reply)})
        terminates = _tau2_user_reply_terminates(user_reply)
        return PlainTextDelivered(
            messages=messages,
            tools_used=tools_used,
            user_terminates=terminates,
        )

    return _route


def _build_agent(config_path: str | None, *, max_iterations: int):
    imports = _vikingbot_imports()
    config = imports["ensure_config"](Path(config_path).expanduser() if config_path else None)
    imports["_init_bot_data"](config)
    bus = imports["MessageBus"]()
    session_manager = imports["SessionManager"](config.bot_data_path)
    sandbox_parent_path = config.workspace_path
    source_workspace_path = imports["get_source_workspace_path"]()
    sandbox_manager = imports["SandboxManager"](config, sandbox_parent_path, source_workspace_path)
    provider = imports["_make_provider"](config)
    return imports["AgentLoop"](
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.model,
        max_iterations=max_iterations,
        memory_window=config.agents.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exa_api_key=None,
        gen_image_model=config.agents.gen_image_model,
        exec_config=config.tools.exec,
        cron_service=None,
        session_manager=session_manager,
        sandbox_manager=sandbox_manager,
        config=config,
        eval=True,
        mcp_servers=None,
    )


def _configure_tools(
    agent: Any,
    provider: Any,
    *,
    keep_default_tools: bool,
    record_tool_timing: Callable[[str, float], None] | None = None,
    task_id: str | None = None,
    task_no: int | None = None,
    data_split: str | None = None,
) -> None:
    # Tau2 rollout may keep generic VikingBot tools, but OpenViking access is
    # restricted to automatic experience recall during prompt construction.
    # No openviking_* tool should be callable by the agent.
    del keep_default_tools
    for tool_name in list(agent.tools.tool_names):
        if str(tool_name).startswith("openviking_"):
            agent.tools.unregister(tool_name)
    agent.tools.register(_make_search_experience_tool())
    agent.tools.register(_make_read_experience_tool())
    tool_lock = _AsyncRWLock()
    write_tool_names = _classify_write_tools(provider)
    for schema in provider.list_openai_tools():
        fn_name = str((schema.get("function") or {}).get("name") or "")
        agent.tools.register(
            _make_tau2_tool(
                schema,
                provider,
                tool_lock=tool_lock,
                is_write_tool=fn_name in write_tool_names,
                record_tool_timing=record_tool_timing,
            )
        )


def _classify_write_tools(provider: Any) -> set[str]:
    """Classify which tau2 tools mutate environment state.

    Pure read/lookup tools can run in parallel within a single rollout; state-mutating
    tools (book/update/cancel/etc.) plus communicate_with_user and ``done`` must run
    exclusively because they advance the user simulator and tau2 DB state.
    """
    write_names: set[str] = {"communicate_with_user", "done"}

    # 1) Introspect the underlying tau2 ToolKit: tau2 marks tools with __tool_type__ and
    #    __mutates_state__. Prefer this when available (covers both gym and native envs).
    env = getattr(provider, "env", None)
    inner = getattr(env, "_impl", None) if env is not None else None
    inner_env = getattr(inner, "env", None) if inner is not None else None
    for toolkit_attr in ("tools", "user_tools"):
        toolkit = getattr(inner_env, toolkit_attr, None) if inner_env is not None else None
        if toolkit is None:
            continue
        get_tools_fn = getattr(toolkit, "get_tools", None)
        tool_type_fn = getattr(toolkit, "tool_type", None)
        mutates_fn = getattr(toolkit, "tool_mutates_state", None)
        try:
            tools_dict = get_tools_fn() if callable(get_tools_fn) else None
        except Exception:
            tools_dict = None
        if isinstance(tools_dict, dict):
            for name, tool_fn in tools_dict.items():
                mutates = getattr(tool_fn, "__mutates_state__", None)
                tool_type = getattr(tool_fn, "__tool_type__", None)
                if mutates is None and mutates_fn is not None:
                    try:
                        mutates = mutates_fn(name)
                    except Exception:
                        mutates = None
                if tool_type is None and tool_type_fn is not None:
                    try:
                        tool_type = tool_type_fn(name)
                    except Exception:
                        tool_type = None
                is_write = mutates is True or str(tool_type) in {
                    "write",
                    "ToolType.WRITE",
                    "ToolType.WRITE.value",
                }
                if is_write:
                    write_names.add(str(name))

    # 2) Heuristic fallback for tools not introspected above: any tool not starting
    #    with a read-y prefix is assumed to be a writer. This is conservative (pessimistic
    #    about parallelism) rather than risking races on stateful tools.
    try:
        schemas = list(provider.list_openai_tools() or [])
    except Exception:
        schemas = []
    _READ_PREFIXES = (
        "get_",
        "search_",
        "list_",
        "find_",
        "retrieve_",
        "lookup_",
        "check_",
        "view_",
        "describe_",
        "think",
        "summary",
    )
    for schema in schemas:
        fn = schema.get("function") or {}
        name = str(fn.get("name") or "")
        if not name or name in write_names:
            continue
        if not any(name.startswith(p) for p in _READ_PREFIXES):
            write_names.add(name)
    return write_names


def _build_system_prompt(policy: str, *, keep_default_tools: bool, rollout_language: str) -> str:
    del keep_default_tools
    instructions = []
    if policy:
        instructions.append(policy)
    instructions.append("Use the provided tools to interact with the environment.")
    instructions.append(
        "Before taking task actions, you MUST use the required `experience_loader` skill. "
        "It explains how to search OpenViking case memories with the `search_experience` tool, return linked experience URIs, and read selected experiences using the `read_experience` tool."
    )
    instructions.append(
        "Loaded experiences are guidance from prior training runs. "
        "Use them only when their situation and applicability boundaries match the current "
        "task; current policy, current tool results, and current user facts override prior "
        "experience."
    )
    if rollout_language == "zh":
        instructions.append(
            "Communicate with the user and write the final response in Chinese. "
            "Do not translate tool names, identifiers, JSON field names, reservation IDs, "
            "flight numbers, or other structured values used by tools."
        )
    instructions.append(
        "If you need to communicate with the user, you MUST call tool `communicate_with_user`."
    )
    instructions.append(
        "When communicating numbers, prices, reservation IDs, flight numbers, airport codes, "
        "dates, names, or other values from tool results, include the exact original value "
        "verbatim even if the surrounding response is in another language."
    )
    instructions.append(
        "When the task is finished or terminated, send any final customer-facing message "
        "through `communicate_with_user` before calling `done`. After `done`, do not call "
        "any more tools and do not emit extra ending content."
    )
    return "\n".join(instructions)


EXPERIENCE_LOADER_TEMPLATE_DIR = Path(__file__).resolve().parent / "experience_loader_template"
EXPERIENCE_LOADER_SKILL_PATH = "skills/experience_loader/SKILL.md"


async def _prepare_experience_loader_skill(
    *,
    agent: Any,
    session_key: Any,
) -> Any:
    """Install the generic experience_loader skill into the rollout sandbox.

    The loader does not contain per-task memory. It instructs the LLM to use the
    tau2-only `search_experience` and `read_experience` tools to search case-linked experiences and load selected experience memories.
    """

    imports = _vikingbot_imports()
    sandbox_manager = getattr(agent, "sandbox_manager", None)
    workspace_path = (
        sandbox_manager.get_workspace_path(session_key)
        if sandbox_manager
        else agent.context.workspace
    )
    skill_content = _read_experience_loader_template_file("SKILL.md")
    if sandbox_manager:
        try:
            sandbox = await sandbox_manager.get_sandbox(session_key)
            await sandbox.write_file(EXPERIENCE_LOADER_SKILL_PATH, skill_content)
        except Exception as exc:
            logger.warning("failed to write experience_loader skill to sandbox: %s", exc)
            _write_experience_loader_files(
                workspace_path=workspace_path,
                skill_content=skill_content,
            )
    else:
        _write_experience_loader_files(
            workspace_path=workspace_path,
            skill_content=skill_content,
        )

    context_builder = imports["ContextBuilder"](
        workspace_path,
        sandbox_manager=sandbox_manager,
        eval=True,
    )
    context_builder.latest_experience_loader_skill_content = skill_content
    return context_builder


def _read_experience_loader_template_file(relative_path: str) -> str:
    return (EXPERIENCE_LOADER_TEMPLATE_DIR / relative_path).read_text(encoding="utf-8")


def _write_experience_loader_files(
    *,
    workspace_path: Path,
    skill_content: str,
) -> None:
    skill_dir = workspace_path / "skills" / "experience_loader"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(skill_content, encoding="utf-8")


async def _execute_required_experience_loader_read(
    *,
    agent: Any,
    messages: list[dict[str, Any]],
    session_key: Any,
    sender_id: str,
) -> dict[str, Any]:
    """Force-load the generic experience_loader skill before task actions."""

    path = EXPERIENCE_LOADER_SKILL_PATH
    tool_id = "required-experience-loader-skill-read"
    messages.append(
        {
            "role": "assistant",
            "content": "Reading required experience_loader skill before task actions.",
            "tool_calls": [
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": path}, ensure_ascii=False),
                    },
                }
            ],
        }
    )
    started_at = time.perf_counter()
    result = await agent.tools.execute(
        "read_file",
        {"path": path},
        session_key=session_key,
        sandbox_manager=agent.sandbox_manager,
        sender_id=sender_id,
    )
    duration_ms = _elapsed_ms(started_at)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_id,
            "name": "read_file",
            "content": result,
        }
    )
    execute_success = not (isinstance(result, str) and result.lstrip().startswith("Error"))
    if not execute_success:
        logger.warning("required experience_loader skill read failed: %s", str(result)[:300])
    return {
        "tool_name": "read_file",
        "args": json.dumps({"path": path}, ensure_ascii=False),
        "result": result,
        "duration": duration_ms,
        "execute_success": execute_success,
        "input_token": 0,
        "output_token": 0,
        "auto": True,
        "required_skill": "experience_loader",
    }


async def _run_agent(
    *,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    session_key: Any,
    sender_id: str,
    keep_default_tools: bool,
    timings: "_RolloutTiming | None" = None,
    case_lookup: dict[str, Any] | None = None,
):
    stage_started_at = time.perf_counter()
    message_context = agent.context
    del case_lookup
    message_context = await _prepare_experience_loader_skill(
        agent=agent,
        session_key=session_key,
    )
    experience_loader_skill = getattr(
        message_context,
        "latest_experience_loader_skill_content",
        None,
    )
    messages = await message_context.build_messages(
        history=[],
        current_message=user_prompt,
        session_key=session_key,
        ov_tools_enable=False,
        experience_recall_enable=False,
        media=None,
        profile_user_list=[],
    )
    if timings is not None:
        timings.record("build_messages", stage_started_at)
    if system_prompt:
        messages.insert(1, {"role": "system", "content": system_prompt})
    _override_vikingbot_current_time_messages(
        messages,
        business_current_time=_tau2_policy_current_time_display(system_prompt),
    )
    user_memory = None
    experience_reminder_text = None  # 完整的 [Experience Reminder] 消息文本（用于 messages.json）
    for msg in messages:
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if not isinstance(content, str):
            continue
        # Experience Reminder (经验记忆) - role=user, starts with [Experience Reminder]
        if "[Experience Reminder]" in content and "## Relevant Agent Experience" in content:
            experience_reminder_text = content
            continue
        # User memory (用户记忆) - starts with "## Current Session"
        if content.startswith("## Current Session"):
            user_memory = _extract_memory_content(content)

    # 合并用户记忆 + 经验记忆正文，去重
    exp_content = (
        _extract_experience_content(experience_reminder_text) if experience_reminder_text else None
    )
    memory_content = _merge_memories(user_memory, exp_content)
    stage_started_at = time.perf_counter()
    required_skill_tool = None
    if experience_loader_skill and experience_loader_skill.strip():
        required_skill_tool = await _execute_required_experience_loader_read(
            agent=agent,
            messages=messages,
            session_key=session_key,
            sender_id=sender_id,
        )
    plain_text_router = _make_tau2_plain_text_router(
        publish_events=False,
        bus=getattr(agent, "bus", None),
        session_key=session_key,
    )
    result = await agent._run_agent_loop(
        messages=messages,
        session_key=session_key,
        publish_events=False,
        sender_id=sender_id,
        ov_tools_enable=False,
        stop_tool_names=["done"],
        on_plain_text=plain_text_router,
    )
    if timings is not None:
        timings.record("agent_loop", stage_started_at)
    final_content, final_reasoning_content, tools_used, token_usage, iteration = result
    if required_skill_tool is not None:
        tools_used = [required_skill_tool, *tools_used]
    case_memory_context = _case_memory_context_from_tools(tools_used)
    memory_content = _merge_memories(memory_content, case_memory_context)
    if _last_tool_name(tools_used) == "done":
        final_content = None
        final_reasoning_content = None
    return (
        final_content,
        final_reasoning_content,
        tools_used,
        token_usage,
        iteration,
        memory_content,
        experience_reminder_text,
        experience_loader_skill,
    )


def _override_vikingbot_current_time_messages(
    messages: list[dict[str, Any]],
    *,
    business_current_time: str | None,
) -> None:
    """Replace VikingBot's wall-clock prompt time with tau2's business time.

    VikingBot's generic context builder includes ``## Current Time: <system
    clock>`` in the user-memory wrapper.  For tau2, the domain policy owns the
    business clock; leaving the host clock in the prompt can make the agent
    interpret unqualified dates against the run date.
    """
    if not business_current_time:
        return
    replacement = f"## Current Time: {business_current_time}"
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or "## Current Time:" not in content:
            continue
        msg["content"] = re.sub(
            r"(?m)^## Current Time: .*$",
            replacement,
            content,
            count=1,
        )


@dataclass(slots=True)
class _RolloutTiming:
    case: str
    enabled: bool
    stages: dict[str, float] = field(default_factory=dict)
    tool_durations: list[tuple[str, float]] = field(default_factory=list)

    def record(self, stage: str, started_at: float) -> None:
        if self.enabled:
            self.stages[stage] = _elapsed_ms(started_at)

    def record_tool(self, tool_name: str, duration_ms: float) -> None:
        if self.enabled:
            self.tool_durations.append((tool_name, duration_ms))

    def snapshot(self, *, total_ms: float, iterations: int | None) -> dict[str, Any]:
        """Return a JSON-serializable timing breakdown for rollout.metadata."""
        tool_total_ms = sum(duration for _, duration in self.tool_durations)
        tool_counts: dict[str, int] = {}
        tool_total_by_name: dict[str, float] = {}
        tool_max_by_name: dict[str, float] = {}
        for name, duration in self.tool_durations:
            tool_counts[name] = tool_counts.get(name, 0) + 1
            tool_total_by_name[name] = tool_total_by_name.get(name, 0.0) + duration
            cur = tool_max_by_name.get(name, 0.0)
            if duration > cur:
                tool_max_by_name[name] = duration
        tools_by_name = {
            name: {
                "count": tool_counts[name],
                "total_ms": round(tool_total_by_name[name], 2),
                "avg_ms": round(tool_total_by_name[name] / tool_counts[name], 2),
                "max_ms": round(tool_max_by_name[name], 2),
            }
            for name in tool_counts
        }
        slowest = max(self.tool_durations, key=lambda item: item[1], default=None)
        return {
            "total_ms": round(total_ms, 2),
            "iterations": iterations,
            "stages_ms": {k: round(v, 2) for k, v in self.stages.items()},
            "tool_count": len(self.tool_durations),
            "tool_total_ms": round(tool_total_ms, 2),
            "slowest_tool": (
                {"name": slowest[0], "duration_ms": round(slowest[1], 2)}
                if slowest is not None
                else None
            ),
            "tools_by_name": tools_by_name,
        }

    def log_summary(self, *, total_ms: float, **metadata: Any) -> None:
        if not self.enabled:
            return
        tool_total_ms = sum(duration for _, duration in self.tool_durations)
        slowest_tool = max(self.tool_durations, key=lambda item: item[1], default=None)
        logger.info(
            "tau2 rollout timing case=%s total_ms=%.1f stages=%s tool_count=%d "
            "tool_total_ms=%.1f slowest_tool=%s metadata=%s",
            self.case,
            total_ms,
            _format_stage_timings(self.stages),
            len(self.tool_durations),
            tool_total_ms,
            _format_tool_timing(slowest_tool),
            metadata,
        )


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def _format_stage_timings(stages: dict[str, float]) -> str:
    return ",".join(f"{stage}:{duration_ms:.1f}" for stage, duration_ms in stages.items())


def _format_tool_timing(item: tuple[str, float] | None) -> str | None:
    if item is None:
        return None
    tool_name, duration_ms = item
    return f"{tool_name}:{duration_ms:.1f}"


def _safe_session_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value)[:80] or "rollout"


MEMORY_PROMPT_PREFIX = "## Current Session\nChannel: cli\n\n---\n\n"
MEMORY_PROMPT_SUFFIX = (
    "---\n\nReply in the same language as the user's query, ignoring the language of "
    "the reference materials. User's query:"
)


def _extract_memory_content(content: str) -> str | None:
    start = content.find(MEMORY_PROMPT_PREFIX)
    end = content.rfind(MEMORY_PROMPT_SUFFIX)
    if start == -1 or end == -1:
        return None
    start += len(MEMORY_PROMPT_PREFIX)
    if start > end:
        return None
    return content[start:end]


def _extract_experience_content(content: str) -> str | None:
    """从 Experience Reminder 消息中提取经验记忆正文。"""
    prefix = "[Experience Reminder]\n## Relevant Agent Experience\n"
    start = content.find(prefix)
    if start == -1:
        return None
    start += len(prefix)
    return content[start:].strip() or None


def _case_memory_context_from_tools(tools_used: list[dict] | None) -> str:
    blocks: list[str] = []
    for tool in tools_used or []:
        if not isinstance(tool, dict) or tool.get("tool_name") != "read_experience":
            continue
        result = str(tool.get("result") or "").strip()
        if not result:
            continue
        args = tool.get("args")
        blocks.append(
            "\n".join(
                [
                    "## Loaded Experience",
                    "",
                    "Tool: `read_experience`",
                    "",
                    "Args:",
                    "```json",
                    str(args or "{}"),
                    "```",
                    "",
                    result,
                ]
            )
        )
    if not blocks:
        return ""
    return "# Experience Loader Context\n\n" + "\n\n---\n\n".join(blocks)


def _merge_memories(user_memory: str | None, exp_memory: str | None) -> str | None:
    """合并用户记忆和经验记忆，去重。

    两者都为 None 时返回 None；只有一个时直接返回它；都有时拼接并标记类型。
    """
    parts: list[str] = []
    if user_memory and user_memory.strip():
        parts.append(f"## User Memories\n{user_memory.strip()}")
    if exp_memory and exp_memory.strip():
        parts.append(f"## Experience Memories\n{exp_memory.strip()}")
    if not parts:
        return None
    return "\n\n---\n\n".join(parts)


def _build_rollout_messages(
    *,
    system_prompt: str,
    user_prompt: str,
    tools_used: Any,
    final_content: str | None,
    evaluation_result: Any,
    reward: Any,
    experience_reminder: str | None = None,
    artifact_created_at: str | None = None,
) -> list[Message]:
    messages = [
        _metadata_message(
            "tau2-system",
            f"system:\n{system_prompt}",
            created_at=artifact_created_at,
        ),
    ]
    # Experience Reminder 放在 system 之后、user 之前，与 agent 实际看到的顺序一致
    if experience_reminder:
        messages.append(
            _message("tau2-experience", "user", experience_reminder, created_at=artifact_created_at)
        )
    messages.append(_message("tau2-user", "user", user_prompt, created_at=artifact_created_at))
    if isinstance(tools_used, list):
        for idx, tool_info in enumerate(tools_used):
            if not isinstance(tool_info, dict):
                continue
            tool_name = str(tool_info.get("tool_name") or "unknown")
            if not tool_name or tool_name == "unknown" and not tool_info.get("result"):
                continue
            args = tool_info.get("args", "")
            tool_input = _as_tool_input(args)
            result = tool_info.get("result")
            has_result = result is not None
            if _is_communicate_with_user(tool_name):
                assistant_text = _communicate_text_from_tool_input(tool_input)
                if assistant_text.strip():
                    messages.append(
                        _message(
                            f"tau2-communicate-assistant-{idx}",
                            "assistant",
                            assistant_text,
                            created_at=artifact_created_at,
                        )
                    )
                if has_result:
                    user_text = _stringify(result)
                    if user_text.strip():
                        messages.append(
                            _message(
                                f"tau2-communicate-user-{idx}",
                                "user",
                                user_text,
                                created_at=artifact_created_at,
                            )
                        )
                continue
            messages.append(
                Message(
                    id=f"tau2-tool-{idx}",
                    role="user" if has_result else "assistant",
                    parts=[
                        ToolPart(
                            tool_id=f"tau2-tool-{idx}",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_output=_stringify(result) if has_result else "",
                            tool_status="completed" if has_result else "running",
                        )
                    ],
                    created_at=artifact_created_at,
                )
            )
    if final_content and str(final_content).strip():
        messages.append(
            _message("tau2-final", "assistant", str(final_content), created_at=artifact_created_at)
        )
    reward_jsonable = _to_jsonable(reward)
    evaluation_jsonable = _to_jsonable(evaluation_result)
    success = reward_jsonable == 1 or reward_jsonable == 1.0
    messages.append(
        _message(
            "tau2-reward",
            "user",
            f"task_success: {success}\ntask_reward: {reward_jsonable}\n"
            f"evaluation report: {_stringify(evaluation_jsonable)}",
            created_at=artifact_created_at,
        )
    )
    return messages


def _tau2_evaluation(*, reward: Any, evaluation_result: Any) -> RubricEvaluation:
    return _tau2_evaluation_helper(
        reward=reward, evaluation_result=evaluation_result, source="tau2_executor"
    )


def _last_tool_name(tools_used: Any) -> str:
    if not isinstance(tools_used, list) or not tools_used:
        return ""
    last = tools_used[-1]
    if not isinstance(last, dict):
        return ""
    return str(last.get("tool_name") or "")


# Backwards-compatible alias for existing imports.
Tau2RolloutExecutor = VikingBotTau2RolloutExecutor
