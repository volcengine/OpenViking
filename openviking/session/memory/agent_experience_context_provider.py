# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Agent Experience Context Provider - Phase 2 of agent-scope memory extraction.

Given a new trajectory summary from Phase 1, search for candidate experiences and
let the LLM decide whether to update an existing one, create a new one, or do nothing.

No tool calls — all context is prefetched. Top-3 candidates also include their
source_trajectories as grounding material.
"""

from typing import Any, Dict, List, Optional

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


EXPERIENCE_MEMORY_TYPE = "experiences"
SEARCH_TOP_K = 5
SOURCE_TRAJ_TOP_K = 3  # only attach source_trajectories for the top-3 candidates
MAX_SOURCE_TRAJS = 3  # max trajectories to load per experience


def _is_directory_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "directory not found" in message or "not_found" in message


class AgentExperienceContextProvider(SessionExtractContextProvider):
    """Phase 2 provider: consolidate the new trajectory into experience memories."""

    include_tool_parts_in_conversation = True

    def __init__(
        self,
        messages: Any,
        trajectory_summary: str,
        trajectory_uri: str,
        latest_archive_overview: str = "",
    ):
        super().__init__(messages=messages, latest_archive_overview=latest_archive_overview)
        self.trajectory_summary = trajectory_summary
        self.trajectory_uri = trajectory_uri
        self.prefetched_uris: List[str] = []

    def instruction(self) -> str:
        from openviking.session.train.gates import default_experience_gate_contract

        output_language = self._output_language
        return f"""You are a memory extraction agent. Distill reusable failure-repair experiences from failed or partially failed agent execution trajectories.

You are given:
- A new trajectory to learn from
- Up to {SEARCH_TOP_K} relevant existing experiences, sometimes with their source trajectories for grounding

Source trajectories are evidence only. Do NOT copy or modify trajectory text in the output.

## What to output

Output experience entries ONLY when a reusable runtime reminder would prevent or recover from the first materially outcome-changing mistake. Do not write full workflows, success-path SOPs, case logs, or generic advice.

Each entry:
- `experience_name`: new or existing experience name
- `constraint`: the full skill-readable experience body. It MUST use the schema's `## Situation`, `## Reminder`, `## Procedure`, and `## Anti-pattern` sections.
- `supersedes`: older `experience_name` replaced by a genuinely broader/corrected one; otherwise empty

The skill loader searches experiences, shows `## Situation` as the applicability snippet, and may then load the whole experience with `read_experience`. Therefore `## Situation` must clearly say when the experience applies, when it does not apply, and which runtime source binds the rule. Do not output `trigger_code`; it is not used by the skill loader.

The system handles create vs update automatically:
- Same `experience_name` as an existing one → update it in place
- New `experience_name` → create a new experience
- `supersedes` set → delete the old experience and inherit its history

## Decision rules

- Existing experience is correct but the agent ignored it → skip unless its wording/applicability is too weak to guide skill loading.
- Existing experience is misleading, over-broad, too weak, or caused the bad path → update it, primarily by sharpening `## Situation`, `## Procedure`, and `## Anti-pattern`.
- No relevant experience exists and the failure has a reusable preventive repair → create a new experience.
- The failure is successful, case-specific, unsupported, random, already solved by available tool facts, or not preventable by a runtime reminder → output no changes.
- If the failure came from agent-initiated scope expansion, do not treat the user's later yes/confirmation to the agent's over-broad proposal as a clean user-initiated request. Preserve the original user-requested write/action scope unless the user independently requested a new object/action in their own words.
- If tool/action/DB checks passed but required user-visible communication failed, create/update a communication-boundary experience. Generalize the omitted literal into its semantic role: total cost, identifier, policy explanation, next step, etc.
- If the trajectory has `Counterfactual Ideal Experience` and selected C1 says an ideal experience would fix the rollout, use C1 as the repair target. Preserve its situation, repair rule, source binding, applicability, and anti-pattern in generalized runtime language.
- Treat legacy `Experience Repair Signal.Action=skip`, `Recommended operation=skip`, `Existing target experience=none`, or `Trigger boundary=none` as advisory only for failed/partial trajectories; they must not suppress a reusable new experience.

