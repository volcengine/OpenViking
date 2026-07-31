# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Load train-domain policy set objects from existing memory files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking.core.skill_loader import SkillLoader
from openviking.server.identity import RequestContext
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train.domain import Policy, PolicySet, PolicyStatus
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer

_HIDDEN_MEMORY_FILES = {".overview.md", ".abstract.md"}
_ALLOWED_STATUSES = {"draft", "staging", "production", "deprecated", "archived"}


@dataclass(slots=True)
class ExperienceSetLoader:
    """Build an ExperienceSet by reading an experiences directory."""

    viking_fs: Any = None

    @tracer("train.experience_set_loader.load", ignore_result=True, ignore_args=True)
    async def load(self, root_uri: str, ctx: RequestContext | None = None) -> PolicySet:
        if ctx is None:
            raise ValueError("ExperienceSetLoader.load requires request_context ctx")
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to load an ExperienceSet")

        try:
            entries = await viking_fs.ls(root_uri, output="original", ctx=ctx)
        except Exception:
            entries = []

        policies: list[Policy] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("isDir") or entry.get("is_dir"):
                continue
            name = str(entry.get("name") or "")
            uri = str(entry.get("uri") or "")
            if not uri.endswith(".md") or name in _HIDDEN_MEMORY_FILES:
                continue
            if uri.endswith("/.overview.md") or uri.endswith("/.abstract.md"):
                continue

            raw = await viking_fs.read_file(uri, ctx=ctx) or ""
            mf = MemoryFileUtils.read(raw, uri=uri)
            fields = dict(mf.extra_fields or {})
            policy_name = str(fields.get("experience_name") or name.removesuffix(".md"))
            version = _safe_int(fields.get("version"), default=1)
            status = _safe_status(fields.get("status"))
            metadata = dict(fields)
            metadata.setdefault("memory_type", mf.memory_type or fields.get("memory_type"))
            policies.append(
                Policy(
                    name=policy_name,
                    uri=uri,
                    version=version,
                    status=status,
                    content=mf.plain_content(),
                    metadata=metadata,
                    links=list(mf.links or []),
                    backlinks=list(mf.backlinks or []),
                )
            )

        policies.sort(key=lambda p: p.uri)
        return PolicySet(
            root_uri=root_uri,
            policies=policies,
            metadata={"source": "memory_store"},
            viking_fs=viking_fs,
            request_context=ctx,
        )


def _safe_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_status(value: Any) -> PolicyStatus:
    status = str(value or "production")
    if status not in _ALLOWED_STATUSES:
        return "production"
    return status  # type: ignore[return-value]


@dataclass(slots=True)
class SkillSetLoader:
    """Build a PolicySet by reading a skills directory.

    Each skill is represented as a subdirectory containing a ``SKILL.md`` file
    with YAML frontmatter.  Skill-specific fields (description, allowed_tools,
    tags, …) are stored in the policy ``metadata`` dict.
    """

    viking_fs: Any = None

    @tracer("train.skill_set_loader.load", ignore_result=True, ignore_args=True)
    async def load(self, root_uri: str, ctx: RequestContext | None = None) -> PolicySet:
        if ctx is None:
            raise ValueError("SkillSetLoader.load requires request_context ctx")
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to load a SkillSet")

        try:
            entries = await viking_fs.ls(root_uri, output="original", ctx=ctx)
        except Exception:
            entries = []

        policies: list[Policy] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            if not (entry.get("isDir") or entry.get("is_dir")):
                continue
            dir_name = str(entry.get("name") or "")
            dir_uri = str(entry.get("uri") or "")
            if not dir_name or not dir_uri:
                continue
            if dir_name.startswith("."):
                continue

            skill_md_uri = f"{dir_uri.rstrip('/')}/SKILL.md"
            try:
                raw = await viking_fs.read_file(skill_md_uri, ctx=ctx)
            except Exception:
                continue
            if not raw:
                continue

            try:
                skill = SkillLoader.parse(raw, source_path=skill_md_uri)
            except Exception:
                # Fall back to generic memory-file parsing if SKILL.md
                # doesn't have valid frontmatter.
                mf = MemoryFileUtils.read(raw, uri=skill_md_uri)
                fields = dict(mf.extra_fields or {})
                skill_name = str(fields.get("skill_name") or dir_name)
                version = _safe_int(fields.get("version"), default=1)
                status = _safe_status(fields.get("status"))
                metadata = dict(fields)
                metadata.setdefault("memory_type", "skills")
                metadata["description"] = fields.get("description", "")
                policies.append(
                    Policy(
                        name=skill_name,
                        uri=skill_md_uri,
                        version=version,
                        status=status,
                        content=mf.plain_content(),
                        metadata=metadata,
                        links=list(mf.links or []),
                        backlinks=list(mf.backlinks or []),
                    )
                )
                continue

            version = 1
            status: PolicyStatus = "production"
            metadata: dict[str, Any] = {
                "memory_type": "skills",
                "description": skill.get("description", ""),
                "allowed_tools": list(skill.get("allowed_tools") or []),
                "tags": list(skill.get("tags") or []),
            }
            policies.append(
                Policy(
                    name=str(skill.get("name") or dir_name),
                    uri=skill_md_uri,
                    version=version,
                    status=status,
                    content=str(skill.get("content") or ""),
                    metadata=metadata,
                    links=[],
                    backlinks=[],
                )
            )

        policies.sort(key=lambda p: p.uri)
        return PolicySet(
            root_uri=root_uri,
            policies=policies,
            metadata={"source": "skill_store"},
            viking_fs=viking_fs,
            request_context=ctx,
        )
