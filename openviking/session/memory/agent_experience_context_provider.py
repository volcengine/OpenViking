# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Agent Experience Context Provider - Phase 2 of agent-scope memory extraction.

Given a new trajectory summary from Phase 1, search for candidate experiences and
let the LLM decide whether to update an existing one, create a new one, or do nothing.

No tool calls — the current trajectory, exact-case successful comparisons, and
existing experience candidates are prefetched.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, ToolContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.tools import add_tool_call_pair_to_messages, get_tool
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import replay, tracer
from openviking.telemetry.replay.models import EncodedValue, ReplayCodecError

EXPERIENCE_MEMORY_TYPE = "experiences"
TRAJECTORY_MEMORY_TYPE = "trajectories"
SEARCH_TOP_K = 5
COMPARISON_TRAJ_TOP_K = 6
COMPARISON_TRAJ_INJECT_TOP_K = 2
MAX_COMPARISON_TRAJ_CHARS = 6000


@dataclass(slots=True)
class ExperienceEvidenceQuery:
    trajectory_summary: str
    trajectory_uri: str
    experience_dir: str
    trajectory_dir: str
    case_uri: str = ""
    case_name: str = ""
    task_signature: str = ""


@dataclass(slots=True)
class TrajectoryEvidence:
    memory_file: MemoryFile


@dataclass(slots=True)
class CandidateExperienceEvidence:
    memory_file: MemoryFile


@dataclass(slots=True)
class ExperienceEvidenceBundle:
    candidates: list[CandidateExperienceEvidence] = field(default_factory=list)
    comparison_trajectories: list[TrajectoryEvidence] = field(default_factory=list)


def _encoded(payload: dict[str, Any], name: str) -> EncodedValue:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ReplayCodecError(f"Replay codec payload is missing encoded field {name!r}")
    return value


@replay.codec(ExperienceEvidenceQuery, name="openviking.memory.experience_evidence_query")
class ExperienceEvidenceQueryReplayCodec:
    @staticmethod
    def encode(value: ExperienceEvidenceQuery, encode):
        return {
            "trajectory_summary": encode(value.trajectory_summary),
            "trajectory_uri": encode(value.trajectory_uri),
            "experience_dir": encode(value.experience_dir),
            "trajectory_dir": encode(value.trajectory_dir),
            "case_uri": encode(value.case_uri),
            "case_name": encode(value.case_name),
            "task_signature": encode(value.task_signature),
        }

    @staticmethod
    def decode(payload, decode):
        return ExperienceEvidenceQuery(
            trajectory_summary=decode(_encoded(payload, "trajectory_summary")),
            trajectory_uri=decode(_encoded(payload, "trajectory_uri")),
            experience_dir=decode(_encoded(payload, "experience_dir")),
            trajectory_dir=decode(_encoded(payload, "trajectory_dir")),
            case_uri=decode(_encoded(payload, "case_uri")),
            case_name=decode(_encoded(payload, "case_name")),
            task_signature=decode(_encoded(payload, "task_signature")),
        )


def _encode_memory_file(memory_file: MemoryFile, encode) -> EncodedValue:
    return encode(memory_file.model_dump(mode="python"))


def _decode_memory_file(payload: EncodedValue, decode) -> MemoryFile:
    data = decode(payload)
    if not isinstance(data, dict):
        raise ReplayCodecError("Evidence memory file must decode to a dictionary")
    return MemoryFile.model_validate(data)


@replay.codec(TrajectoryEvidence, name="openviking.memory.trajectory_evidence")
class TrajectoryEvidenceReplayCodec:
    @staticmethod
    def encode(value: TrajectoryEvidence, encode):
        return {"memory_file": _encode_memory_file(value.memory_file, encode)}

    @staticmethod
    def decode(payload, decode):
        return TrajectoryEvidence(
            memory_file=_decode_memory_file(_encoded(payload, "memory_file"), decode)
        )


@replay.codec(CandidateExperienceEvidence, name="openviking.memory.candidate_experience_evidence")
class CandidateExperienceEvidenceReplayCodec:
    @staticmethod
    def encode(value: CandidateExperienceEvidence, encode):
        return {"memory_file": _encode_memory_file(value.memory_file, encode)}

    @staticmethod
    def decode(payload, decode):
        return CandidateExperienceEvidence(
            memory_file=_decode_memory_file(_encoded(payload, "memory_file"), decode),
        )


