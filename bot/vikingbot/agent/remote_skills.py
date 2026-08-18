"""Request-scoped OpenViking Skill discovery, activation, and materialization."""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import hashlib
import html
import json
import re
import shlex
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from loguru import logger

from openviking.core.namespace import classify_uri, uri_parts
from openviking.core.skill_loader import SkillLoader
from openviking.utils.path_safety import sanitize_relative_viking_path
from vikingbot.agent.remote_skill_cache import (
    RemoteSkillCacheError,
    RemoteSkillCacheKey,
    RemoteSkillSnapshotCache,
    SnapshotBuildResult,
)
from vikingbot.openviking_mount.ov_server import VikingClient

if TYPE_CHECKING:
    from vikingbot.agent.tools.base import Tool
    from vikingbot.config.schema import Config, RemoteSkillsConfig, SessionKey
    from vikingbot.sandbox.manager import SandboxManager


_SKILL_EXCLUDED_FILES = frozenset(
    {".abstract.md", ".overview.md", ".relations.json", ".source.json"}
)
_EXEC_PATH_PREFIX = r"(?<![A-Za-z0-9_./-])"
_EXEC_PATH_SUFFIX = r"(?=$|[\s\"'`;|&()<>])"
_MATERIALIZED_SKILL_ROOT = ".remote-skill"
_TRANSPORT_TOOLS = frozenset({"openviking_multi_read"})
_SHELL_CONTROL_TOKENS = frozenset({";", "&", "&&", "|", "||", "<", ">", "<<", ">>"})
_EXEC_FILE_CONSUMERS = frozenset(
    {
        ".",
        "bash",
        "cat",
        "chmod",
        "cp",
        "head",
        "node",
        "perl",
        "python",
        "python3",
        "ruby",
        "sed",
        "sh",
        "source",
        "tail",
    }
)
_RESOURCE_LIKE_SUFFIXES = frozenset(
    {
        ".bash",
        ".bin",
        ".cfg",
        ".csv",
        ".docx",
        ".ini",
        ".jpeg",
        ".jpg",
        ".json",
        ".md",
        ".model",
        ".png",
        ".py",
        ".sh",
        ".sql",
        ".svg",
        ".toml",
        ".tsv",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_TOOL_ALIASES = {
    "bash": "exec",
    "exec": "exec",
    "read": "read_file",
    "read_file": "read_file",
    "write": "write_file",
    "write_file": "write_file",
    "edit": "edit_file",
    "edit_file": "edit_file",
    "glob": "openviking_glob",
    "grep": "openviking_grep",
    "webfetch": "web_fetch",
    "web_fetch": "web_fetch",
    "websearch": "web_search",
    "web_search": "web_search",
    "spawn": "spawn",
}


class SkillRuntimeError(RuntimeError):
    """A stable, user-facing remote Skill runtime failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.code}: {super().__str__()}"


class _CachedSnapshotCorrupt(RuntimeError):
    """A host-side cache entry no longer matches its validated manifest."""


@dataclass(frozen=True)
class RemoteSkillCandidate:
    name: str
    description: str
    root_uri: str
    skill_md_uri: str
    source: str
    score: float = 0.0
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteSkillFile:
    path: str
    uri: str
    is_dir: bool = False
    kind: str = "auxiliary"
    size: int | None = None
    sha256: str | None = None
    revision: str | None = None


@dataclass
class ActiveRemoteSkill:
    name: str
    description: str
    root_uri: str
    skill_md_uri: str
    content: str
    source: str
    files: dict[str, RemoteSkillFile]
    allowed_tools: tuple[str, ...] = ()
    allowed_tools_declared: bool = False
    requires: dict[str, list[str]] = field(default_factory=dict)
    revision: str = ""
    manifest_signature: str = ""


@dataclass(frozen=True)
class PreparedToolCall:
    params: dict[str, Any]
    skill_uris: tuple[str, ...] = ()


def _resource_path_segments(expression: str) -> tuple[str, ...]:
    """Parse a top-level parameter name or a JSON-Pointer-like resource path."""
    if not expression.startswith("/"):
        return (expression,)
    return tuple(
        segment.replace("~1", "/").replace("~0", "~")
        for segment in expression.split("/")[1:]
        if segment
    )


def _iter_resource_slots(
    value: Any,
    segments: tuple[str, ...],
) -> Iterable[tuple[Any, str | int, Any]]:
    """Yield mutable parent/key/value slots selected by a resource path."""
    if not segments:
        return
    head, *tail = segments
    if head == "*":
        items: Iterable[tuple[str | int, Any]]
        if isinstance(value, list):
            items = enumerate(value)
        elif isinstance(value, dict):
            items = value.items()
        else:
            return
        for key, child in items:
            if tail:
                yield from _iter_resource_slots(child, tuple(tail))
            else:
                yield value, key, child
        return
    if isinstance(value, dict) and head in value:
        child = value[head]
        if tail:
            yield from _iter_resource_slots(child, tuple(tail))
        else:
            yield value, head, child


def _normalize_uri(uri: str) -> str:
    return str(uri or "").strip().rstrip("/")


def _skill_identity_from_uri(uri: str) -> tuple[str, str, str, str]:
    """Return (name, target_uri, root_uri, skill_md_uri) for one Skill definition."""
    normalized = _normalize_uri(uri)
    parts = uri_parts(normalized)
    classification = classify_uri(normalized)
    index = classification.content_index
    if not classification.is_skill or index is None:
        raise SkillRuntimeError(
            "SKILL_INVALID", "Skill URI is outside an OpenViking skills namespace"
        )

    has_definition = len(parts) == index + 3 and parts[-1] == "SKILL.md"
    has_root = len(parts) == index + 2
    if not has_definition and not has_root:
        raise SkillRuntimeError(
            "SKILL_INVALID", "Skill URI must identify one Skill root or its SKILL.md"
        )

    name = parts[index + 1]
    root_parts = parts[:-1] if has_definition else parts
    root_uri = "viking://" + "/".join(root_parts)
    target_uri = "viking://" + "/".join(parts[: index + 1])
    return name, target_uri, root_uri, f"{root_uri}/SKILL.md"


def _skill_root_for_resource_uri(uri: str) -> str | None:
    """Return a containing Skill root for any URI inside a skills namespace."""
    normalized = _normalize_uri(uri)
    classification = classify_uri(normalized)
    index = classification.content_index
    parts = list(classification.parts)
    if not classification.is_skill or index is None or len(parts) < index + 2:
        return None
    return "viking://" + "/".join(parts[: index + 2])


def _parse_vikingbot_metadata(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        scoped = raw.get("vikingbot", raw)
        return dict(scoped) if isinstance(scoped, Mapping) else {}
    if isinstance(raw, str):
        try:
            return _parse_vikingbot_metadata(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _normalize_requires(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, Mapping):
        return {"bins": [], "env": []}
    normalized: dict[str, list[str]] = {}
    for key in ("bins", "env"):
        value = raw.get(key) or []
        if isinstance(value, str):
            value = [value]
        normalized[key] = [str(item).strip() for item in value if str(item).strip()]
    return normalized


def _safe_skill_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-")
    return safe or "remote-skill"


def _source_for_uri(root_uri: str) -> str:
    return (
        "openviking_agent" if root_uri.startswith("viking://agent/skills/") else "openviking_user"
    )


def _manifest_signature(result: Mapping[str, Any]) -> str:
    files = []
    for item in result.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        files.append(
            {
                "path": item.get("path"),
                "uri": item.get("uri"),
                "is_dir": bool(item.get("is_dir", item.get("isDir", False))),
                "size": item.get("size"),
                "sha256": item.get("sha256"),
                "revision": item.get("revision"),
            }
        )
    payload = {
        "root_uri": result.get("root_uri") or result.get("uri"),
        "revision": result.get("revision"),
        "content_sha256": result.get("content_sha256"),
        "files": sorted(files, key=lambda item: str(item.get("path") or "")),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class SkillRuntimeContext:
    """State shared by all model/tool iterations in one VikingBot turn."""

    def __init__(
        self,
        *,
        config: "Config",
        session_key: "SessionKey",
        sandbox_manager: "SandboxManager | None",
        workspace_id: str,
        openviking_connection: dict[str, Any] | None,
        actor_peer_id: str | None,
        local_skill_names: Iterable[str] = (),
        request_id: str | None = None,
    ):
        self.config = config
        self.settings: RemoteSkillsConfig = config.remote_skills
        self.session_key = session_key
        self.sandbox_manager = sandbox_manager
        self.workspace_id = workspace_id
        self.openviking_connection = openviking_connection
        self.actor_peer_id = actor_peer_id
        self.local_skill_names = {str(name) for name in local_skill_names}
        self.request_id = request_id or uuid.uuid4().hex
        self.candidates: list[RemoteSkillCandidate] = []
        self.active_skills: dict[str, ActiveRemoteSkill] = {}
        self.materializations: dict[str, str] = {}
        self.usage: list[dict[str, Any]] = []
        self._client: VikingClient | None = None
        self._activation_locks: dict[str, asyncio.Lock] = {}
        self._materialization_locks: dict[str, asyncio.Lock] = {}
        self._requirement_locks: dict[str, asyncio.Lock] = {}
        self._requirements_checked: set[str] = set()
        self._materialization_started = False
        self._closed = False
        cache = getattr(sandbox_manager, "remote_skill_cache", None)
        self._snapshot_cache: RemoteSkillSnapshotCache | None = (
            cache if isinstance(cache, RemoteSkillSnapshotCache) else None
        )

    async def _get_client(self) -> VikingClient:
        if self._client is None:
            self._client = await VikingClient.create(
                self.workspace_id,
                connection=self.openviking_connection,
                actor_peer_id=self.actor_peer_id,
                config=self.config,
            )
        return self._client

    async def discover(self, query: str) -> list[RemoteSkillCandidate]:
        """Discover only L0 Skill summaries for the current user query."""
        if not str(query or "").strip():
            return []
        try:
            client = await self._get_client()
            result = await asyncio.wait_for(
                client.find_skills(
                    str(query),
                    limit=self.settings.discovery_limit,
                    score_threshold=self.settings.score_threshold,
                ),
                timeout=self.settings.discovery_timeout_seconds,
            )
        except Exception as exc:
            logger.warning("OpenViking Skill discovery skipped: {}", exc)
            self.candidates = []
            return []

        by_name: dict[str, RemoteSkillCandidate] = {}
        for item in result.get("skills") or []:
            if not isinstance(item, Mapping):
                continue
            root_uri = _normalize_uri(str(item.get("root_uri") or item.get("uri") or ""))
            if not root_uri:
                continue
            try:
                uri_name, _target, canonical_root, skill_md_uri = _skill_identity_from_uri(root_uri)
            except SkillRuntimeError:
                continue
            name = str(item.get("name") or uri_name).strip()
            if not name or name in self.local_skill_names:
                continue
            candidate = RemoteSkillCandidate(
                name=name,
                description=str(item.get("description") or "").strip(),
                root_uri=canonical_root,
                skill_md_uri=skill_md_uri,
                source=_source_for_uri(canonical_root),
                score=float(item.get("score") or 0.0),
                allowed_tools=tuple(str(value) for value in item.get("allowed_tools") or []),
            )
            current = by_name.get(name)
            if current is None or self._candidate_precedes(candidate, current):
                by_name[name] = candidate

        self.candidates = sorted(
            by_name.values(),
            key=lambda candidate: (
                0 if candidate.source == "openviking_user" else 1,
                -candidate.score,
                candidate.name,
            ),
        )[: self.settings.discovery_limit]
        return list(self.candidates)

    @staticmethod
    def _candidate_precedes(candidate: RemoteSkillCandidate, current: RemoteSkillCandidate) -> bool:
        candidate_rank = 0 if candidate.source == "openviking_user" else 1
        current_rank = 0 if current.source == "openviking_user" else 1
        return (candidate_rank, -candidate.score) < (current_rank, -current.score)

    def build_discovery_summary(self) -> str:
        if not self.candidates:
            return ""
        lines = ['<skills source="openviking">']
        for skill in self.candidates:
            lines.extend(
                [
                    f'  <skill source="{skill.source}" available="true">',
                    f"    <name>{html.escape(skill.name)}</name>",
                    f"    <description>{html.escape(skill.description)}</description>",
                    f"    <location>{html.escape(skill.skill_md_uri)}</location>",
                    "    <read_tool>openviking_multi_read</read_tool>",
                    "  </skill>",
                ]
            )
        lines.append("</skills>")
        return "\n".join(lines)

    @staticmethod
    def is_skill_definition_uri(uri: str) -> bool:
        try:
            _skill_identity_from_uri(uri)
        except SkillRuntimeError:
            return False
        return _normalize_uri(uri).endswith("/SKILL.md")

    def is_activation_call(self, tool_name: str, params: Mapping[str, Any]) -> bool:
        if tool_name != "openviking_multi_read":
            return False
        return any(self.is_skill_definition_uri(str(uri)) for uri in params.get("uris") or [])

    def activation_succeeded(self, params: Mapping[str, Any]) -> bool:
        """Confirm every Skill definition requested by a multi-read became active."""
        for uri in params.get("uris") or []:
            if not self.is_skill_definition_uri(str(uri)):
                continue
            try:
                root_uri = _skill_identity_from_uri(str(uri))[2]
            except SkillRuntimeError:
                return False
            if root_uri not in self.active_skills:
                return False
        return True

    async def activate_from_read(self, uri: str, content: str) -> ActiveRemoteSkill | None:
        """Activate a Skill after the existing multi-read tool reads SKILL.md."""
        if not self.is_skill_definition_uri(uri):
            self.note_remote_read(uri)
            return None

        name, target_uri, root_uri, skill_md_uri = _skill_identity_from_uri(uri)
        lock = self._activation_locks.setdefault(root_uri, asyncio.Lock())
        async with lock:
            existing = self.active_skills.get(root_uri)
            if existing is not None:
                return existing
            if len(content.encode("utf-8")) > self.settings.max_file_bytes:
                raise SkillRuntimeError(
                    "SKILL_PACKAGE_TOO_LARGE", "Remote SKILL.md exceeds the file limit"
                )
            try:
                parsed = SkillLoader.parse(content, source_path=skill_md_uri)
            except Exception as exc:
                raise SkillRuntimeError("SKILL_INVALID", str(exc)) from exc
            parsed_name = str(parsed.get("name") or "").strip()
            if parsed_name != name:
                raise SkillRuntimeError(
                    "SKILL_INVALID",
                    f"Skill name {parsed_name!r} does not match URI name {name!r}",
                )

            client = await self._get_client()
            try:
                result = await client.get_skill(
                    name,
                    target_uri=target_uri,
                    include_content=True,
                    include_files=True,
                    include_integrity=True,
                )
            except Exception as exc:
                raise SkillRuntimeError("SKILL_NOT_FOUND", str(exc)) from exc

            returned_root = _normalize_uri(str(result.get("root_uri") or result.get("uri") or ""))
            if returned_root != root_uri:
                raise SkillRuntimeError(
                    "SKILL_INVALID", "OpenViking returned a different canonical Skill root"
                )

            canonical_content = str(result.get("content") or "")
            if not canonical_content:
                raise SkillRuntimeError(
                    "SKILL_INTEGRITY_UNAVAILABLE",
                    "OpenViking did not return the canonical SKILL.md content",
                )
            if canonical_content != content:
                raise SkillRuntimeError(
                    "SKILL_REVISION_CHANGED",
                    "Remote SKILL.md changed between read and activation",
                )
            content_checksum = str(result.get("content_sha256") or "").strip()
            if not content_checksum:
                raise SkillRuntimeError(
                    "SKILL_INTEGRITY_UNAVAILABLE",
                    "OpenViking did not return a SKILL.md content checksum",
                )
            if hashlib.sha256(canonical_content.encode("utf-8")).hexdigest() != content_checksum:
                raise SkillRuntimeError(
                    "SKILL_REVISION_CHANGED",
                    "OpenViking returned inconsistent SKILL.md integrity metadata",
                )
            revision = str(result.get("revision") or "").strip()
            if not revision:
                raise SkillRuntimeError(
                    "SKILL_INTEGRITY_UNAVAILABLE",
                    "OpenViking did not return a Skill revision",
                )

            files: dict[str, RemoteSkillFile] = {}
            for item in result.get("files") or []:
                if not isinstance(item, Mapping):
                    continue
                raw_path = str(item.get("path") or "")
                if not raw_path:
                    continue
                try:
                    path = sanitize_relative_viking_path(raw_path)
                except ValueError as exc:
                    raise SkillRuntimeError("SKILL_INVALID", str(exc)) from exc
                item_uri = _normalize_uri(str(item.get("uri") or ""))
                expected_uri = f"{root_uri}/{path}"
                if item_uri != expected_uri:
                    raise SkillRuntimeError(
                        "SKILL_INVALID",
                        f"Skill file URI does not match its canonical path: {raw_path}",
                    )
                if path in files:
                    raise SkillRuntimeError(
                        "SKILL_INVALID", f"Skill manifest contains duplicate path: {path}"
                    )
                is_dir = bool(item.get("is_dir", item.get("isDir", False)))
                size = int(item["size"]) if item.get("size") is not None else None
                checksum = str(item.get("sha256") or "") or None
                if not is_dir and (size is None or checksum is None):
                    raise SkillRuntimeError(
                        "SKILL_INTEGRITY_UNAVAILABLE",
                        f"OpenViking did not return integrity metadata for {path}",
                    )
                if not is_dir and (
                    size < 0 or re.fullmatch(r"[0-9a-f]{64}", checksum or "") is None
                ):
                    raise SkillRuntimeError(
                        "SKILL_INVALID", f"Skill file has invalid integrity metadata: {path}"
                    )
                files[path] = RemoteSkillFile(
                    path=path,
                    uri=item_uri,
                    is_dir=is_dir,
                    kind=str(item.get("kind") or "auxiliary"),
                    size=size,
                    sha256=checksum,
                    revision=str(item.get("revision") or "") or None,
                )

            definition = files.get("SKILL.md")
            if definition is None or definition.is_dir:
                raise SkillRuntimeError(
                    "SKILL_INVALID", "OpenViking Skill manifest is missing SKILL.md"
                )
            definition_bytes = canonical_content.encode("utf-8")
            if definition.size != len(definition_bytes) or definition.sha256 != content_checksum:
                raise SkillRuntimeError(
                    "SKILL_REVISION_CHANGED",
                    "SKILL.md content does not match its manifest integrity metadata",
                )

            metadata = _parse_vikingbot_metadata(parsed.get("metadata"))
            active = ActiveRemoteSkill(
                name=name,
                description=str(parsed.get("description") or ""),
                root_uri=root_uri,
                skill_md_uri=skill_md_uri,
                content=canonical_content,
                source=_source_for_uri(root_uri),
                files=files,
                allowed_tools=tuple(str(value) for value in parsed.get("allowed_tools") or []),
                allowed_tools_declared=bool(parsed.get("allowed_tools_declared")),
                requires=_normalize_requires(metadata.get("requires")),
                revision=revision,
                manifest_signature=_manifest_signature(result),
            )
            self.active_skills[root_uri] = active
            self.usage.append({"event": "skill.activated", "skill_uri": root_uri})
            return active

    def note_remote_read(self, uri: str) -> None:
        normalized = _normalize_uri(uri)
        skill = self._active_skill_for_uri(normalized)
        if skill is not None:
            self.usage.append(
                {
                    "event": "skill.resource.remote_read",
                    "skill_uri": skill.root_uri,
                    "resource_uri": normalized,
                }
            )

    def render_skill_content(self, skill: ActiveRemoteSkill) -> str:
        """Append canonical resource bindings to the content shown to the model."""
        resources = [
            item
            for item in sorted(skill.files.values(), key=lambda value: value.path)
            if not item.is_dir
            and item.path != "SKILL.md"
            and Path(item.path).name not in _SKILL_EXCLUDED_FILES
        ]
        if not resources:
            return skill.content
        lines = [
            skill.content.rstrip(),
            "",
            "## OpenViking resource bindings",
            "",
            "Packaged resources are managed by VikingBot. Do not recreate, copy, or write "
            "them into the workspace, and do not set `working_dir` to an OpenViking storage "
            "or materialization path. Pass the exact relative path below (or its canonical "
            "URI) directly to the consuming tool; VikingBot materializes and rewrites it "
            "automatically. Use `workspace:<relative-path>` or an absolute path only for an "
            "ordinary workspace file.",
        ]
        lines.extend(f"- `{item.path}` → `{item.uri}`" for item in resources)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _tool_specs(skill: ActiveRemoteSkill, tool_name: str) -> list[str]:
        if not skill.allowed_tools_declared:
            return [tool_name]
        matched: list[str] = []
        for spec in skill.allowed_tools:
            head = str(spec).split("(", 1)[0].strip()
            normalized = _TOOL_ALIASES.get(head.lower(), head)
            if normalized == tool_name:
                matched.append(str(spec).strip())
        return matched

    def allows_tool(self, tool_name: str) -> bool:
        """Return whether every active Skill allows this task tool."""
        if tool_name in _TRANSPORT_TOOLS or not self.active_skills:
            return True
        return all(self._tool_specs(skill, tool_name) for skill in self.active_skills.values())

    def prepare_tool_definition(self, tool: "Tool") -> dict[str, Any] | None:
        """Apply the conservative intersection of all active Skill policies."""
        schema = copy.deepcopy(tool.to_schema())
        if tool.name in _TRANSPORT_TOOLS or not self.active_skills:
            return schema
        if not self.allows_tool(tool.name):
            return None
        return schema

    @staticmethod
    def tool_params_for_validation(params: Mapping[str, Any]) -> dict[str, Any]:
        """Return a detached copy for native tool validation."""
        return copy.deepcopy(dict(params))

    async def prepare_tool_call(
        self,
        tool: "Tool",
        params: Mapping[str, Any],
    ) -> PreparedToolCall:
        effective = copy.deepcopy(dict(params))
        used: list[str] = []

        # Text resources stay remote. Resolve relative references such as
        # ``references/checklist.md`` to their canonical OpenViking URI before
        # handing the call to the existing multi-read transport.
        if tool.name == "openviking_multi_read" and isinstance(effective.get("uris"), list):
            resolved_uris: list[Any] = []
            for value in effective["uris"]:
                if not isinstance(value, str):
                    resolved_uris.append(value)
                    continue
                if value.startswith("viking://"):
                    if self.is_skill_definition_uri(value):
                        resolved_uris.append(value)
                        continue
                    skill_root = _skill_root_for_resource_uri(value)
                    if skill_root is None:
                        resolved_uris.append(value)
                        continue
                    skill = self.active_skills.get(skill_root)
                    if skill is None:
                        raise SkillRuntimeError(
                            "SKILL_NOT_ACTIVE",
                            f"Read {skill_root}/SKILL.md before accessing its resources",
                        )
                    match = self._resolve_active_resource(value, skill=skill)
                else:
                    match = self._resolve_active_resource(value, require_match=True)
                if match is None:  # pragma: no cover - require_match is fail-closed
                    resolved_uris.append(value)
                    continue
                skill, resource = match
                resolved_uris.append(resource.uri)
                used.append(skill.root_uri)
            effective["uris"] = resolved_uris

        policy_skills = [] if tool.name in _TRANSPORT_TOOLS else list(self.active_skills.values())
        for policy_skill in policy_skills:
            if not self._tool_specs(policy_skill, tool.name):
                raise SkillRuntimeError(
                    "SKILL_TOOL_NOT_ALLOWED",
                    f"Tool {tool.name!r} is not allowed by {policy_skill.root_uri}",
                )
            self._authorize_tool_arguments(policy_skill, tool.name, effective)
            await self._ensure_requirements(policy_skill)
            used.append(policy_skill.root_uri)

        resource_inputs = getattr(tool, "resource_inputs", {}) or {}
        for expression, resource_kind in resource_inputs.items():
            if resource_kind not in {"local_file", "local_directory"}:
                continue
            for parent, key, value in _iter_resource_slots(
                effective, _resource_path_segments(expression)
            ):
                if not isinstance(value, str):
                    continue
                resolved = await self._resolve_local_resource(
                    value,
                    resource_kind=resource_kind,
                )
                if resolved is not None:
                    local_path, skill_uri = resolved
                    parent[key] = local_path
                    if skill_uri:
                        used.append(skill_uri)

        if tool.name == "exec" and isinstance(effective.get("command"), str):
            command, exec_skill_uris = await self._rewrite_exec_command(effective["command"])
            effective["command"] = command
            used.extend(exec_skill_uris)

        return PreparedToolCall(
            params=effective,
            skill_uris=tuple(dict.fromkeys(used)),
        )

    def _authorize_tool_arguments(
        self,
        skill: ActiveRemoteSkill,
        tool_name: str,
        params: Mapping[str, Any],
    ) -> None:
        specs = self._tool_specs(skill, tool_name)
        if not skill.allowed_tools_declared or any("(" not in spec for spec in specs):
            return
        constrained = [spec for spec in specs if "(" in spec and spec.endswith(")")]
        if tool_name != "exec":
            raise SkillRuntimeError(
                "SKILL_TOOL_NOT_ALLOWED",
                f"Argument constraints for {tool_name!r} are not supported",
            )
        command = str(params.get("command") or "").strip()
        self._reject_compound_shell_command(command, skill.root_uri)
        patterns: list[str] = []
        for spec in constrained:
            body = spec.split("(", 1)[1][:-1]
            patterns.extend(part.strip() for part in body.split("|") if part.strip())
        for pattern in patterns:
            variants = [pattern]
            if ":" in pattern:
                variants.append(pattern.replace(":", " ", 1))
            if any(fnmatch.fnmatchcase(command, variant) for variant in variants):
                return
        raise SkillRuntimeError(
            "SKILL_TOOL_NOT_ALLOWED",
            f"Command is outside the Bash constraints declared by {skill.root_uri}",
        )

    @staticmethod
    def _reject_compound_shell_command(command: str, skill_uri: str) -> None:
        """Keep constrained Bash rules scoped to one simple shell command."""
        if "\n" in command or "\r" in command or "`" in command or "$(" in command:
            raise SkillRuntimeError(
                "SKILL_TOOL_NOT_ALLOWED",
                f"Compound shell syntax is outside the Bash constraints declared by {skill_uri}",
            )
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as exc:
            raise SkillRuntimeError("SKILL_TOOL_NOT_ALLOWED", str(exc)) from exc
        if any(token in _SHELL_CONTROL_TOKENS or set(token) <= set(";&|<>") for token in tokens):
            raise SkillRuntimeError(
                "SKILL_TOOL_NOT_ALLOWED",
                f"Compound shell syntax is outside the Bash constraints declared by {skill_uri}",
            )

    async def _ensure_requirements(self, skill: ActiveRemoteSkill) -> None:
        if skill.root_uri in self._requirements_checked:
            return
        lock = self._requirement_locks.setdefault(skill.root_uri, asyncio.Lock())
        async with lock:
            if skill.root_uri in self._requirements_checked:
                return
            if skill.requires.get("bins") or skill.requires.get("env"):
                if self.sandbox_manager is None:
                    raise SkillRuntimeError(
                        "SKILL_CAPABILITY_UNAVAILABLE",
                        "Remote Skill requirements need an execution sandbox",
                    )
                sandbox = await self.sandbox_manager.get_sandbox(self.session_key)
                await self._check_requirements(skill, sandbox)
            self._requirements_checked.add(skill.root_uri)

    def skill_uris_for_tool(
        self,
        tool_name: str,
        params: Mapping[str, Any],
        prepared: Iterable[str] = (),
    ) -> tuple[str, ...]:
        used = list(prepared)
        if tool_name == "openviking_multi_read":
            for uri in params.get("uris") or []:
                skill = self._active_skill_for_uri(str(uri))
                if skill is not None:
                    used.append(skill.root_uri)
        resolved = tuple(dict.fromkeys(used))
        for skill_uri in resolved:
            self.usage.append(
                {
                    "event": "skill.tool.used",
                    "skill_uri": skill_uri,
                    "tool_name": tool_name,
                }
            )
            logger.info(
                "Remote Skill tool usage: request_id={} skill_uri={} tool_name={}",
                self.request_id,
                skill_uri,
                tool_name,
            )
        return resolved

    async def _resolve_local_resource(
        self,
        value: str,
        *,
        resource_kind: str,
    ) -> tuple[str, str | None] | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if normalized.startswith(("http://", "https://", "data:")):
            return None
        if normalized.startswith("workspace:"):
            try:
                workspace_path = sanitize_relative_viking_path(normalized[len("workspace:") :])
            except ValueError as exc:
                raise SkillRuntimeError("SKILL_RESOURCE_NOT_FOUND", str(exc)) from exc
            return workspace_path, None
        if not normalized.startswith("viking://") and Path(normalized).is_absolute():
            return None
        if not self.active_skills and not normalized.startswith("viking://"):
            return None
        match = self._resolve_active_resource(
            normalized,
            allow_directory=resource_kind == "local_directory",
            require_match=bool(self.active_skills) or normalized.startswith("viking://"),
        )
        if match is None:
            return None
        matched_skill, resource = match
        root = await self.materialize(matched_skill)
        return f"{root}/{resource.path}", matched_skill.root_uri

    def _resolve_active_resource(
        self,
        value: str,
        *,
        skill: ActiveRemoteSkill | None = None,
        allow_directory: bool = False,
        require_match: bool = False,
    ) -> tuple[ActiveRemoteSkill, RemoteSkillFile] | None:
        normalized_value = str(value or "").strip()
        if not normalized_value:
            return None
        skills = [skill] if skill is not None else list(self.active_skills.values())
        if normalized_value.startswith("viking://"):
            uri = _normalize_uri(normalized_value)
            containing_root = _skill_root_for_resource_uri(uri)
            if containing_root is None:
                return None
            containing_skill = self.active_skills.get(containing_root)
            if containing_skill is None:
                raise SkillRuntimeError(
                    "SKILL_NOT_ACTIVE",
                    f"Read {containing_root}/SKILL.md before accessing its resources",
                )
            if skill is not None and containing_skill.root_uri != skill.root_uri:
                raise SkillRuntimeError(
                    "SKILL_RESOURCE_NOT_FOUND",
                    f"Resource {value!r} does not belong to {skill.root_uri}",
                )
            skills = [containing_skill]
            matches = [
                (candidate, item)
                for candidate in skills
                for item in candidate.files.values()
                if item.uri == uri
            ]
        else:
            try:
                relative = sanitize_relative_viking_path(normalized_value.removeprefix("./"))
            except ValueError as exc:
                raise SkillRuntimeError("SKILL_RESOURCE_NOT_FOUND", str(exc)) from exc
            matches = [
                (candidate, candidate.files[relative])
                for candidate in skills
                if relative in candidate.files
            ]
        if not allow_directory:
            matches = [(candidate, item) for candidate, item in matches if not item.is_dir]
        if len(matches) > 1:
            raise SkillRuntimeError(
                "SKILL_RESOURCE_AMBIGUOUS",
                f"Resource {value!r} exists in multiple active Skills; use its canonical URI",
            )
        if not matches and require_match:
            raise SkillRuntimeError(
                "SKILL_RESOURCE_NOT_FOUND",
                f"Resource {value!r} is not present in the active Skill manifest",
            )
        return matches[0] if matches else None

    async def _rewrite_exec_command(
        self,
        command: str,
    ) -> tuple[str, tuple[str, ...]]:
        if not self.active_skills:
            return command, ()
        rewritten = command
        used: list[str] = []
        candidates: dict[str, list[tuple[ActiveRemoteSkill, RemoteSkillFile]]] = {}
        for active_skill in self.active_skills.values():
            for item in active_skill.files.values():
                if (
                    item.is_dir
                    or item.path == "SKILL.md"
                    or Path(item.path).name in _SKILL_EXCLUDED_FILES
                ):
                    continue
                candidates.setdefault(item.path, []).append((active_skill, item))

        for uri in re.findall(r"viking://[^\s\"'`;|&()<>]+", command):
            if _skill_root_for_resource_uri(uri) is not None:
                self._resolve_active_resource(uri, require_match=True)

        workspace_paths: set[str] = set()
        workspace_pattern = re.compile(r"workspace:([^\s\"'`;|&()<>]+)")
        for workspace_match in list(workspace_pattern.finditer(rewritten)):
            try:
                workspace_path = sanitize_relative_viking_path(workspace_match.group(1))
            except ValueError as exc:
                raise SkillRuntimeError("SKILL_RESOURCE_NOT_FOUND", str(exc)) from exc
            workspace_paths.add(workspace_path)
        rewritten = workspace_pattern.sub(lambda match: match.group(1), rewritten)

        try:
            tokens = shlex.split(rewritten)
        except ValueError:
            tokens = []
        known_paths = set(candidates)
        command_name = Path(tokens[0]).name if tokens else ""
        for index, token in enumerate(tokens):
            explicit_relative = token.startswith("./")
            relative = token.removeprefix("./").rstrip(";,|&")
            if not relative or relative in workspace_paths:
                continue
            if relative.startswith(("/", "http://", "https://", "viking://", "-")):
                continue
            if relative in known_paths:
                continue
            suffix = Path(relative).suffix.lower()
            file_consumer_arg = command_name in _EXEC_FILE_CONSUMERS and index == 1
            if (
                explicit_relative
                or "/" in relative
                or suffix in _RESOURCE_LIKE_SUFFIXES
                or file_consumer_arg
            ):
                raise SkillRuntimeError(
                    "SKILL_RESOURCE_NOT_FOUND",
                    f"Command resource {relative!r} is not present in the active Skill manifests; "
                    "use workspace:<path> for an ordinary workspace file",
                )

        for path in sorted(candidates, key=len, reverse=True):
            for matched_skill, item in candidates[path]:
                uri_pattern = re.compile(
                    _EXEC_PATH_PREFIX + re.escape(item.uri) + _EXEC_PATH_SUFFIX
                )
                if not uri_pattern.search(rewritten):
                    continue
                local_root = await self.materialize(matched_skill)
                rewritten = uri_pattern.sub(f"{local_root}/{item.path}", rewritten)
                used.append(matched_skill.root_uri)

        for path in sorted(candidates, key=len, reverse=True):
            pattern = re.compile(
                _EXEC_PATH_PREFIX + r"(?:\./)?" + re.escape(path) + _EXEC_PATH_SUFFIX
            )
            path_match = pattern.search(rewritten)
            if not path_match:
                continue
            if len(candidates[path]) > 1:
                raise SkillRuntimeError(
                    "SKILL_RESOURCE_AMBIGUOUS",
                    f"Command resource {path!r} exists in multiple active Skills; use its canonical URI",
                )
            matched_skill, item = candidates[path][0]
            local_root = await self.materialize(matched_skill)
            replacement = f"{local_root}/{item.path}"
            rewritten = pattern.sub(replacement, rewritten)
            used.append(matched_skill.root_uri)
        return rewritten, tuple(dict.fromkeys(used))

    async def materialize(self, skill: ActiveRemoteSkill) -> str:
        existing = self.materializations.get(skill.root_uri)
        if existing is not None:
            return existing
        if self.sandbox_manager is None:
            raise SkillRuntimeError(
                "SKILL_CAPABILITY_UNAVAILABLE", "Remote Skill files require a sandbox manager"
            )

        lock = self._materialization_locks.setdefault(skill.root_uri, asyncio.Lock())
        async with lock:
            existing = self.materializations.get(skill.root_uri)
            if existing is not None:
                return existing
            sandbox = await self.sandbox_manager.get_sandbox(self.session_key)
            self._materialization_started = True
            await self._ensure_requirements(skill)
            root = self._materialized_root(skill)
            files = [
                item
                for item in skill.files.values()
                if not item.is_dir
                and item.path != "SKILL.md"
                and Path(item.path).name not in _SKILL_EXCLUDED_FILES
            ]
            if len(files) + 1 > self.settings.max_files:
                raise SkillRuntimeError(
                    "SKILL_PACKAGE_TOO_LARGE", "Remote Skill file count exceeds the limit"
                )

            definition = skill.content.encode("utf-8")
            if len(definition) > self.settings.max_file_bytes:
                raise SkillRuntimeError(
                    "SKILL_PACKAGE_TOO_LARGE", "Remote SKILL.md exceeds the file limit"
                )
            oversized = [
                item.path
                for item in files
                if item.size is not None and item.size > self.settings.max_file_bytes
            ]
            if oversized:
                raise SkillRuntimeError(
                    "SKILL_PACKAGE_TOO_LARGE",
                    f"Remote Skill file exceeds the limit: {oversized[0]}",
                )
            declared_total = len(definition) + sum(item.size or 0 for item in files)
            if declared_total > self.settings.max_total_bytes:
                raise SkillRuntimeError(
                    "SKILL_PACKAGE_TOO_LARGE", "Remote Skill bundle exceeds the limit"
                )

            if self._snapshot_cache is None:
                total = await self._materialize_uncached(
                    sandbox=sandbox,
                    root=root,
                    skill=skill,
                    files=files,
                    definition=definition,
                )
                cache_status = "disabled"
            else:
                try:
                    total, cache_status = await self._materialize_from_cache(
                        sandbox=sandbox,
                        root=root,
                        skill=skill,
                        files=files,
                        definition=definition,
                    )
                except RemoteSkillCacheError as exc:
                    logger.warning("Remote Skill cache bypassed for {}: {}", skill.root_uri, exc)
                    total = await self._materialize_uncached(
                        sandbox=sandbox,
                        root=root,
                        skill=skill,
                        files=files,
                        definition=definition,
                    )
                    cache_status = "bypassed"

            self.materializations[skill.root_uri] = root
            self.usage.append(
                {
                    "event": "skill.materialize.completed",
                    "skill_uri": skill.root_uri,
                    "path": root,
                    "bytes": total,
                    "cache": cache_status,
                }
            )
            logger.info(
                "Remote Skill materialized: request_id={} skill_uri={} bytes={} cache={}",
                self.request_id,
                skill.root_uri,
                total,
                cache_status,
            )
            return root

    async def _materialize_uncached(
        self,
        *,
        sandbox: Any,
        root: str,
        skill: ActiveRemoteSkill,
        files: list[RemoteSkillFile],
        definition: bytes,
    ) -> int:
        total = len(definition)
        try:
            await sandbox.write_file_bytes(f"{root}/SKILL.md", definition)
            client = await self._get_client()
            for item in files:
                data = await client.download_bytes(item.uri)
                self._validate_downloaded_file(item, data)
                total += len(data)
                if total > self.settings.max_total_bytes:
                    raise SkillRuntimeError(
                        "SKILL_PACKAGE_TOO_LARGE", "Remote Skill bundle exceeds the limit"
                    )
                await sandbox.write_file_bytes(f"{root}/{item.path}", data)
            await self._ensure_manifest_current(skill, client)
            return total
        except Exception:
            try:
                await sandbox.remove_tree(root)
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to clean incomplete remote Skill snapshot {}: {}",
                    root,
                    cleanup_exc,
                )
            raise

    async def _materialize_from_cache(
        self,
        *,
        sandbox: Any,
        root: str,
        skill: ActiveRemoteSkill,
        files: list[RemoteSkillFile],
        definition: bytes,
    ) -> tuple[int, str]:
        cache = self._snapshot_cache
        if cache is None:  # pragma: no cover - guarded by caller
            raise RuntimeError("Remote Skill snapshot cache is unavailable")
        key = self._cache_key(skill)

        async def build(snapshot_path: Path) -> SnapshotBuildResult:
            total = len(definition)
            await asyncio.to_thread(
                self._write_private_cache_file, snapshot_path / "SKILL.md", definition
            )
            client = await self._get_client()
            for item in files:
                data = await client.download_bytes(item.uri)
                self._validate_downloaded_file(item, data)
                total += len(data)
                if total > self.settings.max_total_bytes:
                    raise SkillRuntimeError(
                        "SKILL_PACKAGE_TOO_LARGE", "Remote Skill bundle exceeds the limit"
                    )
                await asyncio.to_thread(
                    self._write_private_cache_file,
                    snapshot_path / item.path,
                    data,
                )
            await self._ensure_manifest_current(skill, client)
            return SnapshotBuildResult(total_bytes=total, file_count=len(files) + 1)

        for attempt in range(2):
            corrupt = False
            try:
                async with cache.acquire(
                    key=key,
                    root_uri=skill.root_uri,
                    revision=skill.revision,
                    manifest_signature=skill.manifest_signature,
                    builder=build,
                ) as snapshot:
                    if snapshot.hit:
                        await self._ensure_manifest_current(skill)
                    total = await self._copy_cached_snapshot_to_sandbox(
                        snapshot_path=snapshot.path,
                        sandbox=sandbox,
                        root=root,
                        files=files,
                        definition=definition,
                    )
                    return total, "hit" if snapshot.hit else "miss"
            except _CachedSnapshotCorrupt:
                corrupt = True
            except Exception:
                try:
                    await sandbox.remove_tree(root)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to clean incomplete remote Skill snapshot {}: {}",
                        root,
                        cleanup_exc,
                    )
                raise
            finally:
                if corrupt:
                    try:
                        await sandbox.remove_tree(root)
                    except Exception as cleanup_exc:
                        logger.warning(
                            "Failed to clean corrupt remote Skill request snapshot {}: {}",
                            root,
                            cleanup_exc,
                        )

            await cache.invalidate(key)
            if attempt == 0:
                logger.warning("Discarded corrupt remote Skill cache entry for {}", skill.root_uri)
                continue
            raise SkillRuntimeError(
                "SKILL_CACHE_CORRUPT", "Remote Skill cache remained corrupt after rebuilding"
            )
        raise AssertionError("unreachable")

    async def _copy_cached_snapshot_to_sandbox(
        self,
        *,
        snapshot_path: Path,
        sandbox: Any,
        root: str,
        files: list[RemoteSkillFile],
        definition: bytes,
    ) -> int:
        expected: list[tuple[str, int, str]] = [
            ("SKILL.md", len(definition), hashlib.sha256(definition).hexdigest())
        ]
        expected.extend((item.path, int(item.size or 0), str(item.sha256 or "")) for item in files)
        total = 0
        for relative_path, expected_size, expected_sha256 in expected:
            try:
                data = await asyncio.to_thread((snapshot_path / relative_path).read_bytes)
            except OSError as exc:
                raise _CachedSnapshotCorrupt(relative_path) from exc
            if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha256:
                raise _CachedSnapshotCorrupt(relative_path)
            total += len(data)
            if total > self.settings.max_total_bytes:
                raise _CachedSnapshotCorrupt(relative_path)
            await sandbox.write_file_bytes(f"{root}/{relative_path}", data)
        return total

    async def _ensure_manifest_current(
        self,
        skill: ActiveRemoteSkill,
        client: VikingClient | None = None,
    ) -> None:
        client = client or await self._get_client()
        latest = await client.get_skill(
            skill.name,
            target_uri=_skill_identity_from_uri(skill.root_uri)[1],
            include_content=True,
            include_files=True,
            include_integrity=True,
        )
        if _manifest_signature(latest) != skill.manifest_signature:
            raise SkillRuntimeError(
                "SKILL_REVISION_CHANGED", "Remote Skill changed while it was materialized"
            )

    def _validate_downloaded_file(self, item: RemoteSkillFile, data: bytes) -> None:
        if len(data) > self.settings.max_file_bytes:
            raise SkillRuntimeError(
                "SKILL_PACKAGE_TOO_LARGE", f"Remote Skill file too large: {item.path}"
            )
        if item.size is not None and len(data) != item.size:
            raise SkillRuntimeError(
                "SKILL_REVISION_CHANGED", f"Remote Skill file size changed: {item.path}"
            )
        if item.sha256 and hashlib.sha256(data).hexdigest() != item.sha256:
            raise SkillRuntimeError(
                "SKILL_REVISION_CHANGED", f"Remote Skill checksum changed: {item.path}"
            )

    def _cache_key(self, skill: ActiveRemoteSkill) -> RemoteSkillCacheKey:
        connection = dict(self.openviking_connection or {})
        api_key = str(connection.pop("api_key", "") or "")
        stable_identity = bool(connection.get("user_id"))
        if api_key and not stable_identity:
            connection["api_key_sha256"] = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        scope_payload = {
            "workspace_id": self.workspace_id,
            "actor_peer_id": self.actor_peer_id,
            "server_url": connection.get("server_url")
            or str(getattr(self.config.ov_server, "server_url", "") or ""),
            "connection": connection,
        }
        encoded_scope = json.dumps(
            scope_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return RemoteSkillCacheKey(
            scope_digest=hashlib.sha256(encoded_scope).hexdigest(),
            root_digest=hashlib.sha256(skill.root_uri.encode("utf-8")).hexdigest(),
            revision_digest=hashlib.sha256(skill.revision.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _write_private_cache_file(path: Path, data: bytes) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(data)
            path.chmod(0o600)
        except OSError as exc:
            raise RemoteSkillCacheError(f"Remote Skill cache write failed: {exc}") from exc

    async def _check_requirements(self, skill: ActiveRemoteSkill, sandbox: Any) -> None:
        """Check declared capabilities in the sandbox that will execute the Skill."""
        checks: list[tuple[str, str]] = []
        for binary in skill.requires.get("bins", []):
            checks.append((f"bin:{binary}", f"command -v -- {shlex.quote(binary)} >/dev/null 2>&1"))
        for variable in skill.requires.get("env", []):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
                raise SkillRuntimeError(
                    "SKILL_INVALID", f"Invalid environment requirement name: {variable!r}"
                )
            checks.append((f"env:{variable}", f'test "${{{variable}+x}}" = x'))
        if not checks:
            return
        marker = "__VIKINGBOT_SKILL_REQUIREMENTS_OK__"
        missing: list[str] = []
        for label, check in checks:
            output = await sandbox.execute(f"{check} && printf {marker}", timeout=10)
            if marker not in str(output):
                missing.append(label)
        if missing:
            raise SkillRuntimeError(
                "SKILL_CAPABILITY_UNAVAILABLE",
                f"Execution sandbox is missing declared requirements: {missing}",
            )

    def _materialized_root(self, skill: ActiveRemoteSkill) -> str:
        digest = hashlib.sha256(skill.root_uri.encode("utf-8")).hexdigest()
        return (
            f"{_MATERIALIZED_SKILL_ROOT}/{self.request_id}/{digest}/{_safe_skill_name(skill.name)}"
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.sandbox_manager is not None and self._materialization_started:
            try:
                sandbox = await self.sandbox_manager.get_sandbox(self.session_key)
                await sandbox.remove_tree(f"{_MATERIALIZED_SKILL_ROOT}/{self.request_id}")
            except Exception as exc:
                logger.warning("Failed to clean remote Skill snapshot: {}", exc)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                logger.warning("Failed to close remote Skill OpenViking client: {}", exc)
            self._client = None

    def _active_skill_for_uri(self, uri: str) -> ActiveRemoteSkill | None:
        normalized = _normalize_uri(uri)
        for skill in self.active_skills.values():
            if normalized == skill.root_uri or normalized.startswith(skill.root_uri + "/"):
                return skill
        return None
