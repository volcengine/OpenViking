#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from tau2_common import normalize_litellm_env


AGENT_NAME = "openviking_memory_agent"
REPO_ROOT = Path(__file__).resolve().parents[3]
READ_TOOL_PREFIXES = (
    "get_",
    "find_",
    "list_",
    "search_",
    "calculate",
    "think",
    "transfer_",
)


def _json(text: str) -> dict[str, Any]:
    return json.loads(text) if text else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _add_tau2_to_path(tau2_repo: Path) -> None:
    src = tau2_repo / "src"
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(src if src.is_dir() else tau2_repo))


def _save_to_arg(path: Path) -> str:
    # TAU-2 run_domain appends ".json" to save_to. Keep our artifact paths
    # stable by passing the stem when callers hand us a JSON path.
    return str(path.with_suffix("") if path.suffix == ".json" else path)


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


def _metrics(results_path: Path) -> dict[str, Any]:
    data = json.loads(results_path.read_text())
    sims = data.get("simulations") or []
    rewards = [_reward(sim) for sim in sims]
    db_values = [_db_match(sim) for sim in sims]
    db_known = [value for value in db_values if value is not None]
    return {
        "simulation_count": len(sims),
        "avg_reward": sum(rewards) / len(rewards) if rewards else 0.0,
        "db_match_rate": (sum(1 for value in db_known if value) / len(db_known)) if db_known else None,
    }


def _is_write_tool_call(tool_call: Any) -> bool:
    name = str(getattr(tool_call, "name", "") or "")
    return bool(name) and not name.startswith(READ_TOOL_PREFIXES)


def _tool_call_query(tool_calls: list[Any], state_messages: list[Any]) -> str:
    rendered = []
    for call in tool_calls:
        rendered.append(
            f"{getattr(call, 'name', 'unknown_tool')}("
            f"{json.dumps(getattr(call, 'arguments', {}) or {}, ensure_ascii=False, sort_keys=True)}"
            ")"
        )
    recent_user = [
        str(getattr(message, "content", "") or "")
        for message in state_messages[-8:]
        if str(getattr(message, "role", "")) == "user" and str(getattr(message, "content", "") or "").strip()
    ]
    return (
        "Before executing write-like tool call(s): "
        + "; ".join(rendered)
        + "\nRecent user context: "
        + " | ".join(recent_user[-3:])
    )


