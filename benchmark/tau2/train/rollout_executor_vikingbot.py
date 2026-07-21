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
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from benchmark.tau2.train._rollout_helpers import (
    _as_tool_input,
    _case_trial,
    _message,
    _stringify,
    _to_jsonable,
)
from benchmark.tau2.train._rollout_helpers import (
    _tau2_evaluation as _tau2_evaluation_helper,
)
from openviking.message import Message, TextPart, ToolPart
from openviking.session.train import (
    Case,
    ExecutionContext,
    ExperienceSet,
    Rollout,
    RubricEvaluation,
)
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

Tau2ExperienceLoaderMode = Literal["skill", "constraint", "direct_experience"]
VikingBotSystemPromptProfile = Literal["full", "minimal"]
DEFAULT_TAU2_EXPERIENCE_LOADER_MODE: Tau2ExperienceLoaderMode = "skill"
DEFAULT_SYSTEM_PROMPT_PROFILE: VikingBotSystemPromptProfile = "minimal"


def normalize_system_prompt_profile(value: Any) -> VikingBotSystemPromptProfile:
    profile = str(value or DEFAULT_SYSTEM_PROMPT_PROFILE).strip().lower()
    if profile not in {"full", "minimal"}:
        raise ValueError("system_prompt_profile must be 'full' or 'minimal'")
    return profile  # type: ignore[return-value]


def normalize_tau2_experience_loader_mode(value: Any) -> Tau2ExperienceLoaderMode:
    mode = str(value or DEFAULT_TAU2_EXPERIENCE_LOADER_MODE).strip().lower()
    if mode not in {"skill", "constraint", "direct_experience"}:
        raise ValueError("loader_mode must be 'skill', 'constraint', or 'direct_experience'")
    return mode  # type: ignore[return-value]


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


def _make_search_experience_tool(case_lookup: dict[str, Any] | None = None):
    Tool = _vikingbot_imports()["Tool"]

    class SearchExperienceTool(Tool):
        @property
        def name(self) -> str:
            return "search_experience"

        @property
        def description(self) -> str:
            return (
                "Search OpenViking case memories under the current user, read each matched "
                "case's Linked Experiences section, and return candidate case names plus "
                "linked experience URIs and Situation snippets. Use read_experience to open "
                "selected experience URIs."
            )

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "situation": {
                        "type": "string",
                        "description": (
                            "Concise declarative Situation based only on facts in the current "
                            "conversation. Preserve the user's known goal, target, scope, and "
                            "explicit constraints in natural language; do not use a keyword list "
                            "or add speculative alternatives."
                        ),
                    },
                    "task_signature": {
                        "type": "string",
                        "description": (
                            "Optional stable Case task_signature supplied by Runtime Case "
                            "context. Pass it exactly when provided; do not infer or modify it."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum candidate cases to inspect and return.",
                        "default": 2,
                    },
                },
                "required": ["situation"],
            }

        async def execute(
            self,
            tool_context: Any,
            situation: str,
            task_signature: str | None = None,
            limit: int = 2,
            **kwargs: Any,
        ) -> str:
            del tool_context, kwargs
            client = None
            try:
                from vikingbot.openviking_mount.ov_server import VikingClient

                client = await VikingClient.create()
                target_uri = _current_cases_uri(client)
                exact_item = await _find_exact_case_item(
                    client,
                    case_lookup=case_lookup,
                    task_signature=task_signature,
                    cases_root_uri=target_uri,
                )
                if exact_item is not None:
                    candidate = await _experience_search_summary(client, exact_item, rank=1)
                    candidates = _deduplicate_candidate_experiences([candidate])
                    _trace_experience_recall(
                        match_type="exact_case",
                        task_signature=task_signature,
                        candidates=candidates,
                    )
                    return _format_search_experience_response(
                        situation=situation,
                        task_signature=task_signature,
                        match_type="exact_case",
                        candidates=candidates,
                    )
                result = await client.search(
                    situation,
                    target_uri=target_uri,
                    limit=max(1, int(limit)),
                )
                memories = result.get("memories", []) if isinstance(result, dict) else []
                candidates = [
                    await _experience_search_summary(client, item, rank)
                    for rank, item in enumerate(memories, start=1)
                ]
                candidates = _deduplicate_candidate_experiences(candidates)
                fallback_reason = (
                    "task_signature_not_found" if str(task_signature or "").strip() else None
                )
                _trace_experience_recall(
                    match_type="semantic",
                    task_signature=task_signature,
                    candidates=candidates,
                    fallback_reason=fallback_reason,
                )
                return _format_search_experience_response(
                    situation=situation,
                    task_signature=task_signature,
                    match_type="semantic",
                    fallback_reason=fallback_reason,
                    candidates=candidates,
                )
            except Exception as exc:
                logger.warning("search_experience failed: %s", exc)
                return f"Error searching experience candidates: {exc}"
            finally:
                if client is not None:
                    await client.close()

    return SearchExperienceTool()


