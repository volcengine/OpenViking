#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from tau2_common import assert_tau2_results_complete, normalize_litellm_env

AGENT_NAME = "openviking_memory_agent"
REPO_ROOT = Path(__file__).resolve().parents[4]
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
FIXED_FIRST_USER_NAME = "openviking_fixed_first_user_simulator"
TRAIN_TRANSCRIPT_OPENVIKING_TEXT = "openviking_text"
TRAIN_TRANSCRIPT_ROLE_TOOL_BLOCKS = "role_tool_blocks"
TRAIN_TRANSCRIPT_CUSTOM_LIKE = "custom_like"
DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS = 5000


def _json(text: str) -> dict[str, Any]:
    return json.loads(text) if text else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _add_tau2_to_path(tau2_repo: Path) -> None:
    src = tau2_repo / "src"
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(src if src.is_dir() else tau2_repo))


def _load_domain_policy(tau2_repo: Path, domain: str) -> str:
    path = tau2_repo / "data" / "tau2" / "domains" / domain / "policy.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _build_system_prompt(policy: str) -> str:
    return (
        "You are a customer service representative for a benchmark evaluation.\n"
        "Help customers strictly according to the policy below.\n\n"
        "You have two kinds of tools:\n"
        "1. Memory/context tools available to the benchmark agent.\n"
        "2. Business system tools executed by the TAU-2 environment.\n\n"
        "Rules for business system tools:\n"
        "- Call ONE tool at a time. Wait for the result before calling the next tool.\n"
        "- Only pass arguments the customer explicitly provided. Do NOT infer or add optional arguments.\n"
        "- After receiving a tool result, use it to continue helping the customer.\n\n"
        "## Policy\n"
        f"{policy}\n\n"
        "Now wait for the customer to start. Follow the policy exactly."
    )


def _patch_tau2_auxiliary_llm_defaults(llm: str, llm_args: dict[str, Any]) -> None:
    # TAU-2 exposes agent/user LLMs in TextRunConfig, but NL assertion scoring
    # still reads module defaults. Keep the evaluator on the same configured
    # model so benchmark runs do not fall back to inaccessible upstream defaults.
    patches = {
        "DEFAULT_LLM_NL_ASSERTIONS": llm,
        "DEFAULT_LLM_NL_ASSERTIONS_ARGS": deepcopy(llm_args),
        "DEFAULT_LLM_ENV_INTERFACE": llm,
        "DEFAULT_LLM_ENV_INTERFACE_ARGS": deepcopy(llm_args),
    }
    for module_name in (
        "tau2.config",
        "tau2.evaluator.evaluator_nl_assertions",
        "tau2.environment.utils.interface_agent",
    ):
        module = importlib.import_module(module_name)
        for name, value in patches.items():
            if hasattr(module, name):
                setattr(module, name, deepcopy(value))


def _save_to_arg(path: Path) -> str:
    # Some TAU-2 versions append ".json"; newer versions treat save_to as a
    # run directory and write results.json under it.
    return str(path.with_suffix("") if path.suffix == ".json" else path)


def _compat_results_path(path: Path) -> Path:
    run_dir = path.with_suffix("") if path.suffix == ".json" else path
    return run_dir / "results.json"


