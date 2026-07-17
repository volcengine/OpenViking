#!/usr/bin/env python3
"""Run the Journey to the West Wiki-first/Resource-only VikingBot benchmark."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_ROOT = REPO_ROOT / "bot"
for import_root in (str(REPO_ROOT), str(BOT_ROOT)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from dotenv import load_dotenv  # noqa: E402
from vikingbot.agent.loop import AgentLoop  # noqa: E402
from vikingbot.agent.tools.base import Tool, ToolContext  # noqa: E402
from vikingbot.agent.tools.ov_file import VikingMultiReadTool, VikingSearchTool  # noqa: E402
from vikingbot.agent.tools.registry import ToolRegistry  # noqa: E402
from vikingbot.bus.queue import MessageBus  # noqa: E402
from vikingbot.config.loader import load_config  # noqa: E402
from vikingbot.config.schema import SessionKey  # noqa: E402
from vikingbot.providers.vlm_adapter import VLMProviderAdapter  # noqa: E402
from vikingbot.utils.helpers import cal_str_tokens  # noqa: E402

DEFAULT_ENV_FILE = Path.home() / ".openviking_benchmark_env"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "ep-20260514141842-c7s2n"
DEFAULT_QUESTIONS = (
    REPO_ROOT / "examples/wiki-demo/journey-to-the-west/ab-eval/wiki-first-questions.json"
)

WIKI_URI = "viking://user/jiajie/memories/entities"
RESOURCE_URI = "viking://resources/demos/journey-to-the-west"
WIKI_SOURCE = Path(
    "/Users/bytedance/.openviking/wiki_test/viking/default/user/jiajie/memories/entities"
)
RESOURCE_SOURCE = Path(
    "/Users/bytedance/.openviking/wiki_test/viking/default/resources/demos/journey-to-the-west"
)
TOOL_ALLOWLIST = {"openviking_search", "openviking_multi_read"}
STAGE_TOKEN_BUDGET = 3000

INLINE_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^\n)]*\)")
REFERENCE_LINK_RE = re.compile(r"!?\[([^\]]*)\]\s*\[[^\]]*\]")
REFERENCE_DEF_RE = re.compile(r"(?m)^\s*\[[^\]]+\]:\s*\S+.*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_tree_snapshot(root: Path) -> dict[str, Any]:
    """Match ``find | sort | shasum | shasum`` used by the frozen corpus."""
    if not root.is_dir():
        return {"files": 0, "composite_sha256": None, "error": f"missing: {root}"}
    files = sorted(path for path in root.rglob("*") if path.is_file())
    composite = hashlib.sha256()
    for path in files:
        relative = f"./{path.relative_to(root).as_posix()}"
        line = f"{sha256_file(path)}  {relative}\n"
        composite.update(line.encode("utf-8"))
    return {"files": len(files), "composite_sha256": composite.hexdigest()}


def source_snapshot() -> dict[str, Any]:
    return {
        "resource": source_tree_snapshot(RESOURCE_SOURCE),
        "wiki": source_tree_snapshot(WIKI_SOURCE),
    }


def expected_snapshot(question_set: dict[str, Any]) -> dict[str, Any]:
    frozen = question_set.get("corpus_snapshot", {})
    return {
        "resource": {
            "files": frozen.get("resource_files"),
            "composite_sha256": frozen.get("resource_composite_sha256"),
        },
        "wiki": {
            "files": frozen.get("wiki_files"),
            "composite_sha256": frozen.get("wiki_composite_sha256"),
        },
    }


def snapshots_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for source in ("resource", "wiki"):
        if left.get(source, {}).get("files") != right.get(source, {}).get("files"):
            return False
        if left.get(source, {}).get("composite_sha256") != right.get(source, {}).get(
            "composite_sha256"
        ):
            return False
    return True


def estimate_tokens(text: str) -> int:
    return int(cal_str_tokens(text or "", text_type="mixed"))


def markdown_link_count(text: str) -> int:
    return (
        len(INLINE_LINK_RE.findall(text))
        + len(REFERENCE_LINK_RE.findall(text))
        + len(REFERENCE_DEF_RE.findall(text))
    )


def strip_markdown_links(text: str) -> str:
    """Remove Markdown link destinations while retaining visible labels."""
    stripped = INLINE_LINK_RE.sub(lambda match: match.group(1), text)
    stripped = REFERENCE_LINK_RE.sub(lambda match: match.group(1), stripped)
    stripped = REFERENCE_DEF_RE.sub("", stripped)
    return stripped


def redact_secret(message: str, secret: str) -> str:
    if secret:
        return message.replace(secret, "[REDACTED]")
    return message


def normalize_uri(uri: Any) -> str:
    value = str(uri or "").strip()
    return value if value == "viking://" else value.rstrip("/")


def uri_in_root(uri: Any, root: str) -> bool:
    candidate = normalize_uri(uri)
    normalized_root = normalize_uri(root)
    path_part = candidate.removeprefix("viking://")
    if (
        not candidate.startswith("viking://")
        or "%" in candidate
        or "\\" in candidate
        or "?" in candidate
        or "#" in candidate
        or "\x00" in candidate
        or "//" in path_part
        or any(segment in {".", ".."} for segment in path_part.split("/"))
    ):
        return False
    return candidate == normalized_root or candidate.startswith(f"{normalized_root}/")


def extract_single_read(raw: str, uri: str) -> tuple[bool, str]:
    if not raw or raw.lstrip().startswith("Error"):
        return False, raw or "empty tool response"
    start_marker = f"--- START OF {uri} ---"
    end_marker = f"--- END OF {uri} ---"
    start = raw.find(start_marker)
    end = raw.rfind(end_marker)
    if start < 0 or end < 0 or end < start:
        return False, raw
    content = raw[start + len(start_marker) : end].strip("\n")
    if content.lstrip().startswith("ERROR:"):
        return False, content
    return True, content


@dataclass
class PolicyState:
    exp: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    trace: list[dict[str, Any]] = field(default_factory=list)
    scope_violations: list[str] = field(default_factory=list)
    protocol_errors: list[str] = field(default_factory=list)
    search_calls: int = 0
    wiki_search_succeeded: bool = False
    wiki_read_calls: int = 0
    resource_read_calls: int = 0
    wiki_uris_read: list[str] = field(default_factory=list)
    resource_uris_read: list[str] = field(default_factory=list)
    read_sequence: list[dict[str, Any]] = field(default_factory=list)
    rejected_uris: list[str] = field(default_factory=list)
    retrieved_wiki_tokens: int = 0
    retrieved_resource_tokens: int = 0
    fallback_used: bool = False

    def violation(self, message: str) -> None:
        if message not in self.scope_violations:
            self.scope_violations.append(message)

    def protocol(self, message: str) -> None:
        if message not in self.protocol_errors:
            self.protocol_errors.append(message)

    def remaining(self, source: str) -> int:
        used = self.retrieved_wiki_tokens if source == "wiki" else self.retrieved_resource_tokens
        return max(0, STAGE_TOKEN_BUDGET - used)


class RestrictedSearchTool(Tool):
    def __init__(self, state: PolicyState, delegate: VikingSearchTool):
        self.state = state
        self.delegate = delegate

    @property
    def name(self) -> str:
        return "openviking_search"

    @property
    def description(self) -> str:
        if self.state.exp == "A":
            return (
                "Search the benchmark corpus. The first search must explicitly set "
                f"target_uri={WIKI_URI}. Resource search is allowed only after at least "
                "one Wiki page has been read successfully."
            )
        return (
            f"Search the benchmark Resource corpus. target_uri must explicitly be {RESOURCE_URI}."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "target_uri": {"type": "string"},
                "min_score": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["query", "target_uri"],
        }

    async def execute(
        self,
        tool_context: ToolContext,
        query: str,
        target_uri: str,
        min_score: float = 0.35,
        **kwargs: Any,
    ) -> str:
        wiki_read_ready_at_request = bool(self.state.wiki_uris_read)
        async with self.state.lock:
            started = time.perf_counter()
            target = normalize_uri(target_uri)
            call_number = self.state.search_calls + 1
            self.state.search_calls = call_number
            allowed = False
            error = ""

            if self.state.exp == "A":
                if call_number == 1 and target != WIKI_URI:
                    error = f"first A search must target {WIKI_URI}"
                    self.state.protocol(error)
                    self.state.violation(error)
                elif uri_in_root(target, WIKI_URI):
                    allowed = True
                elif uri_in_root(target, RESOURCE_URI):
                    self.state.fallback_used = True
                    if not wiki_read_ready_at_request:
                        error = "Resource search occurred before a successful Wiki read"
                        self.state.protocol(error)
                        self.state.violation(error)
                    else:
                        allowed = True
                else:
                    error = f"search target outside benchmark roots: {target!r}"
                    self.state.violation(error)
            else:
                if target == RESOURCE_URI:
                    allowed = True
                else:
                    error = f"B search must target Resource root, got {target!r}"
                    self.state.violation(error)

            if allowed:
                result = await self.delegate.execute(
                    tool_context,
                    query=query,
                    target_uri=target,
                    min_score=min_score,
                )
                success = bool(result) and not result.lstrip().startswith("Error")
                if success and self.state.exp == "A" and uri_in_root(target, WIKI_URI):
                    self.state.wiki_search_succeeded = True
            else:
                result = f"Error: benchmark policy rejected search: {error}"
                success = False

            elapsed = time.perf_counter() - started
            self.state.trace.append(
                {
                    "tool_name": self.name,
                    "sanitized_args": {
                        "query": query[:500],
                        "target_uri": target,
                        "min_score": min_score,
                    },
                    "target_uri": target,
                    "read_uris": [],
                    "success": success,
                    "duration_seconds": round(elapsed, 6),
                    "estimated_output_tokens": estimate_tokens(result),
                    "links_before": 0,
                    "links_after": 0,
                    "budget": {"accepted": [], "rejected": []},
                    "policy_error": error or None,
                }
            )
            return result


class RestrictedMultiReadTool(Tool):
    def __init__(self, state: PolicyState, delegate: VikingMultiReadTool):
        self.state = state
        self.delegate = delegate

    @property
    def name(self) -> str:
        return "openviking_multi_read"

    @property
    def description(self) -> str:
        if self.state.exp == "A":
            return (
                "Read benchmark Wiki pages, or Resource pages only after a successful "
                "Wiki read. Each retrieval stage has a 3000-token delivery budget."
            )
        return (
            "Read only pages under the benchmark Resource root. Markdown link targets "
            "are removed and the delivery budget is 3000 tokens."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uris": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                }
            },
            "required": ["uris"],
        }

    def _classify(self, uri: str) -> str | None:
        if uri_in_root(uri, WIKI_URI):
            return "wiki"
        if uri_in_root(uri, RESOURCE_URI):
            return "resource"
        return None

    async def execute(self, tool_context: ToolContext, uris: list[str], **kwargs: Any) -> str:
        wiki_search_ready_at_request = self.state.wiki_search_succeeded
        wiki_read_ready_at_request = bool(self.state.wiki_uris_read)
        async with self.state.lock:
            started = time.perf_counter()
            normalized = [normalize_uri(uri) for uri in uris]
            source_types = [self._classify(uri) for uri in normalized]
            if "wiki" in source_types:
                self.state.wiki_read_calls += 1
            if "resource" in source_types:
                self.state.resource_read_calls += 1
                if self.state.exp == "A":
                    self.state.fallback_used = True

            accepted: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            output_parts: list[str] = []
            links_before = 0
            links_after = 0

            for uri, source in zip(normalized, source_types, strict=True):
                policy_error = ""
                if source is None:
                    policy_error = f"read URI outside benchmark roots: {uri!r}"
                    self.state.violation(policy_error)
                elif self.state.exp == "B" and source != "resource":
                    policy_error = f"B attempted Wiki/Entities read: {uri}"
                    self.state.violation(policy_error)
                elif (
                    self.state.exp == "A" and source == "wiki" and not wiki_search_ready_at_request
                ):
                    policy_error = "Wiki read occurred before the required Wiki search"
                    self.state.protocol(policy_error)
                    self.state.violation(policy_error)
                elif (
                    self.state.exp == "A"
                    and source == "resource"
                    and not wiki_read_ready_at_request
                ):
                    policy_error = "Resource read occurred before a successful Wiki read"
                    self.state.protocol(policy_error)
                    self.state.violation(policy_error)

                if policy_error:
                    rejected.append({"uri": uri, "reason": "policy", "detail": policy_error})
                    self.state.rejected_uris.append(uri)
                    continue

                raw = await self.delegate.execute(tool_context, uris=[uri])
                success, content = extract_single_read(raw, uri)
                if not success:
                    rejected.append({"uri": uri, "reason": "read_error"})
                    output_parts.append(f"ERROR reading {uri}: {content[:500]}")
                    continue

                if source == "resource":
                    before = markdown_link_count(content)
                    content = strip_markdown_links(content)
                    after = markdown_link_count(content)
                    links_before += before
                    links_after += after

                tokens = estimate_tokens(content)
                remaining = self.state.remaining(source)
                if tokens > remaining:
                    rejected.append(
                        {
                            "uri": uri,
                            "reason": "budget",
                            "estimated_tokens": tokens,
                            "remaining_tokens": remaining,
                        }
                    )
                    self.state.rejected_uris.append(uri)
                    continue

                if source == "wiki":
                    self.state.wiki_uris_read.append(uri)
                    self.state.retrieved_wiki_tokens += tokens
                else:
                    self.state.resource_uris_read.append(uri)
                    self.state.retrieved_resource_tokens += tokens
                self.state.read_sequence.append(
                    {
                        "order": len(self.state.read_sequence) + 1,
                        "source": source,
                        "uri": uri,
                        "estimated_tokens": tokens,
                    }
                )
                accepted.append({"uri": uri, "source": source, "estimated_tokens": tokens})
                output_parts.append(f"--- START OF {uri} ---\n{content}\n--- END OF {uri} ---")

            for item in rejected:
                output_parts.append(f"BENCHMARK POLICY REJECTED {item['uri']}: {item['reason']}")
            if not output_parts:
                output_parts.append("Error: no requested document passed benchmark policy")
            result = "\n\n".join(output_parts)
            elapsed = time.perf_counter() - started
            self.state.trace.append(
                {
                    "tool_name": self.name,
                    "sanitized_args": {"uris": normalized},
                    "target_uri": None,
                    "read_uris": normalized,
                    "success": bool(accepted),
                    "duration_seconds": round(elapsed, 6),
                    "estimated_output_tokens": sum(
                        int(item["estimated_tokens"]) for item in accepted
                    ),
                    "links_before": links_before,
                    "links_after": links_after,
                    "budget": {
                        "limit_tokens_per_stage": STAGE_TOKEN_BUDGET,
                        "accepted": accepted,
                        "rejected": rejected,
                        "wiki_delivered_tokens": self.state.retrieved_wiki_tokens,
                        "resource_delivered_tokens": self.state.retrieved_resource_tokens,
                    },
                    "policy_error": None,
                }
            )
            return result


def build_system_prompt(exp: str) -> str:
    common = (
        "You are answering one standalone Journey to the West question. You have no "
        "prior-turn context. Use only openviking_search and openviking_multi_read. "
        "Do not use outside knowledge as evidence. Give a direct Chinese answer after "
        "retrieval. Never attempt any URI outside the roots stated below."
    )
    if exp == "A":
        return (
            f"{common}\nExperiment protocol: Wiki-first. First search {WIKI_URI} with an "
            "explicit target_uri and wait for its result, then successfully read at least "
            "one Wiki page and wait for that result. Answer from Wiki if sufficient. Only "
            "when Wiki is insufficient may you search/read "
            f"{RESOURCE_URI}; doing so is fallback. Do not request later stages in the same "
            "tool-call batch."
        )
    return (
        f"{common}\nExperiment protocol: Resource-only. Every search must explicitly target "
        f"{RESOURCE_URI}, and every read URI must be under that root. Never access Wiki "
        "Entities and never follow links from Resource content."
    )


def make_provider(api_key: str, base_url: str, model: str, provider_name: str, config: Any) -> Any:
    from openviking.models.vlm.base import VLMFactory

    vlm = VLMFactory.create(
        {
            "provider": provider_name,
            "model": model,
            "api_key": api_key,
            "api_base": base_url,
            "temperature": 0.0,
            "thinking": bool(getattr(config.agents, "thinking", True)),
            **(
                {"timeout": config.agents.timeout}
                if getattr(config.agents, "timeout", None)
                else {}
            ),
        }
    )
    return VLMProviderAdapter(vlm_instance=vlm, default_model=model)


def make_restricted_registry(config: Any, state: PolicyState) -> ToolRegistry:
    registry = ToolRegistry(config=config)
    registry.register(RestrictedSearchTool(state, VikingSearchTool(config=config)))
    registry.register(RestrictedMultiReadTool(state, VikingMultiReadTool(config=config)))
    if set(registry.tool_names) != TOOL_ALLOWLIST:
        raise RuntimeError(f"unexpected benchmark tools: {registry.tool_names}")
    return registry


def reconciled_tool_trace(
    policy_trace: list[dict[str, Any]], tools_used: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Preserve AgentLoop call order, including calls rejected by schema validation."""
    queued: dict[str, list[dict[str, Any]]] = {}
    for trace in policy_trace:
        queued.setdefault(str(trace["tool_name"]), []).append(trace)
    complete: list[dict[str, Any]] = []
    for used in tools_used:
        name = str(used.get("tool_name", ""))
        matching = queued.get(name, [])
        if matching:
            complete.append(matching.pop(0))
            continue
        try:
            args = json.loads(str(used.get("args", "{}")))
        except (TypeError, json.JSONDecodeError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        sanitized = {
            key: value
            for key, value in args.items()
            if key in {"query", "target_uri", "min_score", "uris"}
        }
        complete.append(
            {
                "tool_name": name,
                "sanitized_args": sanitized,
                "target_uri": sanitized.get("target_uri"),
                "read_uris": sanitized.get("uris", []),
                "success": bool(used.get("execute_success")),
                "duration_seconds": round(float(used.get("duration", 0) or 0) / 1000, 6),
                "estimated_output_tokens": int(used.get("output_token", 0) or 0),
                "links_before": 0,
                "links_after": 0,
                "budget": {"accepted": [], "rejected": []},
                "policy_error": "tool call was rejected before policy wrapper execution",
            }
        )
    for remaining in queued.values():
        complete.extend(remaining)
    return complete


async def run_one(
    question: dict[str, Any],
    exp: str,
    config: Any,
    api_key: str,
    base_url: str,
    model: str,
    provider_name: str,
) -> dict[str, Any]:
    state = PolicyState(exp=exp)
    run_id = f"{question['id']}-{exp}-{uuid.uuid4().hex}"
    session_key = SessionKey(type="benchmark", channel_id="wiki", chat_id=run_id)
    started = time.perf_counter()
    response = ""
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    iterations = 0
    tools_used: list[dict[str, Any]] = []
    network_error: str | None = None
    agent: AgentLoop | None = None

    try:
        provider = make_provider(api_key, base_url, model, provider_name, config)
        agent = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=config.workspace_path,
            model=model,
            temperature=0.0,
            max_iterations=config.agents.max_tool_iterations,
            memory_window=0,
            config=config,
            eval=True,
            mcp_servers={},
        )
        agent.tools = make_restricted_registry(config, state)
        messages = [
            {"role": "system", "content": build_system_prompt(exp)},
            {"role": "user", "content": str(question["question"])},
        ]
        (
            final_content,
            _reasoning,
            tools_used,
            token_usage,
            iterations,
        ) = await agent._run_agent_loop(
            messages=messages,
            session_key=session_key,
            publish_events=False,
            ov_tools_enable=True,
            memory_peer_ids=[],
            memory_owner_user_ids=[],
            disabled_tools=[],
            openviking_connection=None,
        )
        response = final_content or ""
        if response.startswith("Error calling LLM in VLM Adapter"):
            network_error = redact_secret(response, api_key)
            response = ""
    except Exception as exc:
        network_error = redact_secret(f"{type(exc).__name__}: {exc}", api_key)
    finally:
        if agent is not None:
            await agent.close_mcp()

    disallowed = sorted(
        {
            str(item.get("tool_name", ""))
            for item in tools_used
            if str(item.get("tool_name", "")) not in TOOL_ALLOWLIST
        }
    )
    if disallowed:
        state.violation(f"tools_used contains non-allowlisted tools: {disallowed}")
    if any("Invalid parameters" in str(item.get("result", "")) for item in tools_used):
        state.protocol("AgentLoop emitted a tool call with invalid parameters")
    if exp == "A":
        if state.search_calls == 0:
            state.protocol("A did not perform the required Wiki-first search")
        if not state.wiki_uris_read:
            state.protocol("A did not successfully read a Wiki page")
    else:
        if state.search_calls == 0:
            state.protocol("B did not perform the required Resource search")
        if not state.resource_uris_read:
            state.protocol("B did not successfully read a Resource page")

    wiki_unique = list(dict.fromkeys(state.wiki_uris_read))
    resource_unique = list(dict.fromkeys(state.resource_uris_read))
    elapsed = time.perf_counter() - started
    return {
        "run_id": run_id,
        "question_id": question["id"],
        "exp": exp,
        "experiment": "wiki_first" if exp == "A" else "resource_only",
        "question": question["question"],
        "expected_wiki_sufficiency": question.get("expected_wiki_sufficiency"),
        "response": response,
        "fallback_used": state.fallback_used,
        "wiki_read_calls": state.wiki_read_calls,
        "resource_read_calls": state.resource_read_calls,
        "wiki_successful_reads": len(state.wiki_uris_read),
        "resource_successful_reads": len(state.resource_uris_read),
        "wiki_uris_read": state.wiki_uris_read,
        "resource_uris_read": state.resource_uris_read,
        "read_sequence": state.read_sequence,
        "unique_wiki_files": len(wiki_unique),
        "unique_resource_files": len(resource_unique),
        "retrieved_wiki_tokens": state.retrieved_wiki_tokens,
        "retrieved_resource_tokens": state.retrieved_resource_tokens,
        "model_input_tokens": int(token_usage.get("prompt_tokens", 0) or 0),
        "model_output_tokens": int(token_usage.get("completion_tokens", 0) or 0),
        "model_total_tokens": int(token_usage.get("total_tokens", 0) or 0),
        "latency_seconds": round(elapsed, 6),
        "iterations": iterations,
        "tool_trace": reconciled_tool_trace(state.trace, tools_used),
        "rejected_uris": state.rejected_uris,
        "scope_violation": bool(state.scope_violations),
        "scope_violations": state.scope_violations,
        "protocol_error": state.protocol_errors,
        "network_error": network_error,
        "invalid": bool(state.scope_violations or state.protocol_errors or network_error),
    }