def _format_search_experience_response(
    *,
    situation: str,
    candidates: list[dict[str, Any]],
    match_type: str = "semantic",
    task_signature: str | None = None,
    fallback_reason: str | None = None,
) -> str:
    """Render search_experience output for the agent.

    Keep only task-facing information. Internal search roots and duplicate counts are omitted
    because they can make the agent reason about storage plumbing instead of candidate
    applicability.
    """

    payload: dict[str, Any] = {
        "match_type": match_type,
        "situation": situation,
        "candidates": candidates,
    }
    if task_signature:
        payload["task_signature"] = task_signature
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _deduplicate_candidate_experiences(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen_uris: set[str] = set()
    for candidate in candidates:
        deduplicated: list[dict[str, Any]] = []
        for experience in candidate.get("experiences") or []:
            uri = str(experience.get("uri") or "")
            if uri and uri in seen_uris:
                continue
            if uri:
                seen_uris.add(uri)
            deduplicated.append(experience)
        candidate["experiences"] = deduplicated
    return candidates


def _trace_experience_recall(
    *,
    match_type: str,
    task_signature: str | None,
    candidates: list[dict[str, Any]],
    fallback_reason: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "event": "experience_recall",
        "match_type": match_type,
        "candidate_count": len(candidates),
        "experience_count": sum(
            len(candidate.get("experiences") or []) for candidate in candidates
        ),
    }
    if task_signature:
        payload["task_signature"] = task_signature
        payload["exact_case_found"] = match_type == "exact_case"
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason
    tracer.info(json.dumps(payload, ensure_ascii=False))


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


async def _find_exact_case_item(
    client: Any,
    *,
    case_lookup: dict[str, Any] | None,
    task_signature: str | None,
    cases_root_uri: str,
) -> dict[str, Any] | None:
    task_signature = str(task_signature or "").strip()
    if not task_signature or not case_lookup:
        return None

    from vikingbot.agent.memory import MemoryStore

    lookup = MemoryStore._normalize_case_lookup(case_lookup)
    if task_signature != str(lookup.get("task_signature") or ""):
        return None

    for case_uri in MemoryStore._case_uri_candidates(cases_root_uri, lookup):
        content = await client.read_content(case_uri, level="read")
        if not content:
            continue
        if not MemoryStore._case_matches_lookup(content, lookup, uri=case_uri):
            continue
        return {
            "uri": case_uri,
            "score": 1.0,
            "abstract": "",
        }
    return None


def _case_uri(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("uri") or "")
    return str(getattr(item, "uri", "") or "")


def _filename_name(uri: str) -> str:
    return str(uri or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")


def _markdown_section(content: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        content or "",
    )
    return match.group(1).strip() if match else ""


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
        "case_name": _filename_name(case_uri),
        "experiences": [],
    }
    if not case_uri:
        return summary
    try:
        content = await client.read_content(case_uri, level="read")
    except Exception:
        return summary
    exp_uris = _linked_experience_uris(content, source_uri=case_uri)
    # ponytail: fetch Situation snippet per experience so agent can gate read_experience on applicability.
    experiences: list[dict[str, Any]] = []
    for exp_uri in exp_uris:
        exp_entry: dict[str, Any] = {
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
    summary["experiences"] = experiences
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
    loader_mode: Tau2ExperienceLoaderMode = DEFAULT_TAU2_EXPERIENCE_LOADER_MODE
    system_prompt_profile: VikingBotSystemPromptProfile = DEFAULT_SYSTEM_PROMPT_PROFILE
    direct_experience_content: str | None = None
    direct_experience_name: str | None = None
    direct_experience_uri: str | None = None

    def __post_init__(self) -> None:
        if self.rollout_language not in {"default", "zh"}:
            raise ValueError("rollout_language must be 'default' or 'zh'")
        self.loader_mode = normalize_tau2_experience_loader_mode(self.loader_mode)
        self.system_prompt_profile = normalize_system_prompt_profile(self.system_prompt_profile)
        if (
            self.loader_mode == "direct_experience"
            and not str(self.direct_experience_content or "").strip()
        ):
            raise ValueError(
                "direct_experience_content is required when loader_mode='direct_experience'"
            )

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

        case_lookup = _tau2_case_lookup(case)

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
            loader_mode=self.loader_mode,
            record_tool_timing=timings.record_tool,
            task_id=task_id,
            task_no=task_no,
            data_split=data_split,
            case_lookup=case_lookup,
        )
        timings.record("configure_tools", stage_started_at)

        stage_started_at = time.perf_counter()
        system_prompt = _build_system_prompt(
            provider.policy,
            keep_default_tools=self.keep_default_tools,
            rollout_language=self.rollout_language,
            loader_mode=self.loader_mode,
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
            runtime_messages,
        ) = await _run_agent(
            agent=agent,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_key=session_key,
            sender_id="tau2_user",
            keep_default_tools=self.keep_default_tools,
            loader_mode=self.loader_mode,
            system_prompt_profile=self.system_prompt_profile,
            direct_experience_content=self.direct_experience_content,
            direct_experience_name=self.direct_experience_name,
            direct_experience_uri=self.direct_experience_uri,
            timings=timings,
            case_lookup=case_lookup,
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
                runtime_messages=runtime_messages,
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
                "experience_loader_mode": self.loader_mode,
                "system_prompt_profile": self.system_prompt_profile,
                "experience_loader_skill": experience_loader_skill,
                "direct_experience": _direct_experience_metadata(
                    content=self.direct_experience_content,
                    name=self.direct_experience_name,
                    uri=self.direct_experience_uri,
                    enabled=self.loader_mode == "direct_experience",
                ),
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
    loader_mode: Tau2ExperienceLoaderMode = DEFAULT_TAU2_EXPERIENCE_LOADER_MODE,
    record_tool_timing: Callable[[str, float], None] | None = None,
    task_id: str | None = None,
    task_no: int | None = None,
    data_split: str | None = None,
    case_lookup: dict[str, Any] | None = None,
) -> None:
    # Tau2 rollout may keep generic VikingBot tools, but OpenViking access is
    # restricted to automatic experience recall during prompt construction.
    # No openviking_* tool should be callable by the agent.
    del keep_default_tools
    loader_mode = normalize_tau2_experience_loader_mode(loader_mode)
    for tool_name in list(agent.tools.tool_names):
        if str(tool_name).startswith("openviking_"):
            agent.tools.unregister(tool_name)
    if loader_mode == "skill":
        agent.tools.register(_make_search_experience_tool(case_lookup=case_lookup))
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


def _build_system_prompt(
    policy: str,
    *,
    keep_default_tools: bool,
    rollout_language: str,
    loader_mode: Tau2ExperienceLoaderMode = DEFAULT_TAU2_EXPERIENCE_LOADER_MODE,
) -> str:
    del keep_default_tools
    loader_mode = normalize_tau2_experience_loader_mode(loader_mode)
    instructions = []
    if policy:
        instructions.append(policy)
    instructions.append("Use the provided tools to interact with the environment.")
    if loader_mode == "skill":
        instructions.append(
            "Before taking task actions, you MUST use the required `experience_loader` skill. "
            "It explains how to search OpenViking case memories with the `search_experience` "
            "tool, use Situation snippets only as filters, and read every non-excluded "
            "experience that may apply to the current task or a later task boundary using "
            "the `read_experience` tool."
        )
        instructions.append(
            "Loaded experiences are guidance from prior training runs. "
            "Use them only when their situation and applicability boundaries match the current "
            "task; current policy, current tool results, and current user facts override prior "
            "experience."
        )
    elif loader_mode == "direct_experience":
        instructions.append(
            "A direct experimental experience will be injected as an Experience Reminder before "
            "the task. Treat it as prior-run guidance only when it matches the current situation; "
            "current policy, current tool results, and current user facts override it."
        )
    else:
        instructions.append(
            "Experience constraints may be injected automatically as reminder messages before "
            "tool calls. Treat those reminders as prior-run guidance, but current policy, "
            "current tool results, and current user facts override prior experience."
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
    system_prompt_profile: VikingBotSystemPromptProfile = DEFAULT_SYSTEM_PROMPT_PROFILE,
    task_signature: str | None = None,
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
    task_signature = str(task_signature or "").strip()
    if task_signature:
        skill_content = "\n".join(
            [
                skill_content.rstrip(),
                "",
                "## Runtime Case context",
                "",
                f"- `task_signature`: `{task_signature}`",
                "- Pass this exact value to `search_experience`; do not infer or modify it.",
                "",
            ]
        )
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
        system_prompt_profile=system_prompt_profile,
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
    loader_mode: Tau2ExperienceLoaderMode = DEFAULT_TAU2_EXPERIENCE_LOADER_MODE,
    system_prompt_profile: VikingBotSystemPromptProfile = DEFAULT_SYSTEM_PROMPT_PROFILE,
    direct_experience_content: str | None = None,
    direct_experience_name: str | None = None,
    direct_experience_uri: str | None = None,
    timings: "_RolloutTiming | None" = None,
    case_lookup: dict[str, Any] | None = None,
):
    stage_started_at = time.perf_counter()
    loader_mode = normalize_tau2_experience_loader_mode(loader_mode)
    system_prompt_profile = normalize_system_prompt_profile(system_prompt_profile)
    message_context = agent.context
    experience_loader_skill = None
    if loader_mode == "skill":
        message_context = await _prepare_experience_loader_skill(
            agent=agent,
            session_key=session_key,
            system_prompt_profile=system_prompt_profile,
            task_signature=str((case_lookup or {}).get("task_signature") or "") or None,
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
        system_prompt_profile=system_prompt_profile,
    )
    if timings is not None:
        timings.record("build_messages", stage_started_at)
    if system_prompt:
        messages.insert(1, {"role": "system", "content": system_prompt})
    direct_experience_reminder = None
    if loader_mode == "direct_experience":
        direct_experience_reminder = _build_direct_experience_reminder(
            content=direct_experience_content,
            name=direct_experience_name,
            uri=direct_experience_uri,
        )
        _insert_experience_reminder_message(messages, direct_experience_reminder)
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
    runtime_messages = list(getattr(result, "messages", []) or [])
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
        runtime_messages,
    )