def _reward(sim: dict[str, Any]) -> float:
    info = sim.get("reward_info") or {}
    value = info.get("reward", sim.get("reward", 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _db_match(sim: dict[str, Any]) -> bool | None:
    info = sim.get("reward_info") or {}
    db = info.get("db_check") or {}
    if isinstance(db, dict):
        if "score" in db:
            return bool(db["score"])
        if "db_match" in db:
            return bool(db["db_match"])
    return sim.get("db_match")


def _task_success(sim: dict[str, Any]) -> bool:
    return _reward(sim) >= 1.0


def _metrics(results_path: Path) -> dict[str, Any]:
    data = json.loads(results_path.read_text())
    sims = data.get("simulations") or []
    rewards = [_reward(sim) for sim in sims]
    db_values = [_db_match(sim) for sim in sims]
    db_known = [value for value in db_values if value is not None]
    return {
        "simulation_count": len(sims),
        "avg_reward": sum(rewards) / len(rewards) if rewards else 0.0,
        "db_match_rate": (sum(1 for value in db_known if value) / len(db_known))
        if db_known
        else None,
    }


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
            f"{json.dumps(_tool_call_arguments(call), ensure_ascii=False, sort_keys=True)}"
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


def _tool_call_id(tool_call: dict[str, Any]) -> str:
    return str(tool_call.get("id") or tool_call.get("tool_call_id") or "").strip()


def _tool_result_call_id(message: dict[str, Any]) -> str:
    return str(message.get("id") or message.get("tool_call_id") or "").strip()


def _compact_train_tool_output(content: Any, *, max_chars: int) -> str:
    text = (
        content
        if isinstance(content, str)
        else json.dumps(content, ensure_ascii=False, sort_keys=True)
    )
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... <truncated {len(text) - max_chars} chars>"


def _message_text_openviking_text(message: dict[str, Any]) -> tuple[str, str]:
    role = str(message.get("role") or "assistant")
    if role == "user":
        return "user", str(message.get("content") or "")
    if role == "tool":
        return "assistant", f"Tool result: {message.get('content') or ''}"
    calls = message.get("tool_calls") or []
    if calls:
        rendered = []
        for call in calls:
            name = call.get("name") or call.get("function", {}).get("name") or "unknown_tool"
            arguments = call.get("arguments") or call.get("function", {}).get("arguments") or {}
            rendered.append(f"{name}({json.dumps(arguments, ensure_ascii=False, sort_keys=True)})")
        return "assistant", "Assistant tool call: " + "; ".join(rendered)
    return "assistant", str(message.get("content") or "")


def _message_texts_role_tool_blocks(
    message: dict[str, Any],
    *,
    tool_calls_by_id: dict[str, dict[str, Any]],
    max_tool_output_chars: int,
) -> list[tuple[str, str]]:
    role = str(message.get("role") or "assistant")
    rows: list[tuple[str, str]] = []
    if role in {"user", "assistant"}:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            rows.append((role, f"{role}:\n{content}"))
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            call_id = _tool_call_id(tool_call)
            if call_id:
                tool_calls_by_id[call_id] = tool_call
            requestor = str(tool_call.get("requestor") or role or "assistant")
            name = _tool_call_name(tool_call)
            arguments = _tool_call_arguments(tool_call)
            lines = ["tool-call:"]
            if call_id:
                lines.append(f"call_id: {call_id}")
            if name:
                lines.append(f"name: {name}")
            lines.append(
                "arguments: "
                + json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
            )
            rows.append((requestor, "\n".join(lines)))
    elif role == "tool":
        call_id = _tool_result_call_id(message)
        tool_call = tool_calls_by_id.get(call_id) or {}
        requestor = str(message.get("requestor") or tool_call.get("requestor") or "assistant")
        output = _compact_train_tool_output(
            message.get("content"),
            max_chars=max_tool_output_chars,
        )
        lines = ["tool-response:"]
        if call_id:
            lines.append(f"call_id: {call_id}")
        name = _tool_call_name(tool_call)
        if name:
            lines.append(f"name: {name}")
        if message.get("error"):
            lines.append("error: true")
        lines.append(f"output: {output}")
        rows.append((requestor, "\n".join(lines)))
    else:
        content = str(message.get("content") or "").strip()
        if content:
            rows.append(("assistant", f"{role}:\n{content}"))
    return rows


def _message_texts(
    message: dict[str, Any],
    *,
    transcript_format: str,
    tool_calls_by_id: dict[str, dict[str, Any]],
    max_tool_output_chars: int,
) -> list[tuple[str, str]]:
    if transcript_format == TRAIN_TRANSCRIPT_OPENVIKING_TEXT:
        return [_message_text_openviking_text(message)]
    if transcript_format in {TRAIN_TRANSCRIPT_ROLE_TOOL_BLOCKS, TRAIN_TRANSCRIPT_CUSTOM_LIKE}:
        return _message_texts_role_tool_blocks(
            message,
            tool_calls_by_id=tool_calls_by_id,
            max_tool_output_chars=max_tool_output_chars,
        )
    raise ValueError(f"Unsupported train_transcript_format: {transcript_format}")


def _scenario_sha256(instructions: str) -> str:
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()


def _load_fixed_first_user_fixture(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"fixed-first-user fixture not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = data.get("by_scenario_sha256") if isinstance(data, dict) else None
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError(f"fixed-first-user fixture has no by_scenario_sha256 map: {path}")
    return {str(key): str(value) for key, value in mapping.items()}


def _has_user_message(state: Any) -> bool:
    for message in getattr(state, "messages", []) or []:
        role = getattr(message, "role", None)
        if str(getattr(role, "value", role)) == "user":
            return True
    return False


def _append_incoming_user_context(message: Any, state: Any) -> None:
    from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolMessage

    if isinstance(message, MultiToolMessage):
        state.messages.extend(message.tool_messages)
    elif isinstance(message, ToolMessage):
        state.messages.append(message)
    elif isinstance(message, AssistantMessage) and (
        message.has_content() or message.is_tool_call()
    ):
        state.messages.append(message)


def _register_fixed_first_user(args: argparse.Namespace) -> str:
    if not args.fixed_first_user_file:
        return args.user
    _add_tau2_to_path(args.tau2_repo)
    mapping = _load_fixed_first_user_fixture(args.fixed_first_user_file)

    from tau2.data_model.message import UserMessage
    from tau2.registry import registry
    from tau2.user.user_simulator import UserSimulator

    class FixedFirstUserSimulator(UserSimulator):  # type: ignore[misc]
        def _generate_next_message(self, message: Any, state: Any) -> UserMessage:  # type: ignore[override]
            if not _has_user_message(state):
                key = _scenario_sha256(str(self.instructions or ""))
                fixed = mapping.get(key)
                if fixed is None:
                    raise RuntimeError(
                        f"fixed-first-user fixture does not cover this TAU-2 scenario: sha256={key}"
                    )
                _append_incoming_user_context(message, state)
                return UserMessage(role="user", content=fixed)
            return super()._generate_next_message(message, state)

    if FIXED_FIRST_USER_NAME not in registry.get_users():
        registry.register_user(FixedFirstUserSimulator, FIXED_FIRST_USER_NAME)
    return FIXED_FIRST_USER_NAME


def _run_tau2(
    *,
    tau2_repo: Path,
    domain: str,
    split: str,
    task_ids: list[str] | None,
    num_tasks: int | None,
    trials: int,
    max_steps: int,
    max_concurrency: int,
    agent: str,
    user: str,
    agent_llm: str,
    user_llm: str,
    agent_llm_args: dict[str, Any],
    user_llm_args: dict[str, Any],
    seed: int,
    save_to: Path,
):
    _add_tau2_to_path(tau2_repo)
    _patch_tau2_auxiliary_llm_defaults(agent_llm, agent_llm_args)
    from tau2.data_model.simulation import RunConfig, TextRunConfig
    from tau2.run import run_domain

    compat_results = _compat_results_path(save_to)
    if save_to.exists():
        save_to.unlink()
    if compat_results.parent.is_dir():
        shutil.rmtree(compat_results.parent)
    config_cls = TextRunConfig if getattr(RunConfig, "__origin__", None) is not None else RunConfig
    result = run_domain(
        config_cls(
            domain=domain,
            task_split_name=split,
            task_ids=task_ids,
            num_tasks=num_tasks,
            agent=agent,
            llm_agent=agent_llm,
            llm_args_agent=agent_llm_args,
            user=user,
            llm_user=user_llm,
            llm_args_user=user_llm_args,
            num_trials=trials,
            max_steps=max_steps,
            save_to=_save_to_arg(save_to),
            max_concurrency=max_concurrency,
            seed=seed,
            log_level="INFO",
        )
    )
    if not save_to.exists() and compat_results.exists():
        shutil.copyfile(compat_results, save_to)
    return result


def _client(args: argparse.Namespace):
    import openviking as ov

    client = ov.SyncHTTPClient(
        url=args.openviking_url,
        api_key=None,
        user=args.openviking_user,
        account=args.openviking_account,
        timeout=args.openviking_timeout,
        extra_headers={},
    )
    client.initialize()
    return client


def _wait_task(client: Any, task_id: str | None, timeout: int) -> dict[str, Any]:
    if not task_id:
        return {"status": "no_task"}
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get_task(task_id)
        status = (last or {}).get("status")
        if status == "completed":
            return last or {"status": status}
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"OpenViking task {task_id} {status}: {last}")
        time.sleep(2)
    raise TimeoutError(f"OpenViking task {task_id} did not finish within {timeout}s: {last}")


def _read_memory_text(client: Any, match: Any) -> tuple[str, str | None]:
    try:
        return client.read(getattr(match, "uri", "")), None
    except Exception as exc:
        fallback = getattr(match, "abstract", "") or getattr(match, "overview", "") or ""
        return fallback, f"{type(exc).__name__}: {exc}"


def _probe_corpus(args: argparse.Namespace, client: Any) -> dict[str, Any]:
    result = client.search(
        query=f"{args.domain} customer service order reservation booking cancellation exchange return update",
        target_uri=args.search_uri,
        limit=args.retrieval_top_k,
    )
    memories = list(getattr(result, "memories", []) or [])
    reads = []
    for match in memories[: args.retrieval_top_k]:
        uri = getattr(match, "uri", "")
        text, read_error = _read_memory_text(client, match)
        row = {
            "uri": uri,
            "score": getattr(match, "score", None),
            "text_chars": len(text),
            "non_empty": bool(str(text).strip()),
        }
        if read_error:
            row["read_error"] = read_error
        reads.append(row)
    return {
        "query": f"{args.domain} customer service order reservation booking cancellation exchange return update",
        "match_count": len(memories),
        "read_non_empty_count": sum(1 for row in reads if row["non_empty"]),
        "matches": reads,
    }


def _train(args: argparse.Namespace, train_results: Path, corpus_manifest: Path) -> dict[str, Any]:
    if corpus_manifest.is_file() and not args.force_train:
        manifest = json.loads(corpus_manifest.read_text())
        cached_transcript_format = str(
            manifest.get("train_transcript_format") or TRAIN_TRANSCRIPT_OPENVIKING_TEXT
        )
        if cached_transcript_format != args.train_transcript_format:
            raise ValueError(
                "cached corpus train_transcript_format mismatch: "
                f"{cached_transcript_format!r} != {args.train_transcript_format!r}; "
                "use a distinct corpus_id or --force-train"
            )
        cached_include_system_prompt = bool(manifest.get("train_include_system_prompt") or False)
        if cached_include_system_prompt != bool(args.train_include_system_prompt):
            raise ValueError(
                "cached corpus train_include_system_prompt mismatch: "
                f"{cached_include_system_prompt!r} != {bool(args.train_include_system_prompt)!r}; "
                "use a distinct corpus_id or --force-train"
            )
        cached_tool_output_max_chars = int(
            manifest.get("train_tool_output_max_chars") or DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS
        )
        if cached_tool_output_max_chars != int(args.train_tool_output_max_chars):
            raise ValueError(
                "cached corpus train_tool_output_max_chars mismatch: "
                f"{cached_tool_output_max_chars!r} != {int(args.train_tool_output_max_chars)!r}; "
                "use a distinct corpus_id or --force-train"
            )
        cached_skip_failed = bool(manifest.get("train_skip_failed_sessions") or False)
        if cached_skip_failed != bool(args.train_skip_failed_sessions):
            raise ValueError(
                "cached corpus train_skip_failed_sessions mismatch: "
                f"{cached_skip_failed!r} != {bool(args.train_skip_failed_sessions)!r}; "
                "use a distinct corpus_id or --force-train"
            )
        return manifest

    if train_results.is_file() and not args.force_train:
        data = json.loads(train_results.read_text())
        assert_tau2_results_complete(data, context=f"{args.domain} cached train")
    else:
        _run_tau2(
            tau2_repo=args.tau2_repo,
            domain=args.domain,
            split=args.train_split_name,
            task_ids=args.train_task_ids,
            num_tasks=args.train_num_tasks,
            trials=1,
            max_steps=args.max_steps,
            max_concurrency=args.max_concurrency,
            agent=args.base_agent,
            user=args.user,
            agent_llm=args.agent_llm,
            user_llm=args.user_llm,
            agent_llm_args=args.agent_llm_args,
            user_llm_args=args.user_llm_args,
            seed=args.seed,
            save_to=train_results,
        )
        data = json.loads(train_results.read_text())
        assert_tau2_results_complete(data, context=f"{args.domain} train")

    client = _client(args)
    committed = []
    system_prompt_text = ""
    if args.train_include_system_prompt:
        policy = _load_domain_policy(args.tau2_repo, args.domain)
        system_prompt_text = _build_system_prompt(policy)
    skipped_failed_sessions: list[dict[str, Any]] = []
    try:
        for sim in data.get("simulations") or []:
            if args.train_skip_failed_sessions and not _task_success(sim):
                skipped_failed_sessions.append(
                    {
                        "session_id": (
                            f"tau2-{args.domain}-train-{sim.get('task_id')}-"
                            f"trial-{sim.get('trial', 0)}"
                        ),
                        "task_id": sim.get("task_id"),
                        "trial": sim.get("trial", 0),
                        "reward": _reward(sim),
                        "db_match": _db_match(sim),
                    }
                )
                continue
            session_id = (
                f"tau2-{args.domain}-train-{sim.get('task_id')}-trial-{sim.get('trial', 0)}"
            )
            created = client.create_session(session_id=session_id)
            sid = created.get("session_id", session_id)
            if system_prompt_text.strip():
                client.add_message(
                    sid,
                    role="user",
                    parts=[{"type": "text", "text": f"system:\n{system_prompt_text}"}],
                )
            tool_calls_by_id: dict[str, dict[str, Any]] = {}
            for msg in sim.get("messages") or []:
                for role, text in _message_texts(
                    msg,
                    transcript_format=args.train_transcript_format,
                    tool_calls_by_id=tool_calls_by_id,
                    max_tool_output_chars=args.train_tool_output_max_chars,
                ):
                    if not text.strip():
                        continue
                    client.add_message(
                        sid,
                        role=role,
                        parts=[{"type": "text", "text": text}],
                        created_at=msg.get("timestamp"),
                    )
            result = client.commit_session(sid, telemetry=True)
            task = _wait_task(client, result.get("task_id"), args.openviking_wait_timeout)
            committed.append(
                {
                    "session_id": sid,
                    "task_id": sim.get("task_id"),
                    "trial": sim.get("trial", 0),
                    "reward": _reward(sim),
                    "db_match": _db_match(sim),
                    "commit_status": result.get("status"),
                    "openviking_task_id": result.get("task_id"),
                    "openviking_task_status": task.get("status"),
                }
            )
    finally:
        client.close()

    client = _client(args)
    try:
        corpus_probe = _probe_corpus(args, client)
    finally:
        client.close()

    manifest = {
        "domain": args.domain,
        "train_results": str(train_results),
        "openviking": {
            "url": args.openviking_url,
            "account": args.openviking_account,
            "user": args.openviking_user,
            "search_uri": args.search_uri,
        },
        "train_transcript_format": args.train_transcript_format,
        "train_include_system_prompt": bool(args.train_include_system_prompt),
        "train_skip_failed_sessions": bool(args.train_skip_failed_sessions),
        "train_tool_output_max_chars": args.train_tool_output_max_chars,
        "committed_sessions": committed,
        "committed_session_count": len(committed),
        "skipped_failed_sessions": skipped_failed_sessions,
        "skipped_failed_session_count": len(skipped_failed_sessions),
        "corpus_probe": corpus_probe,
    }
    _write_json(corpus_manifest, manifest)
    return manifest


def _register_memory_agent(args: argparse.Namespace, trace_path: Path) -> None:
    _add_tau2_to_path(args.tau2_repo)

    from tau2.agent.llm_agent import LLMAgent, LLMAgentState
    from tau2.data_model.message import AssistantMessage, MultiToolMessage, SystemMessage
    from tau2.registry import registry
    from tau2.utils.llm_utils import generate

    scope_prompt = ""
    if args.scope_prompt_file is not None:
        scope_prompt = args.scope_prompt_file.read_text(encoding="utf-8").strip()

    class OpenVikingMemoryAgent(LLMAgent):
        def get_init_state(self, message_history=None):
            state = super().get_init_state(message_history)
            if scope_prompt:
                state.system_messages.append(SystemMessage(role="system", content=scope_prompt))
            if args.retrieval_mode in {"first_user", "first_user_prewrite"}:
                state.system_messages.append(
                    SystemMessage(role="system", content="<openviking_memory_not_loaded/>")
                )
            return state

        def _retrieve(
            self,
            query: str,
            *,
            search_limit: int,
            inject_limit: int,
            inject_max_chars: int | None = None,
        ) -> tuple[str, list[dict[str, Any]]]:
            client = _client(args)
            rows: list[dict[str, Any]] = []
            try:
                result = client.search(query=query, target_uri=args.search_uri, limit=search_limit)
                memories = list(getattr(result, "memories", []) or [])
                blocks = []
                injected_chars_used = 0
                for index, match in enumerate(memories[:search_limit], 1):
                    uri = getattr(match, "uri", "")
                    text, read_error = _read_memory_text(client, match)
                    clean_text = text.strip()
                    block_text = f"Memory {index} ({uri}):\n{clean_text}" if clean_text else ""
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
                    if budget_dropped:
                        row["skipped_reason"] = "inject_char_budget_exceeded"
                    if read_error:
                        row["read_error"] = read_error
                    rows.append(row)
                    if injected:
                        blocks.append(block_text)
                return "\n\n".join(blocks), rows
            finally:
                client.close()

        def _trace(self, event: dict[str, Any]) -> None:
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

        @staticmethod
        def _trace_injection_fields(block: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
            injected_count = sum(1 for row in matches if row.get("injected"))
            return {
                "injected": bool(block.strip()),
                "injected_count": injected_count if block.strip() else 0,
                "retrieval_action_taken": "retrieve_and_inject"
                if block.strip()
                else "retrieve_no_injection",
            }

        def _generate(self, messages):
            def _is_empty_assistant(response) -> bool:
                content = str(getattr(response, "content", "") or "")
                tool_calls = getattr(response, "tool_calls", None) or []
                return not content.strip() and not tool_calls

            try:
                response = generate(
                    model=self.llm,
                    tools=self.tools,
                    messages=messages,
                    **self.llm_args,
                )
                if not _is_empty_assistant(response):
                    return response
            except json.JSONDecodeError:
                retry_messages = messages + [
                    SystemMessage(
                        role="system",
                        content=(
                            "Retry the last assistant step once. If you call a tool, "
                            "the tool arguments must be syntactically valid JSON."
                        ),
                    )
                ]
            else:
                retry_messages = messages + [
                    SystemMessage(
                        role="system",
                        content=(
                            "Retry the last assistant step once. Return either a useful "
                            "natural language response or a valid tool call; do not return "
                            "an empty assistant message."
                        ),
                    )
                ]
            try:
                response = generate(
                    model=self.llm,
                    tools=self.tools,
                    messages=retry_messages,
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
            if isinstance(message, MultiToolMessage):
                state.messages.extend(message.tool_messages)
            else:
                state.messages.append(message)
            marker_index = next(
                (
                    i
                    for i, item in enumerate(state.system_messages)
                    if isinstance(item, SystemMessage)
                    and item.content == "<openviking_memory_not_loaded/>"
                ),
                None,
            )
            role = getattr(message, "role", "")
            role_value = getattr(role, "value", role)
            if marker_index is not None and str(role_value) == "user":
                query = str(getattr(message, "content", "") or "")
                block, matches = self._retrieve(
                    query,
                    search_limit=args.first_user_retrieval_top_k,
                    inject_limit=args.first_user_inject_top_k,
                    inject_max_chars=args.first_user_memory_inject_max_chars,
                )
                prompt = (
                    "No OpenViking memory matched this user request."
                    if not block
                    else "Use these OpenViking memories only when they match the current task:\n\n"
                    + block
                )
                state.system_messages[marker_index] = SystemMessage(role="system", content=prompt)
                self._trace(
                    {
                        "decision_node": "first_user",
                        "query": query,
                        "search_limit": args.first_user_retrieval_top_k,
                        "inject_limit": args.first_user_inject_top_k,
                        "inject_max_chars": args.first_user_memory_inject_max_chars,
                        "match_count": len(matches),
                        "matches": matches,
                        **self._trace_injection_fields(block, matches),
                    }
                )

            assistant_message = self._generate(state.system_messages + state.messages)
            if args.retrieval_mode in {"prewrite", "first_user_prewrite"}:
                tool_calls = list(getattr(assistant_message, "tool_calls", None) or [])
                write_calls = [call for call in tool_calls if _is_write_tool_call(call)]
                if write_calls:
                    query = _tool_call_query(write_calls, state.messages)
                    block, matches = self._retrieve(
                        query,
                        search_limit=args.prewrite_retrieval_top_k,
                        inject_limit=args.prewrite_inject_top_k,
                        inject_max_chars=args.prewrite_memory_inject_max_chars,
                    )
                    self._trace(
                        {
                            "decision_node": "before_write_tool_call",
                            "query": query,
                            "search_limit": args.prewrite_retrieval_top_k,
                            "inject_limit": args.prewrite_inject_top_k,
                            "inject_max_chars": args.prewrite_memory_inject_max_chars,
                            "match_count": len(matches),
                            "matches": matches,
                            **self._trace_injection_fields(block, matches),
                            "tool_calls": [
                                {
                                    "name": _tool_call_name(call),
                                    "arguments": _tool_call_arguments(call),
                                }
                                for call in write_calls
                            ],
                        }
                    )
                    if block:
                        prompt = (
                            "Before executing the pending write-like tool call, use these "
                            "OpenViking memories only when they match the current task:\n\n" + block
                        )
                        assistant_message = self._generate(
                            state.system_messages
                            + state.messages
                            + [SystemMessage(role="system", content=prompt)]
                        )
            state.messages.append(assistant_message)
            return assistant_message, state

    if AGENT_NAME not in registry.get_agents():

        def create_openviking_memory_agent(tools, domain_policy, **kwargs):
            return OpenVikingMemoryAgent(
                tools=tools,
                domain_policy=domain_policy,
                llm=kwargs.get("llm"),
                llm_args=kwargs.get("llm_args"),
            )

        if hasattr(registry, "register_agent"):
            registry.register_agent(OpenVikingMemoryAgent, AGENT_NAME)
        else:
            registry.register_agent_factory(create_openviking_memory_agent, AGENT_NAME)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TAU-2 with OpenViking Memory V2.")
    parser.add_argument("--tau2-repo", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--corpus-dir", type=Path)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--strategy-id", default="memory_v2_experience_only")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--train-split-name", default="train")
    parser.add_argument("--eval-split-name", default="test")
    parser.add_argument("--task-id", dest="task_ids", action="append")
    parser.add_argument("--num-tasks", type=int)
    parser.add_argument("--train-task-id", dest="train_task_ids", action="append")
    parser.add_argument("--train-num-tasks", type=int)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-concurrency", type=int, default=10)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--base-agent", default="llm_agent")
    parser.add_argument("--user", default="user_simulator")
    parser.add_argument("--agent-llm", required=True)
    parser.add_argument("--user-llm", required=True)
    parser.add_argument("--agent-llm-args", type=_json, default={})
    parser.add_argument("--user-llm-args", type=_json, default={})
    parser.add_argument("--openviking-url")
    parser.add_argument("--openviking-account")
    parser.add_argument("--openviking-user")
    parser.add_argument("--openviking-timeout", type=float, default=600.0)
    parser.add_argument("--openviking-wait-timeout", type=int, default=600)
    parser.add_argument("--search-uri")
    parser.add_argument("--retrieval-top-k", type=int, default=4)
    parser.add_argument("--first-user-retrieval-top-k", type=int)
    parser.add_argument("--first-user-inject-top-k", type=int)
    parser.add_argument("--prewrite-retrieval-top-k", type=int)
    parser.add_argument("--prewrite-inject-top-k", type=int)
    parser.add_argument("--memory-inject-max-chars", type=int)
    parser.add_argument("--first-user-memory-inject-max-chars", type=int)
    parser.add_argument("--prewrite-memory-inject-max-chars", type=int)
    parser.add_argument("--fixed-first-user-file", type=Path)
    parser.add_argument("--scope-prompt-file", type=Path)
    parser.add_argument(
        "--train-transcript-format",
        choices=[
            TRAIN_TRANSCRIPT_OPENVIKING_TEXT,
            TRAIN_TRANSCRIPT_ROLE_TOOL_BLOCKS,
            TRAIN_TRANSCRIPT_CUSTOM_LIKE,
        ],
        default=TRAIN_TRANSCRIPT_OPENVIKING_TEXT,
        help=(
            "How to replay TAU-2 train messages into OpenViking sessions. "
            "openviking_text preserves the compact adapter text format; role_tool_blocks "
            "uses role-prefixed messages plus tool-call/tool-response blocks. "
            "custom_like is a compatibility alias for older cached custom-like corpora."
        ),
    )
    parser.add_argument(
        "--train-include-system-prompt",
        action="store_true",
        help="Prepend the domain policy as a user-visible system block during training.",
    )
    parser.add_argument(
        "--train-skip-failed-sessions",
        action="store_true",
        help="Skip reward<1 train sessions when building positive trajectory memory.",
    )
    parser.add_argument(
        "--train-tool-output-max-chars",
        type=int,
        default=DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS,
        help=(
            "Maximum characters kept for each tool-response block when "
            f"--train-transcript-format={TRAIN_TRANSCRIPT_ROLE_TOOL_BLOCKS}."
        ),
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=["first_user", "prewrite", "first_user_prewrite"],
        default="first_user",
    )
    parser.add_argument("--force-train", action="store_true")
    parser.add_argument("--prepare-corpus-only", action="store_true")
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Run the configured TAU-2 agent without OpenViking retrieval.",
    )
    args = parser.parse_args()
    normalize_litellm_env()
    if args.train_tool_output_max_chars <= 0:
        parser.error("--train-tool-output-max-chars must be positive")
    for name in (
        "memory_inject_max_chars",
        "first_user_memory_inject_max_chars",
        "prewrite_memory_inject_max_chars",
    ):
        value = getattr(args, name)
        if value is not None and value < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    if not args.no_memory:
        missing = [
            name
            for name in (
                "openviking_url",
                "openviking_account",
                "openviking_user",
                "search_uri",
            )
            if not getattr(args, name)
        ]
        if missing:
            parser.error(
                "OpenViking memory runs require: "
                + ", ".join("--" + name.replace("_", "-") for name in missing)
            )

    args.tau2_repo = args.tau2_repo.resolve()
    args.run_dir = args.run_dir.resolve()
    if args.corpus_dir is not None:
        args.corpus_dir = args.corpus_dir.resolve()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir = args.corpus_dir or args.run_dir
    corpus_dir.mkdir(parents=True, exist_ok=True)
    args.first_user_retrieval_top_k = args.first_user_retrieval_top_k or args.retrieval_top_k
    args.first_user_inject_top_k = args.first_user_inject_top_k or args.first_user_retrieval_top_k
    args.prewrite_retrieval_top_k = args.prewrite_retrieval_top_k or args.retrieval_top_k
    args.prewrite_inject_top_k = args.prewrite_inject_top_k or args.prewrite_retrieval_top_k
    args.first_user_memory_inject_max_chars = (
        args.first_user_memory_inject_max_chars
        if args.first_user_memory_inject_max_chars is not None
        else args.memory_inject_max_chars
    )
    args.prewrite_memory_inject_max_chars = (
        args.prewrite_memory_inject_max_chars
        if args.prewrite_memory_inject_max_chars is not None
        else args.memory_inject_max_chars
    )
    if args.fixed_first_user_file is not None:
        args.fixed_first_user_file = args.fixed_first_user_file.expanduser().resolve()
    if args.scope_prompt_file is not None:
        args.scope_prompt_file = args.scope_prompt_file.expanduser().resolve()
        if not args.scope_prompt_file.is_file():
            parser.error(f"--scope-prompt-file does not exist: {args.scope_prompt_file}")
    train_results = corpus_dir / "train_results.json"
    corpus_manifest = corpus_dir / "corpus_manifest.json"
    eval_results = args.run_dir / f"{args.run_label}.json"
    trace_path = args.run_dir / f"{args.run_label}.retrieval_trace.jsonl"
    summary_path = args.run_dir / f"{args.run_label}.summary.json"

    if args.no_memory:
        user_name = _register_fixed_first_user(args)
        _run_tau2(
            tau2_repo=args.tau2_repo,
            domain=args.domain,
            split=args.eval_split_name,
            task_ids=args.task_ids,
            num_tasks=args.num_tasks,
            trials=1,
            max_steps=args.max_steps,
            max_concurrency=args.max_concurrency,
            agent=args.base_agent,
            user=user_name,
            agent_llm=args.agent_llm,
            user_llm=args.user_llm,
            agent_llm_args=args.agent_llm_args,
            user_llm_args=args.user_llm_args,
            seed=args.seed,
            save_to=eval_results,
        )
        assert_tau2_results_complete(
            json.loads(eval_results.read_text()), context=f"{args.domain} eval"
        )
        summary = {
            "run_label": args.run_label,
            "domain": args.domain,
            "strategy_id": args.strategy_id,
            "seed": args.seed,
            "fixed_first_user_file": str(args.fixed_first_user_file)
            if args.fixed_first_user_file
            else None,
            "eval_results": str(eval_results),
            "metrics": _metrics(eval_results),
        }
        _write_json(summary_path, summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    corpus = _train(args, train_results, corpus_manifest)
    if args.prepare_corpus_only:
        print(
            json.dumps(
                {
                    "run_label": args.run_label,
                    "domain": args.domain,
                    "strategy_id": args.strategy_id,
                    "prepare_corpus_only": True,
                    "corpus": corpus,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    trace_path.touch()
    _register_memory_agent(args, trace_path)
    user_name = _register_fixed_first_user(args)
    _run_tau2(
        tau2_repo=args.tau2_repo,
        domain=args.domain,
        split=args.eval_split_name,
        task_ids=args.task_ids,
        num_tasks=args.num_tasks,
        trials=1,
        max_steps=args.max_steps,
        max_concurrency=args.max_concurrency,
        agent=AGENT_NAME,
        user=user_name,
        agent_llm=args.agent_llm,
        user_llm=args.user_llm,
        agent_llm_args=args.agent_llm_args,
        user_llm_args=args.user_llm_args,
        seed=args.seed,
        save_to=eval_results,
    )
    assert_tau2_results_complete(
        json.loads(eval_results.read_text()), context=f"{args.domain} eval"
    )
    summary = {
        "run_label": args.run_label,
        "domain": args.domain,
        "strategy_id": args.strategy_id,
        "retrieval_mode": args.retrieval_mode,
        "retrieval": {
            "first_user_retrieval_top_k": args.first_user_retrieval_top_k,
            "first_user_inject_top_k": args.first_user_inject_top_k,
            "first_user_memory_inject_max_chars": args.first_user_memory_inject_max_chars,
            "prewrite_retrieval_top_k": args.prewrite_retrieval_top_k,
            "prewrite_inject_top_k": args.prewrite_inject_top_k,
            "prewrite_memory_inject_max_chars": args.prewrite_memory_inject_max_chars,
            "memory_inject_max_chars": args.memory_inject_max_chars,
        },
        "train_transcript_format": args.train_transcript_format,
        "train_include_system_prompt": bool(args.train_include_system_prompt),
        "train_skip_failed_sessions": bool(args.train_skip_failed_sessions),
        "train_tool_output_max_chars": args.train_tool_output_max_chars,
        "seed": args.seed,
        "fixed_first_user_file": str(args.fixed_first_user_file)
        if args.fixed_first_user_file
        else None,
        "scope_prompt_file": str(args.scope_prompt_file) if args.scope_prompt_file else None,
        "corpus": corpus,
        "eval_results": str(eval_results),
        "retrieval_trace": str(trace_path),
        "metrics": _metrics(eval_results),
    }
    _write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