def _message_text(message: dict[str, Any]) -> tuple[str, str]:
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
    from tau2.data_model.simulation import RunConfig
    from tau2.run import run_domain

    if save_to.exists():
        save_to.unlink()
    return run_domain(
        RunConfig(
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


def _client(args: argparse.Namespace):
    import openviking as ov

    client = ov.SyncHTTPClient(
        url=args.openviking_url,
        api_key="",
        user=args.openviking_user,
        agent_id=args.openviking_agent_id,
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


def _train(args: argparse.Namespace, train_results: Path, corpus_manifest: Path) -> dict[str, Any]:
    if corpus_manifest.is_file() and not args.force_train:
        return json.loads(corpus_manifest.read_text())

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
    client = _client(args)
    committed = []
    try:
        for sim in data.get("simulations") or []:
            session_id = f"tau2-{args.domain}-train-{sim.get('task_id')}-trial-{sim.get('trial', 0)}"
            created = client.create_session(session_id=session_id)
            sid = created.get("session_id", session_id)
            for msg in sim.get("messages") or []:
                role, text = _message_text(msg)
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
                    "commit_status": result.get("status"),
                    "openviking_task_id": result.get("task_id"),
                    "openviking_task_status": task.get("status"),
                }
            )
    finally:
        client.close()

    manifest = {
        "domain": args.domain,
        "train_results": str(train_results),
        "openviking": {
            "url": args.openviking_url,
            "account": args.openviking_account,
            "user": args.openviking_user,
            "agent_id": args.openviking_agent_id,
            "search_uri": args.search_uri,
        },
        "committed_sessions": committed,
        "committed_session_count": len(committed),
    }
    _write_json(corpus_manifest, manifest)
    return manifest


def _register_memory_agent(args: argparse.Namespace, trace_path: Path) -> None:
    _add_tau2_to_path(args.tau2_repo)

    from tau2.agent.llm_agent import LLMAgent, LLMAgentState
    from tau2.data_model.message import AssistantMessage, MultiToolMessage, SystemMessage
    from tau2.registry import registry
    from tau2.utils.llm_utils import generate

    class OpenVikingMemoryAgent(LLMAgent):
        def get_init_state(self, message_history=None):
            state = super().get_init_state(message_history)
            if args.retrieval_mode == "first_user":
                state.system_messages.append(
                    SystemMessage(role="system", content="<openviking_memory_not_loaded/>")
                )
            return state

        def _retrieve(self, query: str) -> tuple[str, list[dict[str, Any]]]:
            client = _client(args)
            rows: list[dict[str, Any]] = []
            try:
                result = client.search(query=query, target_uri=args.search_uri, limit=args.retrieval_top_k)
                memories = list(getattr(result, "memories", []) or [])
                blocks = []
                for index, match in enumerate(memories[: args.retrieval_top_k], 1):
                    uri = getattr(match, "uri", "")
                    text = ""
                    try:
                        text = client.read(uri)
                    except Exception:
                        text = getattr(match, "abstract", "") or getattr(match, "overview", "") or ""
                    rows.append(
                        {
                            "uri": uri,
                            "score": getattr(match, "score", None),
                            "level": getattr(match, "level", None),
                            "text_chars": len(text),
                        }
                    )
                    if text.strip():
                        blocks.append(f"Memory {index} ({uri}):\n{text.strip()}")
                return "\n\n".join(blocks), rows
            finally:
                client.close()

        def _trace(self, event: dict[str, Any]) -> None:
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

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
                    if isinstance(item, SystemMessage) and item.content == "<openviking_memory_not_loaded/>"
                ),
                None,
            )
            role = getattr(message, "role", "")
            role_value = getattr(role, "value", role)
            if marker_index is not None and str(role_value) == "user":
                query = str(getattr(message, "content", "") or "")
                block, matches = self._retrieve(query)
                prompt = (
                    "No OpenViking memory matched this user request."
                    if not block
                    else "Use these OpenViking experience memories only when they match the current task:\n\n"
                    + block
                )
                state.system_messages[marker_index] = SystemMessage(role="system", content=prompt)
                self._trace(
                    {
                        "decision_node": "first_user",
                        "query": query,
                        "match_count": len(matches),
                        "matches": matches,
                    }
                )

            assistant_message = self._generate(state.system_messages + state.messages)
            if args.retrieval_mode == "prewrite":
                tool_calls = list(getattr(assistant_message, "tool_calls", None) or [])
                write_calls = [call for call in tool_calls if _is_write_tool_call(call)]
                if write_calls:
                    query = _tool_call_query(write_calls, state.messages)
                    block, matches = self._retrieve(query)
                    self._trace(
                        {
                            "decision_node": "before_write_tool_call",
                            "query": query,
                            "match_count": len(matches),
                            "matches": matches,
                            "tool_calls": [
                                {
                                    "name": getattr(call, "name", ""),
                                    "arguments": getattr(call, "arguments", {}) or {},
                                }
                                for call in write_calls
                            ],
                        }
                    )
                    if block:
                        prompt = (
                            "Before executing the pending write-like tool call, use these "
                            "OpenViking experience memories only when they match the current task:\n\n"
                            + block
                        )
                        assistant_message = self._generate(
                            state.system_messages
                            + state.messages
                            + [SystemMessage(role="system", content=prompt)]
                        )
            state.messages.append(assistant_message)
            return assistant_message, state

    if AGENT_NAME not in registry.get_agents():
        registry.register_agent(OpenVikingMemoryAgent, AGENT_NAME)


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
    parser.add_argument("--openviking-url", required=True)
    parser.add_argument("--openviking-account", required=True)
    parser.add_argument("--openviking-user", required=True)
    parser.add_argument("--openviking-agent-id", required=True)
    parser.add_argument("--openviking-timeout", type=float, default=600.0)
    parser.add_argument("--openviking-wait-timeout", type=int, default=600)
    parser.add_argument("--search-uri", required=True)
    parser.add_argument("--retrieval-top-k", type=int, default=4)
    parser.add_argument("--retrieval-mode", choices=["first_user", "prewrite"], default="first_user")
    parser.add_argument("--force-train", action="store_true")
    args = parser.parse_args()
    normalize_litellm_env()

    args.tau2_repo = args.tau2_repo.resolve()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir = args.corpus_dir or args.run_dir
    corpus_dir.mkdir(parents=True, exist_ok=True)
    train_results = corpus_dir / "train_results.json"
    corpus_manifest = corpus_dir / "corpus_manifest.json"
    eval_results = args.run_dir / f"{args.run_label}.json"
    trace_path = args.run_dir / f"{args.run_label}.retrieval_trace.jsonl"
    summary_path = args.run_dir / f"{args.run_label}.summary.json"

    corpus = _train(args, train_results, corpus_manifest)
    trace_path.touch()
    _register_memory_agent(args, trace_path)
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
        user=args.user,
        agent_llm=args.agent_llm,
        user_llm=args.user_llm,
        agent_llm_args=args.agent_llm_args,
        user_llm_args=args.user_llm_args,
        seed=args.seed,
        save_to=eval_results,
    )
    summary = {
        "run_label": args.run_label,
        "domain": args.domain,
        "strategy_id": args.strategy_id,
        "retrieval_mode": args.retrieval_mode,
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