@replay.codec(ExperienceEvidenceBundle, name="openviking.memory.experience_evidence_bundle")
class ExperienceEvidenceBundleReplayCodec:
    @staticmethod
    def encode(value: ExperienceEvidenceBundle, encode):
        return {
            "candidates": encode(value.candidates),
            "comparison_trajectories": encode(value.comparison_trajectories),
        }

    @staticmethod
    def decode(payload, decode):
        return ExperienceEvidenceBundle(
            candidates=decode(_encoded(payload, "candidates")),
            comparison_trajectories=decode(_encoded(payload, "comparison_trajectories")),
        )


class ExperienceEvidenceLoader:
    def __init__(self, viking_fs: VikingFS | Any) -> None:
        self._viking_fs = viking_fs

    @replay.mock("memory.experience.load_evidence", match=["query"])
    async def load(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> ExperienceEvidenceBundle:
        if self._viking_fs is None:
            raise RuntimeError("VikingFS is required for experience evidence loading")
        candidate_uris = await self._candidate_uris(query, ctx)
        candidates = await self._load_candidates(candidate_uris, ctx)
        comparisons = await self._load_comparison_trajectories(query, ctx)
        return ExperienceEvidenceBundle(
            candidates=candidates,
            comparison_trajectories=comparisons,
        )

    async def _candidate_uris(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> list[str]:
        if not query.experience_dir:
            return []
        candidate_uris = await self._search_uris(
            query.trajectory_summary[:500] or "experience",
            [query.experience_dir],
            SEARCH_TOP_K,
            ctx,
        )
        if candidate_uris:
            return candidate_uris
        try:
            entries = await self._viking_fs.ls(query.experience_dir, output="original", ctx=ctx)
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        except Exception as error:
            if _is_directory_not_found_error(error):
                return []
            tracer.error(f"Failed to list experiences in {query.experience_dir}: {error}")
            return []
        fallback_uris = []
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
        return fallback_uris[:SEARCH_TOP_K]

    async def _load_candidates(
        self,
        candidate_uris: list[str],
        ctx: RequestContext,
    ) -> list[CandidateExperienceEvidence]:
        candidates = []
        for uri in candidate_uris:
            memory_file = await self._read_memory_file(uri, ctx)
            if memory_file is None:
                continue
            candidates.append(CandidateExperienceEvidence(memory_file=memory_file))
        return candidates

    async def _load_comparison_trajectories(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> list[TrajectoryEvidence]:
        if not query.trajectory_dir:
            return []
        seen = {query.trajectory_uri}
        linked_uris = await self._case_linked_trajectory_uris(query, ctx)
        if linked_uris is None:
            return []
        results = await self._read_trajectory_evidence(linked_uris, seen, ctx)
        successes = [item for item in results if _is_success_trajectory(item.memory_file)]
        return successes[:COMPARISON_TRAJ_TOP_K]

    async def _case_linked_trajectory_uris(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> list[str] | None:
        case_uri = await self._resolve_case_uri(query, ctx)
        if not case_uri:
            return None
        case_file = await self._read_memory_file(case_uri, ctx)
        if case_file is None:
            return []
        recency_by_uri: dict[str, tuple[str, str]] = {}
        for link in list(case_file.links or []) + list(case_file.backlinks or []):
            if str(link.get("link_type") or "") != "successful_trajectory":
                continue
            created_at = str(link.get("created_at") or "")
            for uri in (str(link.get("to_uri") or ""), str(link.get("from_uri") or "")):
                if "/memories/trajectories/" not in uri:
                    continue
                recency = (created_at, uri)
                previous = recency_by_uri.get(uri)
                if previous is None or recency > previous:
                    recency_by_uri[uri] = recency
        return [uri for _, uri in sorted(recency_by_uri.values(), reverse=True)]

    async def _resolve_case_uri(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> str:
        if query.case_uri:
            return query.case_uri
        trajectory_file = await self._read_memory_file(query.trajectory_uri, ctx)
        if trajectory_file is None:
            return ""
        case_uri = str((trajectory_file.extra_fields or {}).get("case_uri") or "")
        if case_uri:
            return case_uri
        for link in list(trajectory_file.backlinks or []) + list(trajectory_file.links or []):
            for uri in (str(link.get("from_uri") or ""), str(link.get("to_uri") or "")):
                if "/memories/cases/" in uri:
                    return uri
        return ""

    async def _read_trajectory_evidence(
        self,
        candidate_uris: list[str],
        seen: set[str],
        ctx: RequestContext,
    ) -> list[TrajectoryEvidence]:
        results = []
        for uri in candidate_uris:
            if not uri or uri in seen:
                continue
            seen.add(uri)
            memory_file = await self._read_memory_file(uri, ctx)
            if memory_file is None:
                continue
            if memory_file.memory_type and memory_file.memory_type != TRAJECTORY_MEMORY_TYPE:
                continue
            if len(memory_file.content) > MAX_COMPARISON_TRAJ_CHARS:
                memory_file = memory_file.model_copy(
                    update={
                        "content": memory_file.content[: MAX_COMPARISON_TRAJ_CHARS - 20].rstrip()
                        + "\n...<truncated>"
                    }
                )
            results.append(TrajectoryEvidence(memory_file))
        return results

    async def _read_memory_file(
        self,
        uri: str,
        ctx: RequestContext,
    ) -> MemoryFile | None:
        try:
            raw = await self._viking_fs.read_file(uri, ctx=ctx) or ""
            return MemoryFileUtils.read(raw, uri=uri)
        except Exception as error:
            tracer.error(f"Failed to read experience evidence {uri}: {error}")
            return None

    async def _search_uris(
        self,
        query: str,
        search_uris: list[str],
        limit: int,
        ctx: RequestContext,
    ) -> list[str]:
        search_tool = get_tool("search")
        if search_tool is None:
            return []
        tool_context = ToolContext(
            viking_fs=self._viking_fs,
            request_ctx=ctx,
            default_search_uris=search_uris,
        )
        result = await search_tool.execute(ctx=tool_context, query=query, limit=limit)
        if isinstance(result, list):
            return [str(item.get("uri")) for item in result if item.get("uri")]
        if isinstance(result, dict):
            return [str(item.get("uri")) for item in result.get("memories", []) if item.get("uri")]
        return []


def _is_success_trajectory(memory_file: MemoryFile) -> bool:
    return str((memory_file.extra_fields or {}).get("outcome") or "").strip().lower() == "success"


def _is_directory_not_found_error(error: Exception) -> bool:
    message = str(error).lower()
    return "directory not found" in message or "not_found" in message


class AgentExperienceContextProvider(SessionExtractContextProvider):
    """Phase 2 provider: consolidate the new trajectory into experience memories."""

    def __init__(
        self,
        trajectory_summary: str,
        trajectory_uri: str,
        latest_archive_overview: str = "",
        case_uri: str = "",
        case_name: str = "",
        task_signature: str = "",
        evidence_loader: ExperienceEvidenceLoader | None = None,
    ):
        self.trajectory_summary = trajectory_summary
        super().__init__(messages=[], latest_archive_overview=latest_archive_overview)
        self.trajectory_uri = trajectory_uri
        self.case_uri = str(case_uri or "")
        self.case_name = str(case_name or "")
        self.task_signature = str(task_signature or "")
        self._evidence_loader = evidence_loader
        self.prefetched_uris: List[str] = []
        self.prefetched_comparison_trajectories: List[Dict[str, Any]] = []

    def _detect_language(self) -> str:
        from openviking.session.memory.utils import (
            resolve_output_language,
            strip_language_detection_noise,
        )

        return resolve_output_language(strip_language_detection_noise(self.trajectory_summary))

    def instruction(self) -> str:
        from openviking.session.train.gates import default_experience_gate_contract

        output_language = self._output_language
        schema = self._get_registry().get("experiences")
        content_field_names = schema.content_field_names() if schema is not None else ()
        content_fields = ", ".join(f"`{name}`" for name in content_field_names)
        situation_guidance = ""
        if "situation" in content_field_names:
            situation_guidance = """The skill loader uses the rendered `situation` field as the
applicability snippet. It must clearly say when the experience applies, when it does not apply,
and which runtime source binds the rule. """
        return f"""You are a memory extraction agent. Distill reusable failure-repair experiences from failed or partially failed agent execution trajectories.

You are given:
- A new trajectory to learn from
- Optional successful `comparison_trajectory` records from the exact same case
- Up to {SEARCH_TOP_K} relevant existing experiences for update and deduplication

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
- Populate every structured content field declared by the schema: {content_fields}
- `supersedes`: older `experience_name` replaced by a genuinely broader/corrected one; otherwise empty

The storage template defines the Markdown structure and order. Do not include headings inside
field values. {situation_guidance}Do not output `trigger_code`; it is not used by the skill loader.

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
- When successful trajectories are available for the same case, compare them with the failed trajectory before writing an experience: identify which user-visible value, included/excluded record set, source field, label, confirmation, or write argument appears in successful traces and is missing/different in the failure.
- Do not copy trajectory wording directly into an experience. Re-check the original runtime evidence, injected experience effects, existing experiences, and this gate contract before writing an injectable reminder.
- A failed or partial trajectory does not require an experience update. Create/update only when the evidence supports a narrow runtime reminder that would likely prevent or recover from the first materially outcome-changing mistake.

## Writing rules

- One distinct root failure pattern → one experience. Split unrelated failures; keep a coupled rule for communication/action scope when the same ambiguity causes both, so the future agent can answer the information obligation without expanding the write/action scope.
- State the behavior delta: block a write, change one argument, ask/read one missing fact, or include one requested source-bound fact in communication.
- Preserve object boundaries. A user's yes to an agent-proposed broader plan is not independent evidence for extra objects/actions.
- For communication, totals, counts, lists, or summaries, bind the answer to the user-requested scope, frozen record/set membership, included/excluded records, source field, derivation, later-write effect, selected object, or policy gate.
- For information/aggregate/list/summary/value requests, preserve the user-requested source scope at the moment the request is made. Later write actions may create a second "post-action/current remaining state" scope, but they must not silently replace the original requested scope.
- If user wording is ambiguous between an original requested set and a post-action remaining/current-state set, write the experience so the future agent gives both scopes with explicit labels instead of only the narrower post-action scope.
- Do not treat relative words like "other", "remaining", "those", "the rest", "其他", or "剩余" as explicit exclusions when the user is also discussing writes. They are ambiguous unless the user's own wording says to exclude a named object or semantic role; in ambiguous cases, the structured content must name both scopes.
- Do not exclude records from a request-time information/aggregate/list/summary/value merely because they are later modified, canceled, upgraded, consumed, split, or otherwise changed. Exclude them only when the user's own wording explicitly excluded that semantic role from the earlier information request.
- If later writes affect records that could belong to a requested information/list/aggregate value, the structured content must name both the original request-time scope and the post-action/current remaining scope; do not write none/无.
- For total cost, paid amount, balance, refund, or similar monetary aggregates, bind the answer to the canonical runtime value field when one exists: explicit total/paid/charged/order/payment-history amount fields beat reconstructed lower-level unit/segment/item price sums. Use line items only when no canonical total exists, or as a cross-check. If those values differ, the experience must tell the future agent which source field to prefer. Do not name lower-level price fields as the primary source when a record-level total/paid/charged amount is available in runtime evidence.
- `Does not apply when` must describe a task-pattern mismatch, not a temporal stage. Do not write conditions such as "still reading", "before final response", "before writes complete", or "not yet at final_response"; the skill loader may read the experience at task start even when it applies at a later boundary.
- If a loaded existing experience encodes the misleading rule that later-modified/canceled/upgraded records should be removed from an earlier requested aggregate, update that experience instead of creating a competing memory.
- Do not encode dataset-specific values, IDs, amounts, or domain names in the reusable rule; express the lesson as source-scope binding, freeze point, included/excluded object roles, and later-write effect.
- Preserve correct near-misses: the applicability and anti-pattern content must say when NOT to apply the experience.
- Avoid evaluator/control-plane wording such as evaluation, evaluator, communicate_checks, action_checks, db_check, reward, rubric, 评估, 奖励. Rewrite into runtime facts.
- Keep it concise, imperative, and machine-readable. No raw IDs, hidden answers, policy dumps, or full task paths.
- Use the same language for all `experience_name` values.

{default_experience_gate_contract(schema)}
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

        prefetch_messages: List[Dict[str, Any]] = []
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

        for candidate in bundle.candidates:
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

        prefetch_messages.append(
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "You have already read one `new_trajectory`, optional exact-case successful `comparison_trajectory` records, and candidate experience memories.",
                        "Treat `new_trajectory` as the new execution to incorporate.",
                        "Treat `comparison_trajectory` as factual peer evidence for comparing success and failure paths; do not modify it directly.",
                        "Treat `candidate_experience` as existing memories you may update, replace, or skip.",
                        "Based on the above, decide whether to **Update**, **Replace**, **Create**, or **Skip** a failure-repair experience. Output JSON only.",
                        "Only reusable failure patterns should produce entries; successful or unrelated intents should produce no experience changes.",
                    ]
                ),
            }
        )
        return prefetch_messages
