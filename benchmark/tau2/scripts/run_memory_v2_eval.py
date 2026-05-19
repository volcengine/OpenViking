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
REPO_ROOT = Path(__file__).resolve().parents[3]
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
TRAIN_TRANSCRIPT_CUSTOM_LIKE = "custom_like"
TRAIN_OUTCOME_TRANSCRIPT_ONLY = "transcript_only"
TRAIN_OUTCOME_REWARD_SUMMARY = "reward_summary"
TRAIN_OUTCOME_EVALUATOR_REPORT = "evaluator_report"
TRAIN_OUTCOME_MESSAGE_VERSION = "v3_failure_reflection"
DEFAULT_TERMINAL_CONTINUATION_TOOLS = ("transfer_to_human_agents",)
DEFAULT_TERMINAL_PLAN_STATE_MAX_CHECKS = 1
FAILURE_MEMORY_ROLE = "failure_reflection_only"
FAILURE_MEMORY_SIDECAR_VERSION = "v1"
FAILURE_MEMORY_COMPRESSION_SOURCE_CHARS = 6000
FAILURE_MEMORY_COMPRESSION_MAX_CHARS = 1400
READ_SELECTOR_CANDIDATE_PREVIEW_CHARS = 1200
DEFAULT_WRITE_CONSEQUENCE_FINAL_RESPONSE_MAX_CHECKS = 1
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


def _train_routing_hint(domain: str) -> str:
    return "\n".join(
        [
            "[MEMORY_ROUTING_HINT]",
            f"Domain: {domain}",
            "Target memory type: agent trajectory-view operation contracts.",
            "Route reusable task-execution experience to agent trajectory memory.",
            "Extract operation-level knowledge only when it generalizes across tasks.",
            "Prefer triggers, preconditions, read-before-write steps, allowed write or handoff actions,",
            "anti-patterns, and applicability boundaries.",
            "The trajectory content must contain concrete numbered procedure steps.",
            "Do not set procedure steps to N/A, none, empty text, or a placeholder.",
            "If preconditions contain the workflow, rewrite them into action steps:",
            "read/verify state -> present options or ask confirmation -> call the allowed write/handoff tool -> verify result.",
            "Do not store customer names, object ids, account ids, addresses, phone numbers, emails,",
            "hidden evaluator criteria, exact gold answers, or other task-instance identifiers.",
            "Do not treat this as user profile, preference, event, or entity memory.",
        ]
    )


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


