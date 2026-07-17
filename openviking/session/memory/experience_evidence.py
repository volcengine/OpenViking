# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, ToolContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.tools import get_tool
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import replay, tracer
from openviking.telemetry.replay.models import EncodedValue, ReplayCodecError

EXPERIENCE_MEMORY_TYPE = "experiences"
TRAJECTORY_MEMORY_TYPE = "trajectories"
SEARCH_TOP_K = 5
SOURCE_TRAJ_TOP_K = 3
MAX_SOURCE_TRAJS = 3
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
    source_trajectories: list[TrajectoryEvidence] = field(default_factory=list)


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
        return {
            "memory_file": _encode_memory_file(value.memory_file, encode),
            "source_trajectories": encode(value.source_trajectories),
        }

    @staticmethod
    def decode(payload, decode):
        return CandidateExperienceEvidence(
            memory_file=_decode_memory_file(_encoded(payload, "memory_file"), decode),
            source_trajectories=decode(_encoded(payload, "source_trajectories")),
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
        for index, uri in enumerate(candidate_uris):
            memory_file = await self._read_memory_file(uri, ctx)
            if memory_file is None:
                continue
            source_trajectories = []
            if index < SOURCE_TRAJ_TOP_K:
                source_trajectories = await self._load_source_trajectories(memory_file, ctx)
            candidates.append(
                CandidateExperienceEvidence(
                    memory_file=memory_file,
                    source_trajectories=source_trajectories,
                )
            )
        return candidates

    async def _load_source_trajectories(
        self,
        experience: MemoryFile,
        ctx: RequestContext,
    ) -> list[TrajectoryEvidence]:
        uris = [
            str(link.get("to_uri") or "")
            for link in experience.links or []
            if link.get("link_type") == "derived_from" and link.get("to_uri")
        ]
        results = []
        for uri in uris[-MAX_SOURCE_TRAJS:]:
            memory_file = await self._read_memory_file(uri, ctx)
            if memory_file is not None:
                results.append(TrajectoryEvidence(memory_file))
        return results

    async def _load_comparison_trajectories(
        self,
        query: ExperienceEvidenceQuery,
        ctx: RequestContext,
    ) -> list[TrajectoryEvidence]:
        if not query.trajectory_dir:
            return []
        seen = {query.trajectory_uri}
        linked_uris = await self._case_linked_trajectory_uris(query, ctx)
        if linked_uris is not None:
            results = await self._read_trajectory_evidence(linked_uris, seen, ctx)
            successes = [item for item in results if _is_success_trajectory(item.memory_file)]
            return successes[:COMPARISON_TRAJ_TOP_K]

        candidate_uris = await self._search_uris(
            query.trajectory_summary[:500] or "trajectory",
            [query.trajectory_dir],
            COMPARISON_TRAJ_TOP_K + 2,
            ctx,
        )
        results = await self._read_trajectory_evidence(candidate_uris, seen, ctx)
        return [item for item in results if _is_success_trajectory(item.memory_file)][
            :COMPARISON_TRAJ_TOP_K
        ]

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
