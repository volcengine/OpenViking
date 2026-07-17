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

from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.experience_evidence import (
    COMPARISON_TRAJ_INJECT_TOP_K,
    SEARCH_TOP_K,
    ExperienceEvidenceBundle,
    ExperienceEvidenceLoader,
    ExperienceEvidenceQuery,
)
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


EXPERIENCE_MEMORY_TYPE = "experiences"
TRAJECTORY_MEMORY_TYPE = "trajectories"


class AgentExperienceContextProvider(SessionExtractContextProvider):
    """Phase 2 provider: consolidate the new trajectory into experience memories."""

    include_tool_parts_in_conversation = True

    def __init__(
        self,
        messages: Any,
        trajectory_summary: str,
        trajectory_uri: str,
        latest_archive_overview: str = "",
        case_uri: str = "",
        case_name: str = "",
        task_signature: str = "",
        evidence_loader: ExperienceEvidenceLoader | None = None,
    ):
        super().__init__(messages=messages, latest_archive_overview=latest_archive_overview)
        self.trajectory_summary = trajectory_summary
        self.trajectory_uri = trajectory_uri
        self.case_uri = str(case_uri or "")
        self.case_name = str(case_name or "")
        self.task_signature = str(task_signature or "")
        self._evidence_loader = evidence_loader
        self.prefetched_uris: List[str] = []
        self.prefetched_comparison_trajectories: List[Dict[str, Any]] = []

    def instruction(self) -> str:
        from openviking.session.train.gates import default_experience_gate_contract

        output_language = self._output_language
        return f"""You are a memory extraction agent. Distill reusable failure-repair experiences from failed or partially failed agent execution trajectories.

You are given:
- A new trajectory to learn from
- Optional `comparison_trajectory` records from the same/similar task pattern, which may include both successes and failures
- Up to {SEARCH_TOP_K} relevant existing experiences, sometimes with their source trajectories for grounding

Source and comparison trajectories are evidence only. Do NOT copy or modify trajectory text in the output.

## Authoritative outcome evidence

When the training context includes authoritative evaluation or outcome evidence supplied by the
training pipeline, that evidence defines the target behavior. If it conflicts with base-policy
wording, override only the smallest conflicting policy interpretation needed to explain the
required outcome; preserve non-conflicting constraints and object boundaries. Infer the reusable
runtime behavior required by that evidence. The experience itself must not mention the evaluator,
evaluation metadata, hidden checks, expected actions, or reward; rewrite the lesson using only
observable user requests, tool results, runtime facts, and actions.

## What to output

Output experience entries ONLY when a reusable runtime reminder would prevent or recover from the first materially outcome-changing mistake. Do not write full workflows, success-path SOPs, case logs, or generic advice.

Each entry:
- `experience_name`: new or existing experience name
- `situation`: only the `## Situation` bullet body
- `reminder`: only the `## Reminder` bullet body
- `procedure`: only the `## Procedure` bullet body
- `anti_pattern`: only the `## Anti-pattern` bullet body
- `supersedes`: older `experience_name` replaced by a genuinely broader/corrected one; otherwise empty

The storage template adds the four Markdown headings in a fixed order. Do not include headings
inside field values. The skill loader shows the rendered `## Situation` as the applicability
snippet and may then load the whole rendered experience with `read_experience`. Therefore
`situation` must clearly say when the experience applies, when it does not apply, and which
runtime source binds the rule. Do not output `trigger_code`; it is not used by the skill loader.

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
- Treat trajectory memories as factual evidence, not authoritative conclusions. Use their `Timeline`, `Outcome Checks`, `Observed Problem`, `Value/Scope Trace/Evidence`, `Source Field Trace/Evidence`, and `Raw Evidence` to infer the reusable repair.
- When both successful and non-successful trajectories are available for the same or similar task pattern, compare them before writing an experience: identify which user-visible value, included/excluded record set, source field, label, confirmation, or write argument appears in successful traces and is missing/different in failures.
- Do not copy trajectory wording directly into an experience. Re-check the original runtime evidence, injected experience effects, existing experiences, and this gate contract before writing an injectable reminder.
- A failed or partial trajectory does not require an experience update. Create/update only when the evidence supports a narrow runtime reminder that would likely prevent or recover from the first materially outcome-changing mistake.

## Writing rules

- One distinct root failure pattern → one experience. Split unrelated failures; keep a coupled rule for communication/action scope when the same ambiguity causes both, so the future agent can answer the information obligation without expanding the write/action scope.
- State the behavior delta: block a write, change one argument, ask/read one missing fact, or include one requested source-bound fact in communication.
- Preserve object boundaries. A user's yes to an agent-proposed broader plan is not independent evidence for extra objects/actions.
- For communication, totals, counts, lists, or summaries, bind the answer to the user-requested scope, frozen record/set membership, included/excluded records, source field, derivation, later-write effect, selected object, or policy gate.
- For information/aggregate/list/summary/value requests, preserve the user-requested source scope at the moment the request is made. Later write actions may create a second "post-action/current remaining state" scope, but they must not silently replace the original requested scope.
- If user wording is ambiguous between an original requested set and a post-action remaining/current-state set, write the experience so the future agent gives both scopes with explicit labels instead of only the narrower post-action scope.
- Do not treat relative words like "other", "remaining", "those", "the rest", "其他", or "剩余" as explicit exclusions when the user is also discussing writes. They are ambiguous unless the user's own wording says to exclude a named object or semantic role; in ambiguous cases, `Scope ambiguity` must name both scopes.
- Do not exclude records from a request-time information/aggregate/list/summary/value merely because they are later modified, canceled, upgraded, consumed, split, or otherwise changed. Exclude them only when the user's own wording explicitly excluded that semantic role from the earlier information request.
- If later writes affect records that could belong to a requested information/list/aggregate value, `Scope ambiguity` must name both the original request-time scope and the post-action/current remaining scope; do not write none/无.
- For total cost, paid amount, balance, refund, or similar monetary aggregates, bind the answer to the canonical runtime value field when one exists: explicit total/paid/charged/order/payment-history amount fields beat reconstructed lower-level unit/segment/item price sums. Use line items only when no canonical total exists, or as a cross-check. If those values differ, the experience must tell the future agent which source field to prefer. Do not name lower-level price fields as the primary source when a record-level total/paid/charged amount is available in runtime evidence.
- `Does not apply when` must describe a task-pattern mismatch, not a temporal stage. Do not write conditions such as "still reading", "before final response", "before writes complete", or "not yet at final_response"; the skill loader may read the experience at task start even when it applies at a later boundary.
- If a loaded existing experience encodes the misleading rule that later-modified/canceled/upgraded records should be removed from an earlier requested aggregate, update that experience instead of creating a competing memory.
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
        return self._render_memory_dir(EXPERIENCE_MEMORY_TYPE, ctx)

    def _render_trajectory_dir(self, ctx: RequestContext) -> str:
        return self._render_memory_dir(TRAJECTORY_MEMORY_TYPE, ctx)

    def _render_memory_dir(self, memory_type: str, ctx: RequestContext) -> str:
        registry = self._get_registry()
        schema = registry.get(memory_type)
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
        query = ExperienceEvidenceQuery(
            trajectory_summary=self.trajectory_summary,
            trajectory_uri=self.trajectory_uri,
            experience_dir=self._render_experience_dir(ctx),
            trajectory_dir=self._render_trajectory_dir(ctx),
            case_uri=self.case_uri,
            case_name=self.case_name,
            task_signature=self.task_signature,
        )
        loader = self._evidence_loader or ExperienceEvidenceLoader(self._viking_fs)
        bundle = await loader.load(query, ctx)
        return self._render_evidence_bundle(bundle)

    def _render_evidence_bundle(self, bundle: ExperienceEvidenceBundle) -> List[Dict]:
        self.prefetched_uris = []
        self.prefetched_comparison_trajectories = []

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

        comparison_trajectories = bundle.comparison_trajectories[:COMPARISON_TRAJ_INJECT_TOP_K]
        for comparison_idx, comparison_evidence in enumerate(comparison_trajectories):
            comparison_file = comparison_evidence.memory_file
            comparison_uri = str(comparison_file.uri or "")
            comparison_result = self._build_context_result(
                uri=comparison_uri,
                context_role="comparison_trajectory",
                memory_file=comparison_file,
            )
            self.prefetched_comparison_trajectories.append(comparison_result)
            add_tool_call_pair_to_messages(
                messages=prefetch_messages,
                call_id=f"comparison-{comparison_idx}",
                tool_name="read",
                params={"uri": comparison_uri},
                result=comparison_result,
            )

        for idx, candidate in enumerate(bundle.candidates):
            memory_file = candidate.memory_file
            exp_uri = str(memory_file.uri or "")
            if not exp_uri:
                continue
            self.prefetched_uris.append(exp_uri)
            self._read_file_contents[exp_uri] = memory_file
            page_id = self.get_extract_context().page_id_map.get_page_id(exp_uri)
            result = self._build_context_result(
                uri=exp_uri,
                context_role="candidate_experience",
                memory_file=memory_file,
            )
            result["page_id"] = page_id

            add_tool_call_pair_to_messages(
                messages=prefetch_messages,
                call_id=call_id_seq,
                tool_name="read",
                params={"uri": exp_uri},
                result=result,
            )
            call_id_seq += 1

            for source_idx, source_evidence in enumerate(candidate.source_trajectories):
                source_file = source_evidence.memory_file
                source_uri = str(source_file.uri or "")
                add_tool_call_pair_to_messages(
                    messages=prefetch_messages,
                    call_id=f"source-{idx}-{source_idx}",
                    tool_name="read",
                    params={"uri": source_uri},
                    result=self._build_context_result(
                        uri=source_uri,
                        context_role="candidate_source_trajectory",
                        memory_file=source_file,
                    ),
                )

        prefetch_messages.append(
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "You have already read the conversation, one `new_trajectory`, optional `comparison_trajectory` records, candidate experience memories, and optional `candidate_source_trajectory` references.",
                        "Treat `new_trajectory` as the new execution to incorporate.",
                        "Treat `comparison_trajectory` as factual peer evidence for comparing success and failure paths; do not modify it directly.",
                        "Treat `candidate_experience` as existing memories you may update, replace, or skip.",
                        "Treat `candidate_source_trajectory` as reference-only context for understanding a candidate experience; do not modify it directly.",
                        "Based on the above, decide whether to **Update**, **Replace**, **Create**, or **Skip** a failure-repair experience. Output JSON only.",
                        "Only reusable failure patterns should produce entries; successful or unrelated intents should produce no experience changes.",
                    ]
                ),
            }
        )
        return prefetch_messages