def _compact_json(value: Any, *, max_chars: int = 4000) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if not stripped:
        raise ValueError("empty JSON response")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _short_text(value: Any, *, max_chars: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _task_success(sim: dict[str, Any]) -> bool:
    return _reward(sim) >= 1.0


def _first_user_content(sim: dict[str, Any]) -> str:
    for message in sim.get("messages") or []:
        if message.get("role") == "user":
            content = str(message.get("content") or "").strip()
            if content:
                return content
    return "No user request text was available in this failed training sample."


def _failed_check_digest(reward_info: dict[str, Any]) -> dict[str, Any]:
    digest: dict[str, Any] = {}
    db_check = reward_info.get("db_check")
    if isinstance(db_check, dict):
        digest["db_check"] = {
            key: db_check.get(key)
            for key in ("db_match", "db_reward", "score")
            if key in db_check
        }

    action_failures = []
    for check in reward_info.get("action_checks") or []:
        if not isinstance(check, dict):
            continue
        if check.get("action_match") is not False and float(check.get("action_reward") or 0) >= 1:
            continue
        action = check.get("action") or {}
        action_failures.append(
            {
                "tool_name": action.get("name"),
                "tool_type": check.get("tool_type"),
                "action_match": check.get("action_match"),
                "action_reward": check.get("action_reward"),
            }
        )
    if action_failures:
        digest["failed_action_checks"] = action_failures[:8]

    for key in ("env_assertions", "nl_assertions", "communicate_checks"):
        failures = []
        for check in reward_info.get(key) or []:
            if not isinstance(check, dict):
                continue
            if check.get("met") is not False and check.get("passed") is not False:
                continue
            failures.append(
                {
                    "target": _short_text(
                        check.get("info")
                        or check.get("assertion")
                        or check.get("name")
                        or check.get("description"),
                        max_chars=240,
                    ),
                    "met": check.get("met"),
                    "passed": check.get("passed"),
                    "justification": _short_text(check.get("justification"), max_chars=700),
                }
            )
        if failures:
            digest[f"failed_{key}"] = failures[:8]

    return {key: value for key, value in digest.items() if value not in ({}, [])}


def _canonical_memory_uri(uri: Any) -> str:
    return str(uri or "").strip().split("#", 1)[0]


def _failure_memory_sidecar_path(corpus_manifest: Path) -> Path:
    return corpus_manifest.with_name("failure_memory_sidecar.json")


def _memory_diff_operation_rows(memory_diff: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    operations = memory_diff.get("operations") or {}
    for operation_name in ("adds", "updates"):
        operation_rows = operations.get(operation_name) or []
        if not isinstance(operation_rows, list):
            continue
        for item in operation_rows:
            if not isinstance(item, dict):
                continue
            uri = _canonical_memory_uri(item.get("uri"))
            if not uri:
                continue
            rows.append(
                {
                    "uri": uri,
                    "operation": operation_name[:-1],
                    "memory_type": item.get("memory_type"),
                }
            )
    return rows


def _memory_fields(text: str) -> dict[str, Any]:
    marker = "<!-- MEMORY_FIELDS"
    start = text.find(marker)
    if start < 0:
        return {}
    start += len(marker)
    end = text.find("-->", start)
    if end < 0:
        return {}
    fields = text[start:end].strip()
    try:
        value = json.loads(fields)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _is_failure_reflection_memory(text: str) -> bool:
    fields = _memory_fields(text)
    if str(fields.get("outcome") or "").strip().lower() == "failure":
        return True
    for line in text.splitlines():
        lowered = line.strip().lower()
        if lowered.startswith("- result:"):
            return "failed" in lowered or "failure" in lowered
    lowered = text.lower()
    return "failure_reflection" in lowered and "failed" in lowered


def _failure_reflection_search_rows(
    *,
    args: argparse.Namespace,
    client: Any,
    sim: dict[str, Any],
    session_id: str,
    archive_uri: str | None,
) -> list[dict[str, Any]]:
    if not args.search_uri:
        return []

    first_user_query = " ".join(
        part
        for part in (
            args.domain,
            _first_user_content(sim),
            "failure reflection outcome failure negative boundary",
        )
        if part
    )
    queries = (
        f"{args.domain} customer service order reservation booking cancellation exchange return update",
        first_user_query,
    )
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for query in queries:
        result = client.search(
            query=query,
            target_uri=args.search_uri,
            limit=max(args.retrieval_top_k, 20),
        )
        for match in list(getattr(result, "memories", []) or []):
            uri = _canonical_memory_uri(getattr(match, "uri", ""))
            if not uri or uri in seen:
                continue
            text, read_error = _read_memory_text(client, match)
            if read_error or not _is_failure_reflection_memory(text):
                continue
            seen.add(uri)
            rows.append(
                {
                    "uri": uri,
                    "operation": "search_detected",
                    "memory_type": "search_scope",
                    "session_id": session_id,
                    "task_id": sim.get("task_id"),
                    "reward": _reward(sim),
                    "archive_uri": archive_uri,
                    "detection_method": "failure_reflection_search",
                    "score": getattr(match, "score", None),
                }
            )
    return rows


def _failure_memory_sidecar(
    *,
    committed: list[dict[str, Any]],
    failure_memory_sources: list[dict[str, Any]],
    failure_memory_diff_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    uri_to_sources: dict[str, list[dict[str, Any]]] = {}
    uri_to_source_keys: dict[str, set[str]] = {}
    for row in failure_memory_sources:
        uri = _canonical_memory_uri(row.get("uri"))
        if not uri:
            continue
        source = {key: value for key, value in row.items() if key != "uri"}
        source_key = json.dumps(source, ensure_ascii=False, sort_keys=True, default=str)
        if source_key in uri_to_source_keys.setdefault(uri, set()):
            continue
        uri_to_source_keys[uri].add(source_key)
        uri_to_sources.setdefault(uri, []).append(source)

    return {
        "version": FAILURE_MEMORY_SIDECAR_VERSION,
        "memory_role": FAILURE_MEMORY_ROLE,
        "description": (
            "Memory URIs created or updated by failed evaluator-augmented train "
            "sessions. Treat matches as negative-boundary reflections, not positive "
            "procedures, unless a downstream compression step safely rewrites them."
        ),
        "failed_session_count": sum(
            1 for row in committed if row.get("train_outcome_role") == FAILURE_MEMORY_ROLE
        ),
        "failure_memory_uris": sorted(uri_to_sources),
        "failure_memory_uri_count": len(uri_to_sources),
        "failure_memory_sources": [
            {"uri": uri, "sources": sources}
            for uri, sources in sorted(uri_to_sources.items())
        ],
        "memory_diff_errors": failure_memory_diff_errors,
        "memory_diff_error_count": len(failure_memory_diff_errors),
    }


def _load_failure_memory_sidecar(corpus: dict[str, Any] | None) -> dict[str, Any]:
    if not corpus:
        return {}
    sidecar_path = corpus.get("failure_memory_sidecar")
    if sidecar_path:
        path = Path(str(sidecar_path)).expanduser()
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    if corpus.get("failure_memory_uris") is not None:
        return {
            "version": corpus.get("failure_memory_sidecar_version"),
            "memory_role": FAILURE_MEMORY_ROLE,
            "failure_memory_uris": corpus.get("failure_memory_uris") or [],
            "failure_memory_uri_count": corpus.get("failure_memory_uri_count", 0),
            "memory_diff_error_count": corpus.get("failure_memory_diff_error_count", 0),
        }
    return {}


def _train_outcome_message(sim: dict[str, Any], mode: str) -> str:
    if mode == TRAIN_OUTCOME_TRANSCRIPT_ONLY:
        return ""

    reward = _reward(sim)
    db_match = _db_match(sim)
    reward_info = sim.get("reward_info") or {}
    success = _task_success(sim)
    lines = [
        "<tau2_train_evaluator_outcome>",
        "This is train-split evaluator feedback for memory extraction only.",
        (
            "Interpret evaluator feedback as outcome labels, not as an instruction "
            "to repeat the observed final action."
        ),
        f"task_success: {str(success).lower()}",
        f"reward: {reward}",
    ]
    if success:
        lines.extend(
            [
                (
                    "For successful tasks, the transcript may be used as positive "
                    "evidence for a reusable procedure when the preconditions match."
                ),
                (
                    "Keep user intent, observed action, valid preconditions, payment "
                    "or object provenance, and final outcome conceptually separate."
                ),
            ]
        )
    else:
        lines.extend(
            [
                (
                    "This failed training sample must be marked as "
                    f"`memory_role: {FAILURE_MEMORY_ROLE}` if any memory is extracted from it."
                ),
                (
                    "Use it only as a short failure reflection / negative boundary, "
                    "not as a reusable step-by-step procedure. Extract at most: the "
                    "user intent, the failed precondition or policy boundary, a legal "
                    "alternative or question to ask, and the action pattern to avoid."
                ),
                (
                    "Do not record observed failed tool-call order, final transfer, "
                    "refusal, escalation, or no-op as the recommended workflow."
                ),
                f"original_user_request: {_short_text(_first_user_content(sim), max_chars=1200)}",
            ]
        )
    if success:
        lines.extend(
            [
                (
                    "If the task was not successful, extract failed preconditions, legal "
                    "alternatives, and actions to avoid; do not turn a final refusal, "
                    "transfer, escalation, or no-op into the recommended procedure unless "
                    "the evaluator indicates that no valid automated alternative remained."
                ),
                (
                    "Keep user intent, observed action, failure reason, valid preconditions, "
                    "legal alternative action, and action to avoid conceptually separate."
                ),
            ]
        )
    if db_match is not None:
        lines.append(f"db_match: {str(bool(db_match)).lower()}")
    if sim.get("termination_reason") is not None:
        lines.append(f"termination_reason: {sim.get('termination_reason')}")
    if reward_info.get("reward_basis") is not None:
        lines.append(f"reward_basis: {_compact_json(reward_info.get('reward_basis'))}")
    if reward_info.get("reward_breakdown") is not None:
        lines.append(
            f"reward_breakdown: {_compact_json(reward_info.get('reward_breakdown'))}"
        )

    if mode == TRAIN_OUTCOME_EVALUATOR_REPORT:
        evaluator = {
            key: reward_info.get(key)
            for key in (
                "db_check",
                "env_assertions",
                "action_checks",
                "nl_assertions",
                "communicate_checks",
                "info",
            )
            if reward_info.get(key) is not None
        }
        if not success:
            evaluator = _failed_check_digest(reward_info)
            if evaluator:
                lines.append(f"failed_evaluator_digest: {_compact_json(evaluator, max_chars=6000)}")
        elif evaluator:
            lines.append(f"evaluator_report: {_compact_json(evaluator, max_chars=12000)}")

    lines.append("</tau2_train_evaluator_outcome>")
    return "\n".join(lines)


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


def _tool_call_rows(tool_calls: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": _tool_call_name(call),
            "arguments": _tool_call_arguments(call),
        }
        for call in tool_calls
    ]


def _tool_call_signature(tool_call: Any) -> str:
    return (
        f"{_tool_call_name(tool_call)}:"
        f"{json.dumps(_tool_call_arguments(tool_call), ensure_ascii=False, sort_keys=True, default=str)}"
    )


def _tool_call_signatures(tool_calls: list[Any]) -> list[str]:
    return sorted(_tool_call_signature(call) for call in tool_calls if _tool_call_name(call))


def _message_role_name(message: Any) -> str:
    role = getattr(message, "role", "")
    return str(getattr(role, "value", role) or "")


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
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
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
            name = _tool_call_name(call) or "unknown_tool"
            arguments = _tool_call_arguments(call)
            rendered.append(f"{name}({json.dumps(arguments, ensure_ascii=False, sort_keys=True)})")
        return "assistant", "Assistant tool call: " + "; ".join(rendered)
    return "assistant", str(message.get("content") or "")


def _message_texts_custom_like(
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
    if transcript_format == TRAIN_TRANSCRIPT_CUSTOM_LIKE:
        return _message_texts_custom_like(
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
    seen_task = False
    while time.time() < deadline:
        last = client.get_task(task_id)
        status = (last or {}).get("status")
        if status == "completed":
            return last or {"status": status}
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"OpenViking task {task_id} {status}: {last}")
        error = (last or {}).get("error") or {}
        if status == "error" and error.get("code") == "NOT_FOUND":
            if seen_task:
                return {
                    "status": "expired_after_seen",
                    "task_id": task_id,
                    "last": last,
                }
            raise RuntimeError(f"OpenViking task {task_id} was not found: {last}")
        if status:
            seen_task = True
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
            "failure_reflection_detected": _is_failure_reflection_memory(text),
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


def _failure_reflection_probe_rows(
    *,
    corpus_probe: dict[str, Any],
    failed_committed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not failed_committed:
        return []
    rows: list[dict[str, Any]] = []
    for match in corpus_probe.get("matches") or []:
        if not isinstance(match, dict) or not match.get("failure_reflection_detected"):
            continue
        uri = _canonical_memory_uri(match.get("uri"))
        if not uri:
            continue
        for committed in failed_committed:
            rows.append(
                {
                    "uri": uri,
                    "operation": "probe_detected",
                    "memory_type": "search_scope",
                    "session_id": committed.get("session_id"),
                    "task_id": committed.get("task_id"),
                    "reward": committed.get("reward"),
                    "archive_uri": committed.get("archive_uri"),
                    "detection_method": "corpus_probe_failure_scan",
                    "score": match.get("score"),
                }
            )
    return rows


def _train(args: argparse.Namespace, train_results: Path, corpus_manifest: Path) -> dict[str, Any]:
    if corpus_manifest.is_file() and not args.force_train:
        manifest = json.loads(corpus_manifest.read_text())
        cached_mode = str(manifest.get("train_outcome_mode") or TRAIN_OUTCOME_TRANSCRIPT_ONLY)
        if cached_mode != args.train_outcome_mode:
            raise ValueError(
                "cached corpus train_outcome_mode mismatch: "
                f"{cached_mode!r} != {args.train_outcome_mode!r}; "
                "use a distinct corpus_id or --force-train"
            )
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
        cached_routing_hint = bool(manifest.get("train_routing_hint") or False)
        if cached_routing_hint != bool(args.train_routing_hint):
            raise ValueError(
                "cached corpus train_routing_hint mismatch: "
                f"{cached_routing_hint!r} != {bool(args.train_routing_hint)!r}; "
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
        cached_version = str(manifest.get("train_outcome_message_version") or "")
        if (
            args.train_outcome_mode != TRAIN_OUTCOME_TRANSCRIPT_ONLY
            and cached_version != TRAIN_OUTCOME_MESSAGE_VERSION
        ):
            raise ValueError(
                "cached corpus train_outcome_message_version mismatch: "
                f"{cached_version!r} != {TRAIN_OUTCOME_MESSAGE_VERSION!r}; "
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
    failure_memory_sources: list[dict[str, Any]] = []
    failure_memory_diff_errors: list[dict[str, Any]] = []
    system_prompt_text = ""
    if args.train_include_system_prompt:
        policy = _load_domain_policy(args.tau2_repo, args.domain)
        system_prompt_text = _build_system_prompt(policy)
    skipped_failed_sessions: list[dict[str, Any]] = []
    try:
        for sim in data.get("simulations") or []:
            task_success = _task_success(sim)
            if args.train_skip_failed_sessions and not task_success:
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
            train_outcome_role = (
                FAILURE_MEMORY_ROLE
                if args.train_outcome_mode != TRAIN_OUTCOME_TRANSCRIPT_ONLY
                and not task_success
                else "positive_or_transcript_evidence"
            )
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
            if args.train_routing_hint:
                client.add_message(
                    sid,
                    role="user",
                    parts=[{"type": "text", "text": _train_routing_hint(args.domain)}],
                )
            outcome_text = _train_outcome_message(sim, args.train_outcome_mode)
            if outcome_text.strip():
                client.add_message(
                    sid,
                    role="user",
                    parts=[{"type": "text", "text": outcome_text}],
                )
            result = client.commit_session(sid, telemetry=True)
            task = _wait_task(client, result.get("task_id"), args.openviking_wait_timeout)
            archive_uri = result.get("archive_uri")
            memory_diff_status = "not_applicable"
            failure_operation_count = 0
            if train_outcome_role == FAILURE_MEMORY_ROLE:
                memory_diff_status = "missing_archive_uri"
                if archive_uri:
                    diff_uri = f"{archive_uri}/memory_diff.json"
                    try:
                        memory_diff = json.loads(client.read(diff_uri))
                        memory_diff_status = "read"
                        for operation in _memory_diff_operation_rows(memory_diff):
                            failure_operation_count += 1
                            failure_memory_sources.append(
                                {
                                    **operation,
                                    "session_id": sid,
                                    "task_id": sim.get("task_id"),
                                    "reward": _reward(sim),
                                    "archive_uri": archive_uri,
                                }
                            )
                    except Exception as exc:
                        memory_diff_status = "read_error"
                        failure_memory_diff_errors.append(
                            {
                                "session_id": sid,
                                "task_id": sim.get("task_id"),
                                "archive_uri": archive_uri,
                                "memory_diff_uri": diff_uri,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                else:
                    failure_memory_diff_errors.append(
                        {
                            "session_id": sid,
                            "task_id": sim.get("task_id"),
                            "archive_uri": None,
                            "error": "commit_session returned no archive_uri",
                        }
                    )
                try:
                    for row in _failure_reflection_search_rows(
                        args=args,
                        client=client,
                        sim=sim,
                        session_id=sid,
                        archive_uri=archive_uri,
                    ):
                        failure_memory_sources.append(row)
                except Exception as exc:
                    failure_memory_diff_errors.append(
                        {
                            "session_id": sid,
                            "task_id": sim.get("task_id"),
                            "archive_uri": archive_uri,
                            "stage": "failure_reflection_search",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            committed.append(
                {
                    "session_id": sid,
                    "task_id": sim.get("task_id"),
                    "reward": _reward(sim),
                    "task_success": task_success,
                    "train_outcome_role": train_outcome_role,
                    "archive_uri": archive_uri,
                    "memory_diff_status": memory_diff_status,
                    "failure_memory_operation_count": failure_operation_count,
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
        failed_committed = [
            row for row in committed if row.get("train_outcome_role") == FAILURE_MEMORY_ROLE
        ]
        failure_memory_sources.extend(
            _failure_reflection_probe_rows(
                corpus_probe=corpus_probe,
                failed_committed=failed_committed,
            )
        )
        archive_by_session = {
            str(row.get("session_id")): row.get("archive_uri") for row in committed
        }
        for sim in data.get("simulations") or []:
            if args.train_outcome_mode == TRAIN_OUTCOME_TRANSCRIPT_ONLY or _task_success(sim):
                continue
            sid = f"tau2-{args.domain}-train-{sim.get('task_id')}-trial-{sim.get('trial', 0)}"
            try:
                for row in _failure_reflection_search_rows(
                    args=args,
                    client=client,
                    sim=sim,
                    session_id=sid,
                    archive_uri=archive_by_session.get(sid),
                ):
                    failure_memory_sources.append(row)
            except Exception as exc:
                failure_memory_diff_errors.append(
                    {
                        "session_id": sid,
                        "task_id": sim.get("task_id"),
                        "archive_uri": archive_by_session.get(sid),
                        "stage": "post_probe_failure_reflection_search",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    finally:
        client.close()

    failure_sidecar = _failure_memory_sidecar(
        committed=committed,
        failure_memory_sources=failure_memory_sources,
        failure_memory_diff_errors=failure_memory_diff_errors,
    )
    failure_sidecar_path = _failure_memory_sidecar_path(corpus_manifest)
    _write_json(failure_sidecar_path, failure_sidecar)
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
        "train_skip_failed_sessions": bool(args.train_skip_failed_sessions),
        "skipped_failed_sessions": skipped_failed_sessions,
        "skipped_failed_session_count": len(skipped_failed_sessions),
        "failed_committed_session_count": sum(
            1 for row in committed if row.get("train_outcome_role") == FAILURE_MEMORY_ROLE
        ),
        "corpus_probe": corpus_probe,
        "failure_memory_sidecar": str(failure_sidecar_path),
        "failure_memory_sidecar_version": FAILURE_MEMORY_SIDECAR_VERSION,
        "failure_memory_uris": failure_sidecar["failure_memory_uris"],
        "failure_memory_uri_count": failure_sidecar["failure_memory_uri_count"],
        "failure_memory_diff_error_count": failure_sidecar["memory_diff_error_count"],
        "train_outcome_mode": args.train_outcome_mode,
        "train_transcript_format": args.train_transcript_format,
        "train_include_system_prompt": bool(args.train_include_system_prompt),
        "train_routing_hint": bool(args.train_routing_hint),
        "train_tool_output_max_chars": args.train_tool_output_max_chars,
        "train_outcome_message_version": (
            TRAIN_OUTCOME_MESSAGE_VERSION
            if args.train_outcome_mode != TRAIN_OUTCOME_TRANSCRIPT_ONLY
            else None
        ),
    }
    _write_json(corpus_manifest, manifest)
    return manifest


def _register_memory_agent(
    args: argparse.Namespace,
    trace_path: Path,
    corpus: dict[str, Any] | None = None,
) -> None:
    _add_tau2_to_path(args.tau2_repo)

    from tau2.agent.llm_agent import LLMAgent, LLMAgentState
    from tau2.data_model.message import (
        AssistantMessage,
        MultiToolMessage,
        SystemMessage,
        UserMessage,
    )
    from tau2.registry import registry
    from tau2.utils.llm_utils import generate

    scope_prompt = ""
    if args.scope_prompt_file is not None:
        scope_prompt = args.scope_prompt_file.read_text(encoding="utf-8").strip()
    failure_sidecar = _load_failure_memory_sidecar(corpus) if args.compress_failure_memories else {}
    failure_memory_uris = {
        _canonical_memory_uri(uri) for uri in failure_sidecar.get("failure_memory_uris") or []
    }

    def failure_match_reason(uri: str, text: str) -> str | None:
        canonical_uri = _canonical_memory_uri(uri)
        if canonical_uri and canonical_uri in failure_memory_uris:
            return "failure_memory_sidecar"
        if FAILURE_MEMORY_ROLE in text:
            return "memory_role_marker"
        return None

    def candidate_preview(match: Any) -> str:
        preview = getattr(match, "abstract", "") or getattr(match, "overview", "") or ""
        return _short_text(preview, max_chars=READ_SELECTOR_CANDIDATE_PREVIEW_CHARS)

    class OpenVikingMemoryAgent(LLMAgent):
        def _select_memories_to_read(
            self,
            *,
            query: str,
            candidates: list[dict[str, Any]],
            inject_limit: int,
        ) -> tuple[set[int], dict[int, str], str | None]:
            if not args.memory_read_selector:
                return set(range(1, min(len(candidates), inject_limit) + 1)), {}, None
            if not candidates or inject_limit <= 0:
                return set(), {}, None

            prompt = (
                "Select which OpenViking memory search results should be read and injected "
                "for the next customer-service agent step.\n"
                "Use only task applicability: current user goal, pending action, object "
                "identity, policy boundary, payment/provenance requirements, and whether "
                "the candidate appears specific enough to help. Do not select a memory just "
                "because it is top ranked. Do not select policy-opposite, stale, or broad "
                "neighbor memories.\n"
                "First classify the pending action lifecycle: creating a new object/order/"
                "reservation, modifying/canceling/updating an existing object, or completing "
                "a replacement flow after an earlier change. Prefer memories from the same "
                "lifecycle and action family. Skip existing-object modification memories before "
                "a create/book action unless they explicitly cover the same create flow; skip "
                "create-only memories before an existing-object update/cancel action unless "
                "they explicitly cover the required replacement step.\n"
                f"Select at most {inject_limit} candidates. Return only JSON with this shape: "
                '{"selected_indices":[1,2],"reasons":{"1":"why selected","3":"why skipped"}}. '
                "If none apply, return an empty selected_indices list."
            )
            payload = {
                "query": query,
                "inject_limit": inject_limit,
                "candidates": candidates,
            }
            response = generate(
                model=self.llm,
                tools=[],
                messages=[
                    SystemMessage(role="system", content=prompt),
                    UserMessage.text(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
                ],
                call_name="memory_read_selector",
                **self.llm_args,
            )
            content = str(getattr(response, "content", "") or "")
            try:
                parsed = _parse_json_object(content)
                raw_selected = parsed.get("selected_indices")
                if raw_selected is None:
                    raw_selected = [
                        item.get("index")
                        for item in parsed.get("selected", [])
                        if isinstance(item, dict)
                    ]
                selected: list[int] = []
                for item in raw_selected or []:
                    try:
                        index = int(item)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= index <= len(candidates) and index not in selected:
                        selected.append(index)
                    if len(selected) >= inject_limit:
                        break
                raw_reasons = parsed.get("reasons") or {}
                reasons: dict[int, str] = {}
                if isinstance(raw_reasons, dict):
                    for key, value in raw_reasons.items():
                        try:
                            index = int(key)
                        except (TypeError, ValueError):
                            continue
                        reasons[index] = _short_text(value, max_chars=400)
                return set(selected), reasons, None
            except Exception as exc:
                return set(), {}, f"{type(exc).__name__}: {exc}; content={content[:500]}"

        def _compress_failure_memory(
            self,
            *,
            uri: str,
            text: str,
            reason: str,
        ) -> str:
            if not hasattr(self, "_failure_memory_compression_cache"):
                self._failure_memory_compression_cache = {}
            cache_key = hashlib.sha256(
                f"{uri}\n{reason}\n{text}".encode("utf-8")
            ).hexdigest()
            cached = self._failure_memory_compression_cache.get(cache_key)
            if cached is not None:
                return cached

            prompt = (
                "Compress the retrieved OpenViking memory into a short negative-boundary "
                "reflection for the current customer-service task.\n"
                "The source memory was extracted from, or updated by, a failed train-split "
                "trajectory. Do not preserve the failed tool-call order, final handoff, "
                "refusal, escalation, or no-op as a recommended workflow.\n"
                "Output only a compact memory block with these fields when applicable:\n"
                "- Failed boundary: the precondition, policy, provenance, or object boundary "
                "that made the source trajectory fail.\n"
                "- Safe next step: the legal alternative, missing confirmation, or clarifying "
                "question an automated agent should use.\n"
                "- Avoid: the action pattern that should not be copied.\n"
                "If the source contains both a successful procedure and a failed reflection, "
                "keep only the part that is useful for avoiding the failed pattern. Keep the "
                f"answer under {FAILURE_MEMORY_COMPRESSION_MAX_CHARS} characters."
            )
            source = _short_text(text, max_chars=FAILURE_MEMORY_COMPRESSION_SOURCE_CHARS)
            response = generate(
                model=self.llm,
                tools=[],
                messages=[
                    SystemMessage(role="system", content=prompt),
                    UserMessage.text(
                        "Failure memory URI: "
                        f"{uri}\nDetection reason: {reason}\n\nSource memory:\n{source}"
                    ),
                ],
                call_name="compress_failure_memory",
                **self.llm_args,
            )
            compressed = _short_text(
                str(getattr(response, "content", "") or "").strip(),
                max_chars=FAILURE_MEMORY_COMPRESSION_MAX_CHARS,
            )
            if not compressed:
                raise ValueError("failure memory compression returned empty content")
            self._failure_memory_compression_cache[cache_key] = compressed
            return compressed

        def get_init_state(self, message_history=None):
            state = super().get_init_state(message_history)
            self._terminal_continuation_checks_used = 0
            self._terminal_plan_state_checks_used = 0
            self._write_consequence_final_response_checks_used = 0
            self._write_consequence_awaiting_write_result = False
            self._write_consequence_saw_write_result = False
            if scope_prompt:
                state.system_messages.append(SystemMessage(role="system", content=scope_prompt))
            if (
                not args.no_memory
                and args.retrieval_mode in {"first_user", "first_user_prewrite"}
            ):
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
            inject_max_chars: int = 0,
        ) -> tuple[str, list[dict[str, Any]]]:
            client = _client(args)
            rows: list[dict[str, Any]] = []
            try:
                result = client.search(query=query, target_uri=args.search_uri, limit=search_limit)
                memories = list(getattr(result, "memories", []) or [])
                selector_candidates = [
                    {
                        "index": index,
                        "uri": getattr(match, "uri", ""),
                        "score": getattr(match, "score", None),
                        "level": getattr(match, "level", None),
                        "preview": candidate_preview(match),
                    }
                    for index, match in enumerate(memories[:search_limit], 1)
                ]
                selected_to_read, selector_reasons, selector_error = self._select_memories_to_read(
                    query=query,
                    candidates=selector_candidates,
                    inject_limit=inject_limit,
                )
                blocks = []
                injected_chars_used = 0
                for index, match in enumerate(memories[:search_limit], 1):
                    uri = getattr(match, "uri", "")
                    selected = index in selected_to_read
                    read_error = None
                    text = ""
                    if selected:
                        text, read_error = _read_memory_text(client, match)
                    raw_text_chars = len(text)
                    failure_reason = (
                        failure_match_reason(uri, text)
                        if args.compress_failure_memories and selected
                        else None
                    )
                    compression_error = None
                    compressed = False
                    if selected and failure_reason:
                        try:
                            text = self._compress_failure_memory(
                                uri=uri,
                                text=text,
                                reason=failure_reason,
                            )
                            compressed = True
                        except Exception as exc:
                            text = ""
                            compression_error = f"{type(exc).__name__}: {exc}"
                    clean_text = text.strip()
                    block_text = f"Memory {index} ({uri}):\n{clean_text}" if clean_text else ""
                    block_chars = len(block_text)
                    budget_used_before = injected_chars_used
                    budget_dropped = False
                    injected = selected and bool(clean_text)
                    if (
                        injected
                        and inject_max_chars > 0
                        and injected_chars_used + block_chars > inject_max_chars
                    ):
                        injected = False
                        budget_dropped = True
                    if injected:
                        injected_chars_used += block_chars
                    row = {
                        "uri": uri,
                        "score": getattr(match, "score", None),
                        "level": getattr(match, "level", None),
                        "candidate_seen": True,
                        "selected_to_read": selected,
                        "read_selector_enabled": args.memory_read_selector,
                        "read_selector_reason": selector_reasons.get(index),
                        "skipped_reason": None
                        if selected
                        else selector_reasons.get(index)
                        or ("read_selector_error" if selector_error else "not_selected"),
                        "text_chars": len(text),
                        "block_chars": block_chars,
                        "injected": injected,
                        "inject_max_chars": inject_max_chars,
                        "inject_budget_used_before": budget_used_before,
                        "inject_budget_used_after": injected_chars_used,
                        "inject_budget_dropped": budget_dropped,
                    }
                    if budget_dropped:
                        row["skipped_reason"] = "inject_char_budget_exceeded"
                    if selector_error:
                        row["read_selector_error"] = selector_error
                    preview = selector_candidates[index - 1].get("preview")
                    if args.memory_read_selector and preview:
                        row["candidate_preview_chars"] = len(str(preview))
                    if args.compress_failure_memories:
                        row.update(
                            {
                                "failure_memory_detected": bool(failure_reason),
                                "failure_memory_match_reason": failure_reason,
                                "failure_memory_compressed": compressed,
                                "raw_text_chars": raw_text_chars,
                            }
                        )
                        if compression_error:
                            row["failure_memory_compression_error"] = compression_error
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
            budget_max_values = {
                row.get("inject_max_chars")
                for row in matches
                if row.get("inject_max_chars") is not None
            }
            budget_max = max(budget_max_values) if budget_max_values else 0
            budget_used = max(
                [int(row.get("inject_budget_used_after") or 0) for row in matches] or [0]
            )
            budget_dropped_count = sum(
                1 for row in matches if row.get("inject_budget_dropped")
            )
            return {
                "injected": bool(block.strip()),
                "injected_count": injected_count if block.strip() else 0,
                "inject_budget_max_chars": budget_max,
                "inject_budget_used_chars": budget_used,
                "inject_budget_dropped_count": budget_dropped_count,
                "inject_budget_hit": budget_dropped_count > 0,
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

        @staticmethod
        def _assistant_with_tool_calls(assistant_message, tool_calls, content: str | None = None):
            updates = {
                "tool_calls": tool_calls,
                "content": content
                if content is not None
                else getattr(assistant_message, "content", ""),
            }
            if hasattr(assistant_message, "model_copy"):
                return assistant_message.model_copy(update=updates)
            cloned = deepcopy(assistant_message)
            cloned.tool_calls = tool_calls
            cloned.content = updates["content"]
            return cloned

        def _terminal_continuation_tool_names(self) -> set[str]:
            configured = [
                str(item).strip()
                for item in (args.terminal_continuation_tools or [])
                if str(item).strip()
            ]
            if configured:
                return set(configured)
            return set(DEFAULT_TERMINAL_CONTINUATION_TOOLS)

        def _terminal_trigger(
            self, assistant_message, *, enabled: bool
        ) -> dict[str, Any] | None:
            if not enabled:
                return None
            tool_calls = list(getattr(assistant_message, "tool_calls", None) or [])
            terminal_tools = self._terminal_continuation_tool_names()
            matched_tools = [
                call for call in tool_calls if _tool_call_name(call) in terminal_tools
            ]
            if matched_tools:
                return {
                    "reason": "terminal_tool_call",
                    "tool_calls": _tool_call_rows(matched_tools),
                }
            content = str(getattr(assistant_message, "content", "") or "").lower()
            if (
                not tool_calls
                and args.terminal_continuation_text_check
                and (
                    ("transfer" in content and "human" in content)
                    or ("cannot" in content and "policy" in content)
                    or ("unable" in content and "policy" in content)
                )
            ):
                return {
                    "reason": "terminal_text",
                    "content_preview": str(getattr(assistant_message, "content", "") or "")[:500],
                }
            return None

        def _terminal_continuation_trigger(self, assistant_message) -> dict[str, Any] | None:
            return self._terminal_trigger(
                assistant_message,
                enabled=args.terminal_continuation_check,
            )

        def _terminal_plan_state_trigger(self, assistant_message) -> dict[str, Any] | None:
            return self._terminal_trigger(
                assistant_message,
                enabled=args.terminal_plan_state_check,
            )

        def _maybe_terminal_plan_state_check(self, assistant_message, state, phase: str):
            trigger = self._terminal_plan_state_trigger(assistant_message)
            if trigger is None:
                return assistant_message
            checks_used = getattr(self, "_terminal_plan_state_checks_used", 0)
            if checks_used >= args.terminal_plan_state_max_checks:
                self._trace(
                    {
                        "decision_node": "terminal_plan_state_check_skipped",
                        "phase": phase,
                        "skip_reason": "max_checks_exhausted",
                        "checks_used": checks_used,
                        "max_checks": args.terminal_plan_state_max_checks,
                        "trigger": trigger,
                    }
                )
                return assistant_message
            self._terminal_plan_state_checks_used = checks_used + 1

            original_assistant = {
                "content": str(getattr(assistant_message, "content", "") or ""),
                "tool_calls": _tool_call_rows(
                    list(getattr(assistant_message, "tool_calls", None) or [])
                ),
            }
            classifier_prompt = (
                "<openviking_terminal_plan_state_check>\n"
                "Classify whether the assistant's pending terminal handoff/refusal/no-op "
                "would prematurely stop the task. This is a benchmark-neutral plan-state "
                "check: use only the visible conversation, tool observations, domain policy, "
                "and pending terminal action. Do not use hidden evaluator expectations.\n\n"
                "Return decision=continue when either (a) a separately grounded subgoal "
                "remains both legal and actionable without mutating the blocked object or "
                "violating an immutable boundary, or (b) the pending terminal action is a "
                "human transfer but visible policy only requires explaining the blocked "
                "subgoal or asking a narrow supported follow-up. If visible policy explicitly "
                "requires human handoff, or the next step would force the blocked operation, "
                "guess missing state, or continue pointlessly, return decision=allow_terminal.\n\n"
                "Look for these generic states:\n"
                "- One requested subgoal is blocked by policy or tool capability.\n"
                "- Another independent user goal is still legal, grounded in the dialogue or "
                "tool observations, and can be completed or clarified automatically.\n"
                "- The assistant can safely give a non-terminal explanation of a blocked "
                "subgoal and ask whether the user wants a supported alternative, without "
                "calling a transfer tool.\n"
                "- The next step respects immutable boundaries such as object identity, route, "
                "passenger count, order items, payment source, and domain policy.\n\n"
                "Return JSON only with this schema:\n"
                "{\n"
                '  "decision": "allow_terminal or continue",\n'
                '  "blocked_subgoals": [{"subgoal": "short phrase", "reason": "why blocked"}],\n'
                '  "still_legal_independent_subgoals": [{"subgoal": "short phrase", "next_step": "tool or question", "grounding": "visible evidence"}],\n'
                '  "required_user_choices": ["choice still required before any automatic action"],\n'
                '  "immutable_boundary": ["object/field/policy boundary"],\n'
                '  "continuation_instruction": "one short instruction for the next assistant step"\n'
                "}\n\n"
                f"Original terminal trigger: {json.dumps(trigger, ensure_ascii=False, sort_keys=True)}\n"
                "Pending assistant terminal action:\n"
                f"{json.dumps(original_assistant, ensure_ascii=False, sort_keys=True)}\n"
                "</openviking_terminal_plan_state_check>"
            )
            try:
                classifier_response = generate(
                    model=self.llm,
                    tools=[],
                    messages=state.system_messages
                    + state.messages
                    + [SystemMessage(role="system", content=classifier_prompt)],
                    call_name="terminal_plan_state_classifier",
                    **self.llm_args,
                )
            except json.JSONDecodeError as exc:
                self._trace(
                    {
                        "decision_node": "terminal_plan_state_check",
                        "phase": phase,
                        "checks_used": self._terminal_plan_state_checks_used,
                        "max_checks": args.terminal_plan_state_max_checks,
                        "trigger": trigger,
                        "decision": "allow_terminal",
                        "classifier_generate_error": str(exc),
                    }
                )
                return assistant_message
            classifier_content = str(getattr(classifier_response, "content", "") or "")
            try:
                plan_state = _parse_json_object(classifier_content)
            except (json.JSONDecodeError, ValueError) as exc:
                self._trace(
                    {
                        "decision_node": "terminal_plan_state_check",
                        "phase": phase,
                        "checks_used": self._terminal_plan_state_checks_used,
                        "max_checks": args.terminal_plan_state_max_checks,
                        "trigger": trigger,
                        "decision": "allow_terminal",
                        "classifier_parse_error": str(exc),
                        "classifier_raw_preview": classifier_content[:1200],
                    }
                )
                return assistant_message

            decision = str(plan_state.get("decision") or "allow_terminal").strip().lower()
            if decision != "continue":
                self._trace(
                    {
                        "decision_node": "terminal_plan_state_check",
                        "phase": phase,
                        "checks_used": self._terminal_plan_state_checks_used,
                        "max_checks": args.terminal_plan_state_max_checks,
                        "trigger": trigger,
                        "decision": "allow_terminal",
                        "plan_state": plan_state,
                    }
                )
                return assistant_message

            continuation_instruction = _short_text(
                plan_state.get("continuation_instruction")
                or "Continue only with the legal independent subgoal identified above.",
                max_chars=700,
            )
            continuation_prompt = (
                "<openviking_terminal_plan_state_continue>\n"
                "The previous terminal action appears premature because the plan-state "
                "classifier found a legal independent subgoal that remains actionable. "
                "Continue only with that grounded subgoal or ask the required narrow "
                "clarification. Do not force the blocked operation and do not invent hidden "
                "requirements.\n"
                f"Classifier JSON: {json.dumps(plan_state, ensure_ascii=False, sort_keys=True)}\n"
                f"Instruction: {continuation_instruction}\n"
                "</openviking_terminal_plan_state_continue>"
            )
            regenerated = self._generate(
                state.system_messages
                + state.messages
                + [SystemMessage(role="system", content=continuation_prompt)]
            )
            self._trace(
                {
                    "decision_node": "terminal_plan_state_check",
                    "phase": phase,
                    "checks_used": self._terminal_plan_state_checks_used,
                    "max_checks": args.terminal_plan_state_max_checks,
                    "trigger": trigger,
                    "decision": "continue",
                    "plan_state": plan_state,
                    "regenerated_tool_calls": _tool_call_rows(
                        list(getattr(regenerated, "tool_calls", None) or [])
                    ),
                    "regenerated_content_preview": str(
                        getattr(regenerated, "content", "") or ""
                    )[:500],
                }
            )
            return regenerated

        def _maybe_terminal_continuation(self, assistant_message, state, phase: str):
            trigger = self._terminal_continuation_trigger(assistant_message)
            if trigger is None:
                return assistant_message
            checks_used = getattr(self, "_terminal_continuation_checks_used", 0)
            if checks_used >= args.terminal_continuation_max_checks:
                self._trace(
                    {
                        "decision_node": "terminal_continuation_check_skipped",
                        "phase": phase,
                        "skip_reason": "max_checks_exhausted",
                        "checks_used": checks_used,
                        "max_checks": args.terminal_continuation_max_checks,
                        "trigger": trigger,
                    }
                )
                return assistant_message
            self._terminal_continuation_checks_used = checks_used + 1

            prompt = (
                "<openviking_terminal_continuation_check>\n"
                "You were about to produce a terminal handoff, refusal, or no-op.\n"
                "Before doing that, re-check the current task state in a benchmark-neutral way:\n"
                "- Is only one operation blocked by policy, while another independent user "
                "subrequest can still be handled automatically?\n"
                "- Is there a policy-valid alternative action, missing choice, or confirmation "
                "that the automated agent can ask for or complete?\n"
                "- Would the pending terminal action prematurely abandon the user's remaining "
                "goal?\n"
                "- Would any alternative action violate an immutable boundary such as object "
                "identity, route, passenger count, payment source, or domain policy?\n\n"
                "If a legal automated next step remains, continue with that step or ask the "
                "needed choice/confirmation. If no legal automated next step remains, repeat "
                "the terminal action and briefly explain why.\n"
                f"Original terminal trigger: {json.dumps(trigger, ensure_ascii=False, sort_keys=True)}\n"
                "</openviking_terminal_continuation_check>"
            )
            regenerated = self._generate(
                state.system_messages
                + state.messages
                + [SystemMessage(role="system", content=prompt)]
            )
            self._trace(
                {
                    "decision_node": "terminal_continuation_check",
                    "phase": phase,
                    "checks_used": self._terminal_continuation_checks_used,
                    "max_checks": args.terminal_continuation_max_checks,
                    "trigger": trigger,
                    "regenerated_tool_calls": _tool_call_rows(
                        list(getattr(regenerated, "tool_calls", None) or [])
                    ),
                    "regenerated_content_preview": str(
                        getattr(regenerated, "content", "") or ""
                    )[:500],
                }
            )
            return regenerated

        def _final_response_consequence_trigger(self, assistant_message) -> dict[str, Any] | None:
            if not args.write_consequence_final_response_check:
                return None
            if not getattr(self, "_write_consequence_saw_write_result", False):
                return None
            tool_calls = list(getattr(assistant_message, "tool_calls", None) or [])
            if tool_calls:
                return None
            content = str(getattr(assistant_message, "content", "") or "")
            if not content.strip():
                return None
            lowered = content.lower()
            has_numeric_value = any(char.isdigit() for char in content) or "$" in content
            consequence_terms = (
                "refund",
                "savings",
                "saved",
                "amount",
                "fee",
                "charge",
                "payment",
                "credit card",
                "gift card",
                "baggage",
                "passenger",
            )
            if has_numeric_value and any(term in lowered for term in consequence_terms):
                return {"content_preview": content[:700]}
            return None

        def _maybe_write_consequence_final_response_check(
            self, assistant_message, state, phase: str
        ):
            trigger = self._final_response_consequence_trigger(assistant_message)
            if trigger is None:
                return assistant_message
            checks_used = getattr(
                self, "_write_consequence_final_response_checks_used", 0
            )
            if checks_used >= args.write_consequence_final_response_max_checks:
                self._trace(
                    {
                        "decision_node": "write_consequence_final_response_check_skipped",
                        "phase": phase,
                        "skip_reason": "max_checks_exhausted",
                        "checks_used": checks_used,
                        "max_checks": args.write_consequence_final_response_max_checks,
                        "trigger": trigger,
                    }
                )
                return assistant_message
            self._write_consequence_final_response_checks_used = checks_used + 1

            original_content = str(getattr(assistant_message, "content", "") or "")
            prompt = (
                "<openviking_write_consequence_final_response_check>\n"
                "Before sending the final user-facing response, audit any communicated "
                "write consequences using only the visible conversation, tool observations, "
                "domain policy, and tool results already in context.\n"
                "This is a benchmark-neutral provenance check, not a hidden-gold evaluator "
                "check.\n\n"
                "Check any exact amount, refund, saving, fee, charge, payment method, "
                "passenger count, baggage count, route, date, cabin, status, or other "
                "user-visible consequence. If an exact value is visible or can be computed "
                "from visible tool results, correct the response. If an exact value is not "
                "grounded, remove it or state only what is grounded. Prefer executed write "
                "tool results over earlier assistant proposals or quotes. Do not say that a "
                "fee, charge, deduction, refund, count, status, or other secondary "
                "consequence was applied unless it appears in the executed write arguments "
                "or returned tool result. If multiple executed write results contain new "
                "payment/refund deltas, compute any aggregate amount from those visible "
                "post-write deltas and do not subtract unexecuted proposed charges. Do not "
                "call tools. Do not invent task-specific rules.\n\n"
                "Original final response:\n"
                f"{original_content}\n"
                "</openviking_write_consequence_final_response_check>"
            )
            regenerated = self._generate(
                state.system_messages
                + state.messages
                + [SystemMessage(role="system", content=prompt)]
            )
            regenerated_tool_calls = list(getattr(regenerated, "tool_calls", None) or [])
            used_regenerated = not regenerated_tool_calls
            regenerated_content = str(getattr(regenerated, "content", "") or "")
            self._trace(
                {
                    "decision_node": "write_consequence_final_response_check",
                    "phase": phase,
                    "checks_used": self._write_consequence_final_response_checks_used,
                    "max_checks": args.write_consequence_final_response_max_checks,
                    "trigger": trigger,
                    "regenerated_tool_calls": _tool_call_rows(regenerated_tool_calls),
                    "used_regenerated": used_regenerated,
                    "content_changed": used_regenerated
                    and regenerated_content.strip() != original_content.strip(),
                    "regenerated_content_preview": regenerated_content[:700],
                }
            )
            if not used_regenerated:
                return assistant_message
            return regenerated

        def generate_next_message(self, message, state: LLMAgentState):
            if isinstance(message, MultiToolMessage):
                if getattr(self, "_write_consequence_awaiting_write_result", False):
                    self._write_consequence_saw_write_result = True
                    self._write_consequence_awaiting_write_result = False
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
            assistant_message = self._maybe_terminal_plan_state_check(
                assistant_message, state, "initial_generation"
            )
            assistant_message = self._maybe_terminal_continuation(
                assistant_message, state, "initial_generation"
            )
            if not args.no_memory and args.retrieval_mode in {"prewrite", "first_user_prewrite"}:
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
                        initial_write_calls = list(write_calls)
                        assistant_message = self._generate(
                            state.system_messages
                            + state.messages
                            + [SystemMessage(role="system", content=prompt)]
                        )
                        assistant_message = self._maybe_terminal_plan_state_check(
                            assistant_message, state, "after_prewrite_regeneration"
                        )
                        assistant_message = self._maybe_terminal_continuation(
                            assistant_message, state, "after_prewrite_regeneration"
                        )
                        regenerated_tool_calls = list(
                            getattr(assistant_message, "tool_calls", None) or []
                        )
                        regenerated_write_calls = [
                            call
                            for call in regenerated_tool_calls
                            if _is_write_tool_call(call)
                        ]
                        drift_detected = _tool_call_signatures(
                            regenerated_write_calls
                        ) != _tool_call_signatures(initial_write_calls)
                        self._trace(
                            {
                                "decision_node": "after_prewrite_regeneration",
                                "drift_detected": drift_detected,
                                "drift_retry_enabled": args.prewrite_drift_retry,
                                "initial_tool_calls": _tool_call_rows(initial_write_calls),
                                "regenerated_tool_calls": _tool_call_rows(
                                    regenerated_write_calls
                                ),
                            }
                        )
                        if (
                            args.prewrite_drift_retry
                            and drift_detected
                            and regenerated_write_calls
                        ):
                            retry_query = _tool_call_query(
                                regenerated_write_calls, state.messages
                            )
                            retry_block, retry_matches = self._retrieve(
                                retry_query,
                                search_limit=args.prewrite_retrieval_top_k,
                                inject_limit=args.prewrite_inject_top_k,
                                inject_max_chars=args.prewrite_memory_inject_max_chars,
                            )
                            self._trace(
                                {
                                    "decision_node": "before_write_tool_call_drift_retry",
                                    "query": retry_query,
                                    "search_limit": args.prewrite_retrieval_top_k,
                                    "inject_limit": args.prewrite_inject_top_k,
                                    "inject_max_chars": args.prewrite_memory_inject_max_chars,
                                    "match_count": len(retry_matches),
                                    "matches": retry_matches,
                                    **self._trace_injection_fields(
                                        retry_block, retry_matches
                                    ),
                                    "tool_calls": _tool_call_rows(
                                        regenerated_write_calls
                                    ),
                                }
                            )
                            if retry_block:
                                retry_prompt = (
                                    "The pending write-like tool calls changed after memory "
                                    "was applied. Before executing the revised write-like tool "
                                    "calls, use these OpenViking memories only when they match "
                                    "the current task:\n\n"
                                    + retry_block
                                )
                                assistant_message = self._generate(
                                    state.system_messages
                                    + state.messages
                                    + [SystemMessage(role="system", content=retry_prompt)]
                                )
                                assistant_message = self._maybe_terminal_plan_state_check(
                                    assistant_message, state, "after_prewrite_drift_retry"
                                )
                                assistant_message = self._maybe_terminal_continuation(
                                    assistant_message, state, "after_prewrite_drift_retry"
                                )
                                final_tool_calls = list(
                                    getattr(assistant_message, "tool_calls", None) or []
                                )
                                final_write_calls = [
                                    call
                                    for call in final_tool_calls
                                    if _is_write_tool_call(call)
                                ]
                                self._trace(
                                    {
                                        "decision_node": "after_prewrite_drift_retry",
                                        "drift_still_detected": _tool_call_signatures(
                                            final_write_calls
                                        )
                                        != _tool_call_signatures(regenerated_write_calls),
                                        "retry_source_tool_calls": _tool_call_rows(
                                            regenerated_write_calls
                                        ),
                                        "final_tool_calls": _tool_call_rows(
                                            final_write_calls
                                        ),
                                    }
                                )
            assistant_message = self._maybe_write_consequence_final_response_check(
                assistant_message, state, "before_assistant_response"
            )
            final_tool_calls = list(getattr(assistant_message, "tool_calls", None) or [])
            if any(_is_write_tool_call(call) for call in final_tool_calls):
                self._write_consequence_awaiting_write_result = True
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
    parser.add_argument("--openviking-agent-id")
    parser.add_argument("--openviking-timeout", type=float, default=600.0)
    parser.add_argument("--openviking-wait-timeout", type=int, default=1800)
    parser.add_argument("--search-uri")
    parser.add_argument("--retrieval-top-k", type=int, default=4)
    parser.add_argument("--first-user-retrieval-top-k", type=int)
    parser.add_argument("--first-user-inject-top-k", type=int)
    parser.add_argument("--prewrite-retrieval-top-k", type=int)
    parser.add_argument("--prewrite-inject-top-k", type=int)
    parser.add_argument(
        "--memory-inject-max-chars",
        type=int,
        default=0,
        help=(
            "Optional hard cap for the total characters of injected memory blocks at "
            "each retrieval decision. 0 means no character cap."
        ),
    )
    parser.add_argument(
        "--first-user-memory-inject-max-chars",
        type=int,
        help="Optional hard cap overriding --memory-inject-max-chars for first-user retrieval.",
    )
    parser.add_argument(
        "--prewrite-memory-inject-max-chars",
        type=int,
        help="Optional hard cap overriding --memory-inject-max-chars for pre-write retrieval.",
    )
    parser.add_argument("--fixed-first-user-file", type=Path)
    parser.add_argument("--scope-prompt-file", type=Path)
    parser.add_argument(
        "--terminal-continuation-check",
        action="store_true",
        help=(
            "Diagnostic postcheck: before terminal handoff/refusal actions, regenerate "
            "once with a generic policy-continuation check."
        ),
    )
    parser.add_argument(
        "--terminal-plan-state-check",
        action="store_true",
        help=(
            "Diagnostic control-plane treatment: before terminal handoff/refusal actions, "
            "run a cheap JSON plan-state classifier and only regenerate when it finds a "
            "legal independent subgoal that remains actionable."
        ),
    )
    parser.add_argument(
        "--terminal-continuation-tool",
        dest="terminal_continuation_tools",
        action="append",
        default=[],
        help=(
            "Tool name treated as terminal/handoff for --terminal-continuation-check. "
            "May be repeated; defaults to transfer_to_human_agents when omitted."
        ),
    )
    parser.add_argument(
        "--terminal-continuation-text-check",
        action="store_true",
        help="Also run terminal-continuation check on obvious textual refusal/handoff.",
    )
    parser.add_argument(
        "--terminal-continuation-max-checks",
        type=int,
        default=1,
        help=(
            "Maximum terminal-continuation full regenerations per simulation. "
            "Further terminal triggers are traced and skipped. Defaults to 1."
        ),
    )
    parser.add_argument(
        "--terminal-plan-state-max-checks",
        type=int,
        default=DEFAULT_TERMINAL_PLAN_STATE_MAX_CHECKS,
        help=(
            "Maximum terminal plan-state classifier checks per simulation. Further "
            "terminal triggers are traced and skipped. Defaults to "
            f"{DEFAULT_TERMINAL_PLAN_STATE_MAX_CHECKS}."
        ),
    )
    parser.add_argument(
        "--train-outcome-mode",
        choices=[
            TRAIN_OUTCOME_TRANSCRIPT_ONLY,
            TRAIN_OUTCOME_REWARD_SUMMARY,
            TRAIN_OUTCOME_EVALUATOR_REPORT,
        ],
        default=TRAIN_OUTCOME_TRANSCRIPT_ONLY,
        help=(
            "Append train-split evaluator outcome feedback to sessions before "
            "OpenViking memory extraction. Non-default modes are oracle/evaluator-"
            "augmented variants and should not be mixed with transcript-only results."
        ),
    )
    parser.add_argument(
        "--train-transcript-format",
        choices=[TRAIN_TRANSCRIPT_OPENVIKING_TEXT, TRAIN_TRANSCRIPT_CUSTOM_LIKE],
        default=TRAIN_TRANSCRIPT_OPENVIKING_TEXT,
        help=(
            "How to replay TAU-2 train messages into OpenViking sessions. "
            "openviking_text preserves the original PR-B text format; custom_like "
            "matches the older custom-procedure payload style with role-prefixed "
            "messages plus tool-call/tool-response blocks."
        ),
    )
    parser.add_argument(
        "--train-include-system-prompt",
        action="store_true",
        help=(
            "Prepend the domain policy as a user-visible system: block during "
            "training memory extraction, matching the older custom payload shape."
        ),
    )
    parser.add_argument(
        "--train-routing-hint",
        action="store_true",
        help=(
            "Append a non-gold memory-routing hint before training commit. This is "
            "an explicit ablation knob and should not be folded into transcript-only "
            "baseline claims."
        ),
    )
    parser.add_argument(
        "--train-skip-failed-sessions",
        action="store_true",
        help=(
            "Skip reward<1 train sessions when building positive trajectory memory. "
            "This uses train-split outcome only for corpus admission; failure lessons "
            "should use non-transcript train_outcome_mode variants instead."
        ),
    )
    parser.add_argument(
        "--train-tool-output-max-chars",
        type=int,
        default=DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS,
        help=(
            "Maximum characters kept for each tool-response block when "
            f"--train-transcript-format={TRAIN_TRANSCRIPT_CUSTOM_LIKE}. Defaults to "
            f"{DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS}."
        ),
    )
    parser.add_argument(
        "--compress-failure-memories",
        action="store_true",
        help=(
            "When using evaluator-augmented training, compress memories touched by "
            "failed train sessions into short negative-boundary reflections before "
            "injection. This is an eval-time diagnostic treatment, not a corpus rewrite."
        ),
    )
    parser.add_argument(
        "--memory-read-selector",
        action="store_true",
        help=(
            "Diagnostic retrieval treatment: after search, ask the agent LLM which "
            "candidate memories should be read and injected instead of injecting by "
            "rank alone."
        ),
    )
    parser.add_argument(
        "--prewrite-drift-retry",
        action="store_true",
        help=(
            "Diagnostic prewrite treatment: if regenerated write-like tool calls differ "
            "from the calls used for the prewrite retrieval query, retrieve once more "
            "against the revised write set and regenerate once."
        ),
    )
    parser.add_argument(
        "--write-consequence-final-response-check",
        action="store_true",
        help=(
            "Diagnostic control-plane treatment: before final natural-language responses "
            "that communicate amounts, refunds, payments, baggage, passengers, or other "
            "user-visible write consequences, run a grounding check against visible "
            "conversation and tool-result evidence."
        ),
    )
    parser.add_argument(
        "--write-consequence-final-response-max-checks",
        type=int,
        default=DEFAULT_WRITE_CONSEQUENCE_FINAL_RESPONSE_MAX_CHECKS,
        help=(
            "Maximum final-response consequence audits per simulation. "
            f"Defaults to {DEFAULT_WRITE_CONSEQUENCE_FINAL_RESPONSE_MAX_CHECKS}."
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
    if args.compress_failure_memories and args.train_outcome_mode == TRAIN_OUTCOME_TRANSCRIPT_ONLY:
        parser.error("--compress-failure-memories requires non-transcript train_outcome_mode")
    if args.memory_read_selector and args.no_memory:
        parser.error("--memory-read-selector cannot be used with --no-memory")
    if args.prewrite_drift_retry and args.no_memory:
        parser.error("--prewrite-drift-retry cannot be used with --no-memory")
    if args.terminal_continuation_max_checks < 0:
        parser.error("--terminal-continuation-max-checks must be non-negative")
    if args.terminal_plan_state_max_checks < 0:
        parser.error("--terminal-plan-state-max-checks must be non-negative")
    if args.write_consequence_final_response_max_checks < 0:
        parser.error("--write-consequence-final-response-max-checks must be non-negative")
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
                "openviking_agent_id",
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
        agent_name = args.base_agent
        if (
            args.terminal_continuation_check
            or args.terminal_plan_state_check
            or args.write_consequence_final_response_check
            or args.scope_prompt_file is not None
        ):
            trace_path.touch()
            _register_memory_agent(args, trace_path)
            agent_name = AGENT_NAME
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
            agent=agent_name,
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
            "scope_prompt_file": str(args.scope_prompt_file) if args.scope_prompt_file else None,
            "terminal_continuation_check": args.terminal_continuation_check,
            "terminal_continuation_tools": args.terminal_continuation_tools
            or list(DEFAULT_TERMINAL_CONTINUATION_TOOLS),
            "terminal_continuation_text_check": args.terminal_continuation_text_check,
            "terminal_continuation_max_checks": args.terminal_continuation_max_checks,
            "terminal_plan_state_check": args.terminal_plan_state_check,
            "terminal_plan_state_max_checks": args.terminal_plan_state_max_checks,
            "write_consequence_final_response_check": (
                args.write_consequence_final_response_check
            ),
            "write_consequence_final_response_max_checks": (
                args.write_consequence_final_response_max_checks
            ),
            "compress_failure_memories": args.compress_failure_memories,
            "memory_read_selector": args.memory_read_selector,
            "retrieval_trace": str(trace_path)
            if (
                args.terminal_continuation_check
                or args.terminal_plan_state_check
                or args.write_consequence_final_response_check
                or args.scope_prompt_file
            )
            else None,
            "eval_results": str(eval_results),
            "metrics": _metrics(eval_results),
        }
        _write_json(summary_path, summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    corpus = _train(args, train_results, corpus_manifest)
    if args.compress_failure_memories:
        failure_sidecar = _load_failure_memory_sidecar(corpus)
        if not failure_sidecar:
            raise RuntimeError(
                "--compress-failure-memories requires failure_memory_sidecar in the corpus "
                "manifest; rebuild the evaluator-augmented corpus"
            )
        if int(failure_sidecar.get("memory_diff_error_count") or 0) > 0:
            raise RuntimeError(
                "failure_memory_sidecar has memory_diff read errors; rebuild the corpus before "
                "running compressed failure-memory injection"
            )
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
    _register_memory_agent(args, trace_path, corpus)
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
        "seed": args.seed,
        "fixed_first_user_file": str(args.fixed_first_user_file)
        if args.fixed_first_user_file
        else None,
        "scope_prompt_file": str(args.scope_prompt_file) if args.scope_prompt_file else None,
        "train_outcome_mode": args.train_outcome_mode,
        "train_transcript_format": args.train_transcript_format,
        "train_include_system_prompt": bool(args.train_include_system_prompt),
        "train_routing_hint": bool(args.train_routing_hint),
        "train_tool_output_max_chars": args.train_tool_output_max_chars,
        "terminal_continuation_check": args.terminal_continuation_check,
        "terminal_continuation_tools": args.terminal_continuation_tools
        or list(DEFAULT_TERMINAL_CONTINUATION_TOOLS),
        "terminal_continuation_text_check": args.terminal_continuation_text_check,
        "terminal_continuation_max_checks": args.terminal_continuation_max_checks,
        "terminal_plan_state_check": args.terminal_plan_state_check,
        "terminal_plan_state_max_checks": args.terminal_plan_state_max_checks,
        "write_consequence_final_response_check": args.write_consequence_final_response_check,
        "write_consequence_final_response_max_checks": (
            args.write_consequence_final_response_max_checks
        ),
        "compress_failure_memories": args.compress_failure_memories,
        "memory_read_selector": args.memory_read_selector,
        "prewrite_drift_retry": args.prewrite_drift_retry,
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