## Writing rules

- One distinct root failure pattern → one experience. Split unrelated failures; keep a coupled rule for communication/action scope when the same ambiguity causes both, so the future agent can answer the information obligation without expanding the write/action scope.
- State the behavior delta: block a write, change one argument, ask/read one missing fact, or include one requested source-bound fact in communication.
- Preserve object boundaries. A user's yes to an agent-proposed broader plan is not independent evidence for extra objects/actions.
- For communication, totals, counts, lists, or summaries, bind the answer to the user-requested scope, frozen record/set membership, included/excluded records, source field, derivation, later-write effect, selected object, or policy gate.
- For information/aggregate/list/summary/value requests, preserve the user-requested source scope at the moment the request is made. Later write actions may create a second "post-action/current remaining state" scope, but they must not silently replace the original requested scope.
- If user wording is ambiguous between an original requested set and a post-action remaining/current-state set, write the experience so the future agent gives both scopes with explicit labels instead of only the narrower post-action scope.
- Do not encode dataset-specific values, IDs, amounts, or domain names in the reusable rule; express the lesson as source-scope binding, freeze point, included/excluded object roles, and later-write effect.
- Preserve correct near-misses: `## Situation`/`## Anti-pattern` must say when NOT to apply the experience.
- Avoid evaluator/control-plane wording such as evaluation, evaluator, communicate_checks, action_checks, db_check, reward, rubric, 评估, 奖励. Rewrite into runtime facts.
- Keep it concise, imperative, and machine-readable. No raw IDs, hidden answers, policy dumps, or full task paths.
- Use the same language for all `experience_name` values.

{default_experience_gate_contract()}
- Do NOT use `delete_ids`; use `supersedes` instead.
- Follow field descriptions in the schema.
- Output JSON only. Do not call any tools.