def _build_direct_experience_reminder(
    *,
    content: str | None,
    name: str | None = None,
    uri: str | None = None,
) -> str:
    """Render request-provided experience text as the standard reminder message."""

    exp_content = str(content or "").strip()
    if not exp_content:
        raise ValueError(
            "direct_experience_content is required when loader_mode='direct_experience'"
        )
    exp_name = _safe_direct_experience_name(name)
    exp_uri = str(uri or "").strip() or (
        f"direct://experience/{_slug_direct_experience_name(exp_name)}"
    )
    return "\n".join(
        [
            "[Experience Reminder]",
            "## Relevant Agent Experience",
            "",
            f"### {exp_name}",
            "",
            f"Experience URI: `{exp_uri}`",
            "",
            exp_content,
        ]
    ).rstrip()


def _insert_experience_reminder_message(
    messages: list[dict[str, Any]],
    reminder_content: str,
) -> None:
    """Insert a reminder after leading system messages and before the current user task."""

    insert_at = 0
    while insert_at < len(messages):
        raw = messages[insert_at]
        if not isinstance(raw, dict) or raw.get("role") != "system":
            break
        insert_at += 1
    messages.insert(insert_at, {"role": "user", "content": reminder_content})


def _safe_direct_experience_name(name: str | None) -> str:
    text = re.sub(r"\s+", " ", str(name or "").strip())
    return text or "direct_experience"


