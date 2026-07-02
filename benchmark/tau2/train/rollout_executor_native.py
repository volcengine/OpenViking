#!/usr/bin/env python3
"""TAU-2 native RolloutExecutor implementation for batch policy training."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
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
from openviking.message import Message, TextPart, ToolPart
from openviking.session.train import (
    Case,
    ExecutionContext,
    ExperienceSet,
    Rollout,
    RubricEvaluation,
)
from openviking.session.train.components.progress import ProgressPrinter
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _progress_stage_label(stage: Any, *, default: str) -> str:
    stage_text = str(stage or "")
    stage_name = stage_text.split(maxsplit=1)[0]
    if stage_name.endswith("_rollout"):
        return f"{stage_name}_start"
    if stage_name.endswith("_rollout_start"):
        return stage_name
    return default


AGENT_NAME_PREFIX = "openviking_native_memory_agent"
_NATIVE_AGENT_CONFIGS: dict[str, "NativeTau2RolloutExecutor"] = {}
WRITE_TOOL_PREFIXES = (
    "toggle_",
    "enable_",
    "disable_",
    "set_",
    "reset_",
    "update_",
    "modify_",
    "cancel_",
    "book_",
    "exchange_",
    "return_",
    "grant_",
    "reboot_",
)


@dataclass(slots=True)
class NativeTau2RolloutExecutor:
    """Execute tau2 cases through TAU-2's native orchestrator and agent APIs."""

    agent_llm: str | None = None
    user_llm: str | None = None
    agent_llm_args: dict[str, Any] = field(default_factory=dict)
    user_llm_args: dict[str, Any] = field(default_factory=dict)
    base_agent: str = "llm_agent"
    user: str = "user_simulator"
    max_steps: int = 200
    max_errors: int = 10
    concurrency: int = 20
    seed: int = 300
    retrieval_mode: str = "first_user_prewrite"
    search_uri: str = "viking://user/memories/experiences"
    retrieval_top_k: int = 4
    first_user_retrieval_top_k: int | None = None
    first_user_inject_top_k: int | None = None
    prewrite_retrieval_top_k: int | None = None
    prewrite_inject_top_k: int | None = None
    memory_inject_max_chars: int | None = None
    first_user_memory_inject_max_chars: int | None = None
    prewrite_memory_inject_max_chars: int | None = None
    openviking_url: str | None = None
    openviking_api_key: str | None = None
    openviking_account: str | None = None
    openviking_user: str | None = None
    openviking_timeout: float = 600.0
    memory_enabled: bool = True
    scope_prompt: str = ""
    rollout_language: str = "default"
    log_timings: bool = True
    show_progress: bool = False
    progress_label: str = "tau2"

    def __post_init__(self) -> None:
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be > 0")
        if self.max_errors <= 0:
            raise ValueError("max_errors must be > 0")
        if self.retrieval_top_k <= 0:
            raise ValueError("retrieval_top_k must be > 0")
        if self.retrieval_mode not in {"first_user", "prewrite", "first_user_prewrite"}:
            raise ValueError("retrieval_mode must be first_user, prewrite, or first_user_prewrite")
        if self.rollout_language not in {"default", "zh"}:
            raise ValueError("rollout_language must be 'default' or 'zh'")
        for name in (
            "memory_inject_max_chars",
            "first_user_memory_inject_max_chars",
            "prewrite_memory_inject_max_chars",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        self.first_user_retrieval_top_k = self.first_user_retrieval_top_k or self.retrieval_top_k
        self.first_user_inject_top_k = (
            self.first_user_inject_top_k or self.first_user_retrieval_top_k
        )
        self.prewrite_retrieval_top_k = self.prewrite_retrieval_top_k or self.retrieval_top_k
        self.prewrite_inject_top_k = self.prewrite_inject_top_k or self.prewrite_retrieval_top_k
        if self.first_user_memory_inject_max_chars is None:
            self.first_user_memory_inject_max_chars = self.memory_inject_max_chars
        if self.prewrite_memory_inject_max_chars is None:
            self.prewrite_memory_inject_max_chars = self.memory_inject_max_chars

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        self._sync_openviking_options(policy_set)
        progress = ProgressPrinter(
            total=len(cases),
            label=_progress_stage_label(context.metadata.get("stage"), default=self.progress_label),
            enabled=self.show_progress,
            description=f"Running {len(cases)} tau2 native rollouts, concurrency={self.concurrency}",
        )
        progress.render()
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(index: int, case: Case) -> Rollout:
            async with semaphore:
                progress.start_one()
                try:
                    rollout = await asyncio.to_thread(self._execute_one_sync, case, context, index)
                    progress.complete_one()
                    return rollout
                except Exception:
                    progress.fail_one()
                    raise

        try:
            return list(
                await asyncio.gather(*(run_one(index, case) for index, case in enumerate(cases)))
            )
        finally:
            progress.finish()

    def _sync_openviking_options(self, policy_set: ExperienceSet) -> None:
        metadata = dict(policy_set.metadata or {})
        self.openviking_url = self.openviking_url or _optional_metadata_str(
            metadata, "openviking_url", "server_url"
        )
        self.openviking_api_key = self.openviking_api_key or _optional_metadata_str(
            metadata, "openviking_api_key", "api_key"
        )
        self.openviking_account = self.openviking_account or _optional_metadata_str(
            metadata, "openviking_account", "account_id", "account"
        )
        self.openviking_user = self.openviking_user or _optional_metadata_str(
            metadata, "openviking_user", "user_id", "user"
        )

    def _execute_one_sync(self, case: Case, context: ExecutionContext, case_index: int) -> Rollout:
        started_at = time.perf_counter()
        domain = str(case.input["domain"])
        task_id = str(case.input["task_id"])
        task_no = int(case.input["task_no"])
        data_split = str(case.input["data_split"])
        trial = _case_trial(case)
        seed = _case_seed(self.seed, case_index=case_index, eval_trial=trial)

        _ensure_tau2_llm_api_bases()

        from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
        from tau2.registry import registry
        from tau2.run import run_task

        llm_agent, llm_args_agent, llm_user, llm_args_user = _resolve_llm_runtime_config(self)
        tasks = registry.get_tasks_loader(domain)()
        task_by_id = {str(task.id): task for task in tasks}
        try:
            task = task_by_id[task_id]
        except KeyError as exc:
            raise ValueError(f"tau2 task not found domain={domain} task_id={task_id}") from exc

        agent_name = self.base_agent
        if self.memory_enabled:
            agent_name = _register_native_memory_agent(self)

        simulation = run_task(
            domain=domain,
            task=task,
            agent=agent_name,
            user=self.user,
            llm_agent=llm_agent,
            llm_args_agent=llm_args_agent,
            llm_user=llm_user,
            llm_args_user=llm_args_user,
            max_steps=self.max_steps,
            max_errors=self.max_errors,
            evaluation_type=EvaluationType.ALL,
            seed=seed,
        )
        reward_info = simulation.reward_info
        if reward_info is None:
            reward_info = evaluate_simulation(
                domain=domain,
                task=task,
                simulation=simulation,
                evaluation_type=EvaluationType.ALL,
                solo_mode=False,
            )
            simulation.reward_info = reward_info
        reward = _to_jsonable(getattr(reward_info, "reward", 0.0))
        evaluation_result = _to_jsonable(reward_info)
        messages = _build_rollout_messages_from_simulation(
            simulation=simulation,
            reward=reward,
            evaluation_result=evaluation_result,
        )
        memory_context = _memory_context_from_simulation(simulation)
        rollout = Rollout(
            case=case,
            messages=messages,
            policy_snapshot_id=context.policy_snapshot_id,
            evaluation=_tau2_evaluation(reward=reward, evaluation_result=evaluation_result),
            metadata={
                "rollout_backend": "native",
                "domain": domain,
                "data_split": data_split,
                "task_no": task_no,
                "task_id": task_id,
                "eval_trial": case.input.get("eval_trial"),
                "eval_trial_count": case.input.get("eval_trial_count"),
                "train_trial": case.input.get("train_trial"),
                "train_trial_count": case.input.get("train_trial_count"),
                "original_case_name": case.input.get("original_case_name"),
                "seed": seed,
                "reward": reward,
                "evaluation_result": evaluation_result,
                "termination_reason": getattr(simulation, "termination_reason", None),
                "duration": getattr(simulation, "duration", None),
                "agent_cost": getattr(simulation, "agent_cost", None),
                "user_cost": getattr(simulation, "user_cost", None),
                "tools_used": _tool_usage_from_simulation(simulation),
                "memory": memory_context,
                "memory_enabled": self.memory_enabled,
                "retrieval_mode": self.retrieval_mode if self.memory_enabled else None,
                "search_uri": self.search_uri if self.memory_enabled else None,
                "execution_metadata": dict(context.metadata),
            },
        )
        if self.log_timings:
            logger.info(
                "tau2 native rollout timing case=%s total_ms=%.1f task_id=%s task_no=%s "
                "split=%s reward=%s message_count=%s",
                case.name,
                (time.perf_counter() - started_at) * 1000.0,
                task_id,
                task_no,
                data_split,
                reward,
                len(rollout.messages),
            )
        return rollout


def _resolve_llm_runtime_config(
    executor: NativeTau2RolloutExecutor,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    """Resolve tau2 native LLM settings for direct ``run_task`` calls.

    ``tau2.run.run_task`` does not apply ``RunConfig`` defaults. Passing
    ``None`` through leaves ``LLMAgent.llm`` unset, and the orchestrator then
    fails during ``set_seed`` before the first model call. Mirror tau2's
    RunConfig defaults here while still letting request options and env vars
    override the model names.
    """

    import os
    from copy import deepcopy

    from tau2.config import (
        DEFAULT_LLM_AGENT,
        DEFAULT_LLM_ARGS_AGENT,
        DEFAULT_LLM_ARGS_USER,
        DEFAULT_LLM_USER,
    )

    llm_agent = _first_non_empty(
        executor.agent_llm,
        os.getenv("TAU2_AGENT_LLM"),
        DEFAULT_LLM_AGENT,
        name="agent_llm",
    )
    llm_user = _first_non_empty(
        executor.user_llm,
        os.getenv("TAU2_USER_LLM"),
        DEFAULT_LLM_USER,
        name="user_llm",
    )
    llm_args_agent = deepcopy(DEFAULT_LLM_ARGS_AGENT or {})
    llm_args_agent.update(dict(executor.agent_llm_args or {}))
    llm_args_user = deepcopy(DEFAULT_LLM_ARGS_USER or {})
    llm_args_user.update(dict(executor.user_llm_args or {}))
    return llm_agent, llm_args_agent, llm_user, llm_args_user


def _first_non_empty(*values: Any, name: str) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    raise ValueError(f"{name} must be set for tau2 native rollout")


def _ensure_tau2_llm_api_bases() -> None:
    import os

    base_url = (
        os.environ.get("OPENAI_API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("ARK_BASE_URL")
    )
    if not base_url:
        return
    os.environ.setdefault("OPENAI_API_BASE", base_url)
    os.environ.setdefault("OPENAI_BASE_URL", base_url)
    os.environ.setdefault("AGENT_API_BASE", base_url)
    os.environ.setdefault("USER_API_BASE", base_url)


def _optional_metadata_str(metadata: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _register_native_memory_agent(executor: NativeTau2RolloutExecutor) -> str:
    from tau2.agent.llm_agent import LLMAgent, LLMAgentState
    from tau2.data_model.message import (
        AssistantMessage,
        MultiToolMessage,
        SystemMessage,
        UserMessage,
    )
    from tau2.registry import registry
    from tau2.utils.llm_utils import generate

    agent_name = _native_agent_name(executor)
    _NATIVE_AGENT_CONFIGS[agent_name] = executor
    if agent_name in registry.get_agents():
        return agent_name

    class OpenVikingNativeMemoryAgent(LLMAgent):
        @property
        def _executor(self) -> NativeTau2RolloutExecutor:
            return _NATIVE_AGENT_CONFIGS[agent_name]

        def get_init_state(self, message_history=None):
            executor_config = self._executor
            state = super().get_init_state(message_history)
            self._openviking_memory_contexts: list[str] = []
            if executor_config.scope_prompt:
                state.system_messages.append(
                    SystemMessage(role="system", content=executor_config.scope_prompt)
                )
            if executor_config.rollout_language == "zh":
                state.system_messages.append(
                    SystemMessage(
                        role="system",
                        content=(
                            "Communicate with the user and write final responses in Chinese. "
                            "Do not translate tool names, identifiers, JSON field names, "
                            "reservation IDs, flight numbers, or other structured values."
                        ),
                    )
                )
            return state

        def _retrieve(
            self,
            query: str,
            *,
            search_limit: int,
            inject_limit: int,
            inject_max_chars: int | None = None,
            exclude_uris: set[str] | None = None,
        ) -> tuple[str, list[dict[str, Any]], set[str]]:
            """Retrieve and rank memories.

            Returns:
                block: joined text of injected memories
                rows: detail rows for each match
                injected_uris: set of URIs that were actually injected
            """
            executor_config = self._executor
            client = _client(executor_config)
            rows: list[dict[str, Any]] = []
            injected_uris: set[str] = set()
            try:
                result = client.search(
                    query=query,
                    target_uri=executor_config.search_uri,
                    limit=search_limit,
                )
                memories = list(getattr(result, "memories", []) or [])
                # URI deduplication: keep the highest-scoring match per URI
                deduped: dict[str, Any] = {}
                for match in memories[:search_limit]:
                    uri = getattr(match, "uri", "")
                    if not uri:
                        continue
                    if exclude_uris and uri in exclude_uris:
                        continue
                    if uri in deduped:
                        prev_score = getattr(deduped[uri], "score", 0) or 0
                        curr_score = getattr(match, "score", 0) or 0
                        if curr_score <= prev_score:
                            continue
                    deduped[uri] = match
                deduped_memories = sorted(
                    deduped.values(),
                    key=lambda m: getattr(m, "score", 0) or 0,
                    reverse=True,
                )

                blocks: list[str] = []
                injected_chars_used = 0
                for index, match in enumerate(deduped_memories, 1):
                    uri = getattr(match, "uri", "")
                    text, read_error = _read_memory_text(client, match)
                    clean_text = text.strip()
                    block_text = (
                        f"Memory {index} ({uri}):\n{clean_text}" if clean_text else ""
                    )
                    block_chars = len(block_text)
                    budget_used_before = injected_chars_used
                    budget_dropped = False
                    truncated = False
                    injected = index <= inject_limit and bool(block_text)
                    if injected and inject_max_chars is not None:
                        remaining = inject_max_chars - injected_chars_used
                        if remaining <= 0:
                            injected = False
                            budget_dropped = True
                        elif block_chars > remaining:
                            if not blocks:
                                block_text = block_text[:remaining]
                                block_chars = len(block_text)
                                truncated = True
                            else:
                                injected = False
                                budget_dropped = True
                    if injected:
                        injected_chars_used += block_chars
                        injected_uris.add(uri)
                    row = {
                        "uri": uri,
                        "score": getattr(match, "score", None),
                        "level": getattr(match, "level", None),
                        "text_chars": len(text),
                        "block_chars": block_chars,
                        "injected": injected,
                        "inject_max_chars": inject_max_chars,
                        "inject_budget_used_before": budget_used_before,
                        "inject_budget_used_after": injected_chars_used,
                        "inject_budget_dropped": budget_dropped,
                        "inject_budget_truncated": truncated,
                    }
                    if read_error:
                        row["read_error"] = read_error
                    rows.append(row)
                    if injected:
                        blocks.append(block_text)
                return "\n\n".join(blocks), rows, injected_uris
            finally:
                client.close()

        def _generate(self, messages):
            def _is_empty_assistant(response: Any) -> bool:
                content = str(getattr(response, "content", "") or "")
                tool_calls = getattr(response, "tool_calls", None) or []
                return not content.strip() and not tool_calls

            try:
                response = generate(
                    model=self.llm,
                    tools=self.tools,
                    messages=messages,
                    is_agent=True,
                    **self.llm_args,
                )
                if not _is_empty_assistant(response):
                    return response
            except json.JSONDecodeError:
                retry_prompt = (
                    "Retry the last assistant step once. If you call a tool, "
                    "the tool arguments must be syntactically valid JSON."
                )
            else:
                retry_prompt = (
                    "Retry the last assistant step once. Return either a useful natural "
                    "language response or a valid tool call; do not return an empty assistant message."
                )
            try:
                response = generate(
                    model=self.llm,
                    tools=self.tools,
                    messages=messages + [SystemMessage(role="system", content=retry_prompt)],
                    is_agent=True,
                    **self.llm_args,
                )
                if not _is_empty_assistant(response):
                    return response
                return AssistantMessage(
                    role="assistant",
                    content="I need to continue with the available task information.",
                    raw_data={"openviking_memory_agent_error": "empty_assistant_message"},
                )
            except json.JSONDecodeError as exc:
                return AssistantMessage(
                    role="assistant",
                    content="I need to continue with the available task information.",
                    raw_data={
                        "openviking_memory_agent_error": "invalid_tool_call_json",
                        "error": str(exc),
                    },
                )

        def generate_next_message(self, message, state: LLMAgentState):
            executor_config = self._executor
            if isinstance(message, MultiToolMessage):
                state.messages.extend(message.tool_messages)
            else:
                state.messages.append(message)

            role = getattr(message, "role", "")
            role_value = getattr(role, "value", role)
            is_first_user = (
                executor_config.retrieval_mode in {"first_user", "first_user_prewrite"}
                and str(role_value) == "user"
                and not getattr(self, "_first_user_memory_injected", False)
            )
            injected_uris: set[str] = getattr(self, "_injected_memory_uris", set())

            if is_first_user:
                query = str(getattr(message, "content", "") or "")
                block, matches, new_injected = self._retrieve(
                    query,
                    search_limit=int(
                        executor_config.first_user_retrieval_top_k
                        or executor_config.retrieval_top_k
                    ),
                    inject_limit=int(
                        executor_config.first_user_inject_top_k or executor_config.retrieval_top_k
                    ),
                    inject_max_chars=executor_config.first_user_memory_inject_max_chars,
                    exclude_uris=injected_uris,
                )
                self._first_user_memory_injected = True
                if block:
                    injected_uris.update(new_injected)
                    self._injected_memory_uris = injected_uris
                    # Prepend experience reminder to the user message content
                    # so it becomes part of the conversation history (visible in messages.json)
                    reminder_prefix = (
                        "[Experience Reminder]\n"
                        "## Relevant Agent Experience\n\n"
                        + block
                        + "\n\n---\n\n"
                    )
                    message.content = reminder_prefix + str(message.content or "")
                    self._openviking_memory_contexts.append(block)

            assistant_message = self._generate(state.system_messages + state.messages)
            if executor_config.retrieval_mode in {"prewrite", "first_user_prewrite"}:
                tool_calls = list(getattr(assistant_message, "tool_calls", None) or [])
                write_calls = [call for call in tool_calls if _is_write_tool_call(call)]
                if write_calls:
                    query = _tool_call_query(write_calls, state.messages)
                    block, matches, new_injected = self._retrieve(
                        query,
                        search_limit=int(
                            executor_config.prewrite_retrieval_top_k
                            or executor_config.retrieval_top_k
                        ),
                        inject_limit=int(
                            executor_config.prewrite_inject_top_k or executor_config.retrieval_top_k
                        ),
                        inject_max_chars=executor_config.prewrite_memory_inject_max_chars,
                        exclude_uris=injected_uris,
                    )
                    if block:
                        injected_uris.update(new_injected)
                        self._injected_memory_uris = injected_uris
                        self._openviking_memory_contexts.append(block)
                        reminder_content = (
                            "[Experience Reminder]\n"
                            "## Relevant Agent Experience (before write action)\n\n"
                            + block
                        )
                        # Inject as a user message so it's part of the conversation history
                        state.messages.append(UserMessage(role="user", content=reminder_content))
                        assistant_message = self._generate(
                            state.system_messages + state.messages
                        )
            contexts = list(getattr(self, "_openviking_memory_contexts", []) or [])
            if contexts:
                raw_data = dict(getattr(assistant_message, "raw_data", None) or {})
                raw_data["openviking_memory_context"] = "\n\n".join(contexts)
                assistant_message.raw_data = raw_data
            state.messages.append(assistant_message)
            return assistant_message, state

    registry.register_agent(OpenVikingNativeMemoryAgent, agent_name)
    return agent_name


def _native_agent_name(executor: NativeTau2RolloutExecutor) -> str:
    import hashlib

    payload = {
        "retrieval_mode": executor.retrieval_mode,
        "search_uri": executor.search_uri,
        "first_user_retrieval_top_k": executor.first_user_retrieval_top_k,
        "first_user_inject_top_k": executor.first_user_inject_top_k,
        "prewrite_retrieval_top_k": executor.prewrite_retrieval_top_k,
        "prewrite_inject_top_k": executor.prewrite_inject_top_k,
        "memory_inject_max_chars": executor.memory_inject_max_chars,
        "first_user_memory_inject_max_chars": executor.first_user_memory_inject_max_chars,
        "prewrite_memory_inject_max_chars": executor.prewrite_memory_inject_max_chars,
        "openviking_url_set": bool(executor.openviking_url),
        "openviking_api_key_set": bool(executor.openviking_api_key),
        "openviking_account": executor.openviking_account,
        "openviking_user": executor.openviking_user,
        "openviking_timeout": executor.openviking_timeout,
        "scope_prompt": executor.scope_prompt,
        "rollout_language": executor.rollout_language,
    }
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"{AGENT_NAME_PREFIX}_{digest}"


def _client(executor: NativeTau2RolloutExecutor):
    import openviking as ov

    client = ov.SyncHTTPClient(
        url=executor.openviking_url,
        api_key=executor.openviking_api_key,
        account=executor.openviking_account,
        user=executor.openviking_user,
        timeout=executor.openviking_timeout,
        extra_headers={},
        profile_enabled=False,
    )
    client.initialize()
    return client


def _read_memory_text(client: Any, match: Any) -> tuple[str, str | None]:
    try:
        return client.read(getattr(match, "uri", "")), None
    except Exception as exc:
        fallback = getattr(match, "abstract", "") or getattr(match, "overview", "") or ""
        return fallback, f"{type(exc).__name__}: {exc}"


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or tool_call.get("function", {}).get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


def _tool_call_arguments(tool_call: Any) -> Any:
    if isinstance(tool_call, dict):
        return tool_call.get("arguments") or tool_call.get("function", {}).get("arguments") or {}
    return getattr(tool_call, "arguments", {}) or {}


def _is_write_tool_call(tool_call: Any) -> bool:
    name = _tool_call_name(tool_call)
    return bool(name) and name.startswith(WRITE_TOOL_PREFIXES)


def _tool_call_query(tool_calls: list[Any], state_messages: list[Any]) -> str:
    rendered = []
    for call in tool_calls:
        rendered.append(
            f"{_tool_call_name(call) or 'unknown_tool'}("
            f"{json.dumps(_tool_call_arguments(call), ensure_ascii=False, sort_keys=True, default=str)}"
            ")"
        )
    recent_user = [
        str(getattr(message, "content", "") or "")
        for message in state_messages[-8:]
        if str(getattr(message, "role", "")) == "user"
        and str(getattr(message, "content", "") or "").strip()
    ]
    recent_observations = [
        str(getattr(message, "content", "") or "")[:600]
        for message in state_messages[-12:]
        if str(getattr(message, "role", "")) == "tool"
        and str(getattr(message, "content", "") or "").strip()
    ]
    parts = [
        "Before executing write-like tool call(s): " + "; ".join(rendered),
        "Recent user context: " + " | ".join(recent_user[-3:]),
    ]
    if recent_observations:
        parts.append("Recent tool observations: " + " | ".join(recent_observations[-4:]))
    return "\n".join(parts)


def _case_seed(base_seed: int, *, case_index: int, eval_trial: Any) -> int:
    trial = 0
    try:
        if eval_trial is not None:
            trial = int(eval_trial)
    except (TypeError, ValueError):
        trial = 0
    return int(base_seed) + case_index + trial * 100_000


def _build_rollout_messages_from_simulation(
    *,
    simulation: Any,
    reward: Any,
    evaluation_result: Any,
) -> list[Message]:
    messages: list[Message] = []
    pending_tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for index, message in enumerate(getattr(simulation, "messages", []) or []):
        converted = _simulation_message_to_rollout_messages(
            message,
            index,
            pending_tool_calls=pending_tool_calls,
        )
        messages.extend(converted)
    for call_id, (tool_name, tool_input) in pending_tool_calls.items():
        messages.append(
            Message(
                id=f"tau2-tool-pending-{index if 'index' in locals() else 0}-{len(messages)}",
                role="assistant",
                parts=[
                    ToolPart(
                        tool_id=call_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_status="running",
                    )
                ],
            )
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
        )
    )
    return messages


def _simulation_message_to_rollout_messages(
    message: Any,
    index: int,
    *,
    pending_tool_calls: dict[str, tuple[str, dict[str, Any]]] | None = None,
) -> list[Message]:
    role = _role_value(getattr(message, "role", "assistant"))
    if role == "system":
        content = str(getattr(message, "content", "") or "")
        return [
            _metadata_message(
                f"tau2-system-{index}",
                f"system:\n{content}",
            )
        ]
    if role in {"user", "assistant"}:
        content = str(getattr(message, "content", "") or "")
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if tool_calls:
            rows = []
            for call_idx, call in enumerate(tool_calls):
                call_id = str(getattr(call, "id", "") or f"tau2-tool-{index}-{call_idx}")
                tool_name = _tool_call_name(call)
                tool_input = _as_tool_input(_tool_call_arguments(call))
                if _is_communicate_with_user(tool_name):
                    assistant_text = _communicate_text_from_tool_input(tool_input)
                    if assistant_text.strip():
                        rows.append(
                            Message(
                                id=f"tau2-communicate-assistant-{index}-{call_idx}",
                                role="assistant",
                                parts=[TextPart(text=assistant_text)],
                                created_at=getattr(message, "timestamp", None),
                            )
                        )
                    if pending_tool_calls is not None:
                        pending_tool_calls[call_id] = (tool_name, tool_input)
                    continue
                if pending_tool_calls is not None:
                    pending_tool_calls[call_id] = (tool_name, tool_input)
                    continue
                rows.append(
                    Message(
                        id=f"tau2-tool-call-{index}-{call_idx}",
                        role="assistant" if role == "assistant" else "user",
                        parts=[
                            ToolPart(
                                tool_id=call_id,
                                tool_name=tool_name,
                                tool_input=tool_input,
                                tool_status="running",
                            )
                        ],
                        created_at=getattr(message, "timestamp", None),
                    )
                )
            return rows
        return [
            Message(
                id=f"tau2-{role}-{index}",
                role="user" if role == "user" else "assistant",
                parts=[TextPart(text=content)],
                created_at=getattr(message, "timestamp", None),
            )
        ]
    if role == "tool":
        call_id = str(getattr(message, "id", "") or f"tau2-tool-{index}")
        pending = pending_tool_calls.pop(call_id, None) if pending_tool_calls is not None else None
        tool_name, tool_input = pending if pending is not None else ("unknown", None)
        output = str(getattr(message, "content", "") or "")
        if _is_communicate_with_user(tool_name):
            if not output.strip():
                return []
            return [
                Message(
                    id=f"tau2-communicate-user-{index}",
                    role="user",
                    parts=[TextPart(text=output)],
                    created_at=getattr(message, "timestamp", None),
                )
            ]
        return [
            Message(
                id=f"tau2-tool-result-{index}",
                role="user",
                parts=[
                    ToolPart(
                        tool_id=call_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_output=output,
                        tool_status="error"
                        if bool(getattr(message, "error", False))
                        else "completed",
                    )
                ],
                created_at=getattr(message, "timestamp", None),
            )
        ]
    content = str(getattr(message, "content", "") or "")
    return [_message(f"tau2-message-{index}", "assistant", content)]


def _tool_usage_from_simulation(simulation: Any) -> list[dict[str, Any]]:
    usages: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    for message in getattr(simulation, "messages", []) or []:
        role = _role_value(getattr(message, "role", ""))
        if role in {"user", "assistant"}:
            for call in list(getattr(message, "tool_calls", None) or []):
                call_id = str(getattr(call, "id", "") or f"call_{len(usages)}")
                row = {
                    "tool_name": _tool_call_name(call),
                    "args": _tool_call_arguments(call),
                    "requestor": getattr(call, "requestor", role),
                }
                pending[call_id] = row
                usages.append(row)
        elif role == "tool":
            call_id = str(getattr(message, "id", "") or "")
            row = pending.get(call_id)
            if row is not None:
                row["result"] = getattr(message, "content", None)
                row["error"] = bool(getattr(message, "error", False))
    return usages


def _memory_context_from_simulation(simulation: Any) -> str | None:
    blocks = []
    for message in getattr(simulation, "messages", []) or []:
        raw_data = getattr(message, "raw_data", None)
        if isinstance(raw_data, dict) and raw_data.get("openviking_memory_context"):
            blocks.append(str(raw_data["openviking_memory_context"]))
    return "\n\n".join(blocks) if blocks else None


def _role_value(role: Any) -> str:
    return str(getattr(role, "value", role))


def _tau2_evaluation(*, reward: Any, evaluation_result: Any) -> RubricEvaluation:
    return _tau2_evaluation_helper(
        reward=reward, evaluation_result=evaluation_result, source="tau2_native_executor"
    )