def load_questions(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open(encoding="utf-8") as handle:
        question_set = json.load(handle)
    questions = question_set.get("questions")
    if not isinstance(questions, list):
        raise ValueError("question file must contain a top-level questions array")
    required = {
        "id",
        "question",
        "gold_answer",
        "rubric",
        "critical_errors",
        "source_uris",
        "expected_wiki_sufficiency",
    }
    ids: set[str] = set()
    for item in questions:
        missing = required - set(item)
        if missing:
            raise ValueError(f"question missing fields {sorted(missing)}")
        if item["id"] in ids:
            raise ValueError(f"duplicate question id: {item['id']}")
        ids.add(item["id"])
    return question_set, questions


def select_questions(
    questions: list[dict[str, Any]], ids: list[str] | None, count: int | None
) -> list[dict[str, Any]]:
    selected = questions
    if ids:
        requested = set(ids)
        known = {item["id"] for item in questions}
        missing = requested - known
        if missing:
            raise ValueError(f"unknown question ids: {sorted(missing)}")
        selected = [item for item in questions if item["id"] in requested]
    if count is not None:
        selected = selected[:count]
    return selected


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    questions_path = Path(args.questions).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"output exists; pass --overwrite: {output_path}")
    if args.config:
        config_path = Path(args.config).expanduser().resolve()
        if not config_path.is_file():
            raise FileNotFoundError(f"config not found: {config_path}")
        os.environ["OPENVIKING_CONFIG_FILE"] = str(config_path)

    load_dotenv(DEFAULT_ENV_FILE, override=False)
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(f"ARK_API_KEY is required (default env file: {DEFAULT_ENV_FILE})")

    question_set, all_questions = load_questions(questions_path)
    selected = select_questions(all_questions, args.question_id, args.count)
    if not selected:
        raise ValueError("no questions selected")
    config = load_config()
    model = DEFAULT_MODEL
    base_url = DEFAULT_BASE_URL
    provider_name = "volcengine"
    if args.config:
        model = getattr(config.agents, "model", "") or DEFAULT_MODEL
        base_url = getattr(config.agents, "api_base", "") or DEFAULT_BASE_URL
        provider_name = getattr(config.agents, "provider", "") or "volcengine"

    before = source_snapshot()
    expected = expected_snapshot(question_set)
    before_matches_frozen = snapshots_equal(before, expected)
    started_at = utc_now()
    experiments = ["A", "B"] if args.exp == "both" else [args.exp]
    jobs = [(question, exp) for question in selected for exp in experiments]
    semaphore = asyncio.Semaphore(args.threads)

    async def limited(question: dict[str, Any], exp: str) -> dict[str, Any]:
        async with semaphore:
            return await run_one(question, exp, config, api_key, base_url, model, provider_name)

    results = await asyncio.gather(*(limited(question, exp) for question, exp in jobs))
    after = source_snapshot()
    source_integrity_ok = before_matches_frozen and snapshots_equal(before, after)
    if not source_integrity_ok:
        message = "source snapshot differs from frozen corpus or changed during evaluation"
        for result in results:
            if message not in result["protocol_error"]:
                result["protocol_error"].append(message)
            result["invalid"] = True

    payload = {
        "schema_version": "openviking_wiki_ab_results_v1",
        "run_started_at": started_at,
        "run_finished_at": utc_now(),
        "completed": True,
        "questions": str(questions_path),
        "questions_sha256": sha256_file(questions_path),
        "requested_exp": args.exp,
        "requested_question_ids": [item["id"] for item in selected],
        "model": model,
        "provider": provider_name,
        "api_base": base_url,
        "tool_allowlist": sorted(TOOL_ALLOWLIST),
        "stage_token_budget": STAGE_TOKEN_BUDGET,
        "source_expected": expected,
        "source_before": before,
        "source_after": after,
        "source_integrity_ok": source_integrity_ok,
        "results": results,
    }
    atomic_write_json(output_path, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run isolated VikingBot Wiki-first/Resource-only A/B evaluation."
    )
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="Question JSON path")
    parser.add_argument("--exp", required=True, choices=("A", "B", "both"))
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument(
        "--question-id", action="append", help="Question ID; may be supplied repeatedly"
    )
    parser.add_argument("--count", type=int, help="Run only the first N selected questions")
    parser.add_argument("--config", help="Optional ov.conf path")
    parser.add_argument("--threads", type=int, default=1, help="Concurrent independent runs")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.count is not None and args.count < 1:
        raise ValueError("--count must be at least 1")
    if args.threads < 1:
        raise ValueError("--threads must be at least 1")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
        payload = asyncio.run(run_benchmark(args))
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Wrote {len(payload['results'])} runs to {Path(args.output).expanduser().resolve()} "
        f"(source_integrity_ok={payload['source_integrity_ok']})"
    )


if __name__ == "__main__":
    main()