def _slug_direct_experience_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "").strip()).strip("_.-")
    return slug[:80] or "direct_experience"


def _direct_experience_metadata(
    *,
    content: str | None,
    name: str | None,
    uri: str | None,
    enabled: bool,
) -> dict[str, Any] | None:
    if not enabled:
        return None
    content_text = str(content or "")
    exp_name = _safe_direct_experience_name(name)
    return {
        "name": exp_name,
        "uri": str(uri or "").strip()
        or f"direct://experience/{_slug_direct_experience_name(exp_name)}",
        "content_chars": len(content_text),
        "content_sha256": sha256(content_text.encode("utf-8")).hexdigest(),
    }


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
    runtime_messages: list[dict[str, Any]] | None = None,
    artifact_created_at: str | None = None,
) -> list[Message]:
    del system_prompt, user_prompt, tools_used, experience_reminder
    if not runtime_messages:
        raise ValueError(
            "runtime_messages are required; tau2 artifacts must use AgentLoop messages"
        )
    return _build_rollout_messages_from_runtime(
        runtime_messages,
        final_content=final_content,
        evaluation_result=evaluation_result,
        reward=reward,
        artifact_created_at=artifact_created_at,
    )


def _build_rollout_messages_from_runtime(
    runtime_messages: list[dict[str, Any]],
    *,
    final_content: str | None,
    evaluation_result: Any,
    reward: Any,
    artifact_created_at: str | None = None,
) -> list[Message]:
    """Convert AgentLoop's real runtime messages into rollout artifact messages.

    AgentLoop runtime messages are the source of truth for dynamic user-message
    injections such as experience constraints.  The conversion keeps natural
    user/assistant text in order and renders provider tool-result messages as
    completed OpenViking ToolPart entries.
    """

    messages: list[Message] = []
    tool_inputs_by_id: dict[str, dict[str, Any]] = {}
    for raw in runtime_messages or []:
        if not isinstance(raw, dict):
            continue
        for call in raw.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if call_id:
                tool_inputs_by_id[call_id] = _as_tool_input(function.get("arguments", {}))

    for idx, raw in enumerate(runtime_messages or []):
        msg = _runtime_message_to_rollout_message(
            raw,
            idx=idx,
            tool_inputs_by_id=tool_inputs_by_id,
            artifact_created_at=artifact_created_at,
        )
        if msg is not None:
            messages.append(msg)

    if final_content and str(final_content).strip():
        final_text = str(final_content)
        if not any(
            message.role == "assistant" and message.content == final_text for message in messages
        ):
            messages.append(
                _message("tau2-final", "assistant", final_text, created_at=artifact_created_at)
            )

    del reward, evaluation_result
    return messages


