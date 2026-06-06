# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Load train-domain ExperienceSet objects from existing memory files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking.server.identity import RequestContext
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train.domain import Experience, ExperienceSet, PolicyStatus
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer

_HIDDEN_MEMORY_FILES = {".overview.md", ".abstract.md"}
_ALLOWED_STATUSES = {"draft", "staging", "production", "deprecated", "archived"}


@dataclass(slots=True)
class ExperienceSetLoader:
    """Build an ExperienceSet by reading an experiences directory."""

    viking_fs: Any = None

    @tracer("train.experience_set_loader.load", ignore_result=True, ignore_args=True)
    async def load(self, root_uri: str, ctx: RequestContext | None = None) -> ExperienceSet:
        if ctx is None:
            raise ValueError("ExperienceSetLoader.load requires request_context ctx")
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to load an ExperienceSet")

        try:
            entries = await viking_fs.ls(root_uri, output="original", ctx=ctx)
        except Exception:
            entries = []

        policies: list[Experience] = []
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
            experience_name = str(fields.get("experience_name") or name.removesuffix(".md"))
            version = _safe_int(fields.get("version"), default=1)
            status = _safe_status(fields.get("status"))
            metadata = dict(fields)
            metadata.setdefault("memory_type", mf.memory_type or fields.get("memory_type"))
            policies.append(
                Experience(
                    name=experience_name,
                    uri=uri,
                    version=version,
                    status=status,
                    content=mf.plain_content(),
                    metadata=metadata,
                )
            )

        policies.sort(key=lambda p: p.uri)
        return ExperienceSet(
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
