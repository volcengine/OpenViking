# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Agent Experience Context Provider - Phase 2 of agent-scope memory extraction.

Given a new trajectory summary from Phase 1, load deterministic candidate experiences
and let the LLM decide whether to update an existing one, create a new one, or do nothing.

No tool calls — the current trajectory, exact-case successful comparisons, and
existing experience candidates are prefetched.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.experience_lifecycle import normalize_experience_status
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import replay, tracer
from openviking.telemetry.replay.models import EncodedValue, ReplayCodecError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

EXPERIENCE_MEMORY_TYPE = "experiences"
TRAJECTORY_MEMORY_TYPE = "trajectories"
COMPARISON_TRAJ_TOP_K = 6
COMPARISON_TRAJ_INJECT_TOP_K = 2
SEMANTIC_EXPERIENCE_TOP_K = 5
MAX_COMPARISON_TRAJ_CHARS = 6000


@dataclass(slots=True)
class ExperienceEvidenceQuery:
    trajectory_summary: str
    trajectory_uri: str
    trajectory_dir: str
    case_uri: str = ""
    case_name: str = ""
    task_signature: str = ""
    loaded_experience_uris: list[str] = field(default_factory=list)


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
            "trajectory_dir": encode(value.trajectory_dir),
            "case_uri": encode(value.case_uri),
            "case_name": encode(value.case_name),
            "task_signature": encode(value.task_signature),
            "loaded_experience_uris": encode(value.loaded_experience_uris),
        }

    @staticmethod
    def decode(payload, decode):
        return ExperienceEvidenceQuery(
            trajectory_summary=decode(_encoded(payload, "trajectory_summary")),
            trajectory_uri=decode(_encoded(payload, "trajectory_uri")),
            trajectory_dir=decode(_encoded(payload, "trajectory_dir")),
            case_uri=decode(_encoded(payload, "case_uri")),
            case_name=decode(_encoded(payload, "case_name")),
            task_signature=decode(_encoded(payload, "task_signature")),
            loaded_experience_uris=decode(_encoded(payload, "loaded_experience_uris")),
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
        case_file = await self._load_case_file(query, ctx)
        candidate_uris = _unique_experience_uris(
            [
                *_case_linked_experience_uris(case_file),
                *query.loaded_experience_uris,
                *(await self._search_candidate_experience_uris(query, ctx)),
            ]
        )
        candidates = await self._load_candidates(candidate_uris, ctx)
        comparisons = await self._load_comparison_trajectories(query, case_file, ctx)
        return ExperienceEvidenceBundle(
            candidates=candidates,
            comparison_trajectories=comparisons,
        )

    async def _search_candidate_experience_uris(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> list[str]:
        """Find global semantic candidates so equivalent roots are updated, not duplicated."""

        target_uri = _experience_directory_uri(query.case_uri or query.trajectory_uri)
        search_query = "\n".join(
            part.strip()
            for part in (
                query.case_name,
                query.task_signature,
                query.trajectory_summary[:4000],
            )
            if part and part.strip()
        )
        if not target_uri or not search_query:
            return []
        try:
            result = await self._viking_fs.find(
                search_query,
                target_uri=target_uri,
                limit=SEMANTIC_EXPERIENCE_TOP_K,
                level=[2],
                ctx=ctx,
            )
        except Exception as error:
            logger.warning(f"Failed to search candidate experiences: {error}")
            return []

        uris: list[str] = []
        for item in getattr(result, "memories", []) or []:
            uri = str(getattr(item, "uri", "") or "")
            if not uri and isinstance(item, dict):
                uri = str(item.get("uri") or "")
            if "/memories/experiences/" not in uri or not uri.endswith(".md"):
                continue
            if uri.endswith("/.overview.md") or uri in uris:
                continue
            uris.append(uri)
        return uris[:SEMANTIC_EXPERIENCE_TOP_K]

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
            if memory_file.memory_type and memory_file.memory_type != EXPERIENCE_MEMORY_TYPE:
                continue
            if normalize_experience_status(
                (memory_file.extra_fields or {}).get("status")
            ) == "archived":
                continue
            candidates.append(CandidateExperienceEvidence(memory_file=memory_file))
        return candidates

    async def _load_comparison_trajectories(
        self,
        query: ExperienceEvidenceQuery,
        case_file: MemoryFile | None,
        ctx: RequestContext,
    ) -> list[TrajectoryEvidence]:
        if not query.trajectory_dir:
            return []
        seen = {query.trajectory_uri}
        linked_uris = _case_linked_trajectory_uris(case_file)
        results = await self._read_trajectory_evidence(linked_uris, seen, ctx)
        non_successes = [item for item in results if not _is_success_trajectory(item.memory_file)]
        successes = [item for item in results if _is_success_trajectory(item.memory_file)]
        ordered: list[TrajectoryEvidence] = []
        if non_successes:
            ordered.append(non_successes.pop(0))
        if successes:
            ordered.append(successes.pop(0))
        ordered.extend(non_successes)
        ordered.extend(successes)
        return ordered[:COMPARISON_TRAJ_TOP_K]

    async def _load_case_file(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> MemoryFile | None:
        case_uri = await self._resolve_case_uri(query, ctx)
        if not case_uri:
            return None
        return await self._read_memory_file(case_uri, ctx)

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


def _is_success_trajectory(memory_file: MemoryFile) -> bool:
    return str((memory_file.extra_fields or {}).get("outcome") or "").strip().lower() == "success"


def _case_linked_experience_uris(case_file: MemoryFile | None) -> list[str]:
    if case_file is None:
        return []
    return _unique_experience_uris(_linked_uris(case_file))


def _case_linked_trajectory_uris(case_file: MemoryFile | None) -> list[str]:
    if case_file is None:
        return []
    recency_by_uri: dict[str, tuple[str, str]] = {}
    for link in list(case_file.links or []) + list(case_file.backlinks or []):
        if str(link.get("link_type") or "") not in {
            "successful_trajectory",
            "failed_trajectory",
            "partial_trajectory",
            "unfinished_trajectory",
            "unknown_trajectory",
        }:
            continue
        created_at = str(link.get("created_at") or "")
        for uri in _link_uris(link):
            if "/memories/trajectories/" not in uri:
                continue
            recency = (created_at, uri)
            previous = recency_by_uri.get(uri)
            if previous is None or recency > previous:
                recency_by_uri[uri] = recency
    return [uri for _, uri in sorted(recency_by_uri.values(), reverse=True)]


def _linked_uris(memory_file: MemoryFile) -> list[str]:
    return [
        uri
        for link in list(memory_file.links or []) + list(memory_file.backlinks or [])
        for uri in _link_uris(link)
    ]


def _link_uris(link: dict[str, Any]) -> list[str]:
    return [str(link.get(key) or "") for key in ("to_uri", "from_uri")]


def _unique_experience_uris(uris: list[str]) -> list[str]:
    return list(dict.fromkeys(uri for uri in uris if "/memories/experiences/" in str(uri or "")))


def _experience_directory_uri(uri: str) -> str:
    prefix, separator, _ = str(uri or "").partition("/memories/")
    if not separator or not prefix.startswith("viking://user/"):
        return ""
    return f"{prefix}/memories/experiences"


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
        loaded_experience_uris: list[str] | None = None,
        evidence_loader: ExperienceEvidenceLoader | None = None,
    ):
        self.trajectory_summary = trajectory_summary
        super().__init__(messages=[], latest_archive_overview=latest_archive_overview)
        self.trajectory_uri = trajectory_uri
        self.case_uri = str(case_uri or "")
        self.case_name = str(case_name or "")
        self.task_signature = str(task_signature or "")
        self.loaded_experience_uris = list(loaded_experience_uris or [])
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
        return f"""You are a memory extraction agent. Distill reusable failure-repair experiences from failed or partially failed agent execution trajectories, or from a narrowly eligible successful trajectory with `recovery_evidence.status=observed_recovered`.

## Inputs

- One failed or partial `new_trajectory`, or one successful `new_trajectory` whose structured
  recovery evidence proves a material failed path, an actually executed alternative, and a
  verified recovered result
- Up to two `comparison_trajectory` records from the exact same case, including successful and
  non-successful peers when available
- Existing `candidate_experience` memories linked to the exact case, actually loaded in the
  failed rollout, or found as semantically similar reusable failure patterns

Source and comparison trajectories are evidence only. Do not copy or modify trajectory text in
the output.

## Extraction algorithm

For each material failure, reason in this order before producing any entry:
1. Contract: identify the required result from the user request, authoritative input, or observable
   runtime contract. Evaluation feedback may identify a miss, but must not become a hidden runtime
   requirement.
2. First controllable divergence: locate the earliest observable agent decision, action, omitted
   check, or produced output that caused or failed to prevent that one miss. If it is unknown, skip.
3. Replacement behavior: state the smallest future decision rule or action change that would alter
   the result while preserving nearby correct behavior.
4. Independent proof: define how the future agent verifies the actual result from authoritative
   input or an independently observable artifact, and what exact check is repeated after repair.
5. Safe fallback: define what to do when the evidence, capability, conversion, or check is
   unavailable without fabricating data or silently weakening the user's request.
6. Transfer test: mentally substitute different entities, dates, amounts, filenames, and valid
   input values from the same task family. Keep the entry only when its applicability boundary,
   decision rule, actions, verification relationship, and fallback remain correct. Otherwise skip;
   do not paraphrase the source task into an Experience.

For a successful recovered trajectory, treat the observable primary-path failure as the failure
boundary. Extract only the narrow switch to the alternative path that was actually executed and
verified. Do not turn the rest of the successful run into a positive workflow or full SOP. If the
trace shows only a same-path retry, cleanup, minor syntax correction, or an unverified alternative,
output no changes.

Create separate entries when failures have different evidence, decision boundaries, repairs, or
verification methods. Do not create an entry merely to restate requested content or list everything
that was missed.

## Decision and output

- Existing experience is correct but the agent ignored it: skip unless its wording or
  applicability is too weak to guide skill loading.
- Existing experience is misleading, over-broad, or too weak: update it.
- No relevant experience exists and the failure has a reusable preventive repair: create it.
- A successful trajectory with `recovery_evidence.status=observed_recovered` may create or update
  one narrow recovery Experience when the failed boundary, executed alternative, and verification
  are all supported by runtime evidence.
- Ordinary successful, case-specific, unsupported, random, already-covered, or non-preventable
  trajectories: output no changes.
- A non-full trajectory may still contribute a narrow, evidenced repair. Update the single
  compatible candidate when peer trajectories support the same trigger, decision boundary,
  corrective action, and verification. Do not broaden the rule merely to make sources agree.
- Treat trajectories as factual evidence, not authoritative conclusions. Compare observations,
  decisions, actions, verification, and outputs at the first material divergence.
- Do not copy trajectory wording directly into an experience. Re-check runtime evidence,
  injected experience effects, existing experiences, and the gate contract below.

Each entry must provide:
- `experience_name`: an existing name for Update or a new name for Create
- Every structured content field declared by the schema: {content_fields}
- `supersedes`: an older experience name only when the corrected experience genuinely replaces it

The storage template defines the Markdown structure and order. Do not include headings inside
field values. {situation_guidance}The complete rendered Experience must contain Situation,
Reminder, Procedure, Verification, Fallback, and Anti-pattern. Do not output `trigger_code`; it is
not used by the skill loader.

The system applies same-name entries as updates and new names as creates. Use `supersedes` instead
of `delete_ids`. Keep content concise, imperative, free of case IDs and hidden answers, and use the
same language for all `experience_name` values.

{default_experience_gate_contract()}
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
            trajectory_dir=self._render_trajectory_dir(ctx),
            case_uri=self.case_uri,
            case_name=self.case_name,
            task_signature=self.task_signature,
            loaded_experience_uris=self.loaded_experience_uris,
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
                        "You have already read one `new_trajectory`, optional exact-case peer `comparison_trajectory` records, and candidate experience memories.",
                        "Treat `new_trajectory` as the new execution to incorporate.",
                        "Treat `comparison_trajectory` as factual peer evidence for comparing success and failure paths; do not modify it directly.",
                        "Treat `candidate_experience` as existing memories you may update, replace, or skip.",
                        "Based on the above, decide whether to **Update**, **Create**, or **Skip** a failure-repair experience. Output JSON only.",
                        "Only reusable failure patterns should produce entries. An ordinary successful or unrelated intent produces no changes; a successful new_trajectory is eligible only when recovery_evidence.status=observed_recovered and the trace proves the failed boundary, actually executed alternative, and verified recovered result.",
                    ]
                ),
            }
        )
        return prefetch_messages