All memory content must be written in {output_language}.
"""

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        registry = self._get_registry()
        schema = registry.get(EXPERIENCE_MEMORY_TYPE)
        if schema is None or not schema.enabled:
            return []
        return [schema]

    def get_tools(self) -> List[str]:
        return []

    def _render_experience_dir(self, ctx: RequestContext) -> str:
        registry = self._get_registry()
        schema = registry.get(EXPERIENCE_MEMORY_TYPE)
        if schema is None or not schema.directory:
            return ""

        if ctx and ctx.user:
            user_space = ctx.user.user_id
        else:
            user_space = "default"

        return TemplateUtils.render(
            schema.directory,
            {"user_space": user_space},
        )

    async def _load_source_trajectories(
        self,
        exp_uri: str,
        links: List[Dict],
        viking_fs: VikingFS,
        ctx: RequestContext,
    ) -> List[Dict]:
        """Load the most recent source trajectories for a candidate experience from its links."""
        uris = [
            link.get("to_uri", "")
            for link in (links or [])
            if link.get("link_type") == "derived_from" and link.get("to_uri", "")
        ]

        recent_uris = uris[-MAX_SOURCE_TRAJS:]
        results = []
        for uri in recent_uris:
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx) or ""
                mf = MemoryFileUtils.read(raw, uri=uri)
                result = mf.to_metadata()
                result["content"] = mf.content
                result["uri"] = uri
                results.append(result)
            except Exception as e:
                tracer.error(f"Failed to read source trajectory {uri}: {e}")
        return results

    def _build_context_result(
        self,
        *,
        uri: str,
        context_role: str,
        result: Optional[Dict[str, Any]] = None,
        memory_file: Optional[MemoryFile] = None,
    ) -> Dict[str, Any]:
        payload = dict(result or {})
        if memory_file is not None:
            payload = memory_file.to_metadata()
            if memory_file.memory_type == EXPERIENCE_MEMORY_TYPE or "/memories/experiences/" in uri:
                payload["constraint"] = str(
                    (memory_file.extra_fields or {}).get("constraint") or memory_file.content or ""
                )
                payload.pop("content", None)
            else:
                payload["content"] = memory_file.content
        payload["uri"] = uri
        payload["context_role"] = context_role
        return payload

    async def prefetch(self) -> List[Dict]:
        if not isinstance(self.messages, list):
            tracer.error(f"Expected List[Message], got {type(self.messages)}")
            return []

        ctx = self._ctx
        viking_fs = self._viking_fs

        experience_dir = self._render_experience_dir(ctx)

        candidate_uris: List[str] = []
        if experience_dir and viking_fs:
            candidate_uris = await self.search_files(
                query=self.trajectory_summary[:500] or "experience",
                search_uris=[experience_dir],
                limit=SEARCH_TOP_K,
            )

            if not candidate_uris:
                try:
                    entries = await viking_fs.ls(experience_dir, output="original", ctx=ctx)
                    fallback_uris: List[str] = []
                    for entry in entries or []:
                        uri = str(entry.get("uri", "")) if isinstance(entry, dict) else ""
                        name = str(entry.get("name", "")) if isinstance(entry, dict) else ""
                        if not uri.endswith(".md"):
                            continue
                        if name in {".overview.md", ".abstract.md"}:
                            continue
                        if uri.endswith("/.overview.md") or uri.endswith("/.abstract.md"):
                            continue
                        fallback_uris.append(uri)
                    candidate_uris = fallback_uris[:SEARCH_TOP_K]
                except AGFSNotFoundError:
                    candidate_uris = []
                except FileNotFoundError:
                    candidate_uris = []
                except Exception as e:
                    if _is_directory_not_found_error(e):
                        candidate_uris = []
                    else:
                        tracer.error(f"Failed to list experiences in {experience_dir}: {e}")

        prefetch_messages: List[Dict[str, Any]] = [self._build_conversation_message()]
        add_tool_call_pair_to_messages(
            messages=prefetch_messages,
            call_id="new-trajectory",
            tool_name="read",
            params={"uri": self.trajectory_uri},
            result=self._build_context_result(
                uri=self.trajectory_uri,
                context_role="new_trajectory",
                result={
                    "memory_type": "trajectories",
                    "content": self.trajectory_summary,
                },
            ),
        )
        call_id_seq = 0

        for idx, exp_uri in enumerate(candidate_uris):
            result = await self.read_file(exp_uri)
            if result is None:
                continue

            self.prefetched_uris.append(exp_uri)
            mf = self._read_file_contents.get(exp_uri)
            if not mf:
                continue

            add_tool_call_pair_to_messages(
                messages=prefetch_messages,
                call_id=call_id_seq,
                tool_name="read",
                params={"uri": exp_uri},
                result=self._build_context_result(
                    uri=exp_uri,
                    context_role="candidate_experience",
                    result=result,
                    memory_file=mf,
                ),
            )
            call_id_seq += 1

            if idx < SOURCE_TRAJ_TOP_K and viking_fs:
                source_trajs = await self._load_source_trajectories(
                    exp_uri, mf.links, viking_fs, ctx
                )
                for source_idx, source_result in enumerate(source_trajs):
                    source_uri = source_result["uri"]
                    add_tool_call_pair_to_messages(
                        messages=prefetch_messages,
                        call_id=f"source-{idx}-{source_idx}",
                        tool_name="read",
                        params={"uri": source_uri},
                        result=self._build_context_result(
                            uri=source_uri,
                            context_role="candidate_source_trajectory",
                            result=source_result,
                        ),
                    )

        prefetch_messages.append(
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "You have already read the conversation, one `new_trajectory`, candidate experience memories, and optional `candidate_source_trajectory` references.",
                        "Treat `new_trajectory` as the new execution to incorporate.",
                        "Treat `candidate_experience` as existing memories you may update, replace, or skip.",
                        "Treat `candidate_source_trajectory` as reference-only context for understanding a candidate experience; do not modify it directly.",
                        "Based on the above, decide whether to **Update**, **Replace**, **Create**, or **Skip** a failure-repair experience. Output JSON only.",
                        "Only reusable failure patterns should produce entries; successful or unrelated intents should produce no experience changes.",
                    ]
                ),
            }
        )
        return prefetch_messages