def _runtime_message_to_rollout_message(
    raw: dict[str, Any],
    *,
    idx: int,
    tool_inputs_by_id: dict[str, dict[str, Any]],
    artifact_created_at: str | None,
) -> Message | None:
    role = str(raw.get("role") or "user")
    content = raw.get("content")

    if role == "system":
        return _message(
            f"tau2-runtime-{idx}",
            "user",
            f"system:\n{_runtime_content_to_text(content)}",
            created_at=artifact_created_at,
        )

    if role == "tool":
        tool_call_id = str(raw.get("tool_call_id") or f"tau2-runtime-tool-{idx}")
        tool_name = str(raw.get("name") or raw.get("tool_name") or "unknown")
        return Message(
            id=f"tau2-runtime-{idx}",
            role="user",
            parts=[
                ToolPart(
                    tool_id=tool_call_id,
                    tool_name=tool_name,
                    tool_input=tool_inputs_by_id.get(tool_call_id, {}),
                    tool_output=_runtime_content_to_text(content),
                    tool_status="completed",
                )
            ],
            created_at=artifact_created_at,
        )

    if role not in {"user", "assistant"}:
        role = "user"

    # Assistant tool-call messages are represented by the following completed
    # tool result message.  Keep assistant natural-language content only.
    if raw.get("tool_calls") and not str(content or "").strip():
        return None

    text = _runtime_content_to_text(content)
    if not text.strip():
        return None
    return Message(
        id=f"tau2-runtime-{idx}",
        role=role,  # type: ignore[arg-type]
        parts=[TextPart(text=text)],
        created_at=artifact_created_at,
    )


def _runtime_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
            else:
                texts.append(_stringify(item))
        return "\n".join(text for text in texts if text)
    return _stringify(content)


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
