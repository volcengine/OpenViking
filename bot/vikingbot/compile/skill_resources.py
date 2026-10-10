"""Permission-scoped, versioned Skill attachments for bounded Compile assignments."""

from __future__ import annotations

import asyncio
import json
import posixpath
import uuid

from openviking.core.namespace import relative_uri_path
from openviking.utils.path_safety import safe_join_viking_uri
from vikingbot.agent.tools.base import Tool
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import COMPILE_STAGING_ROOT
from vikingbot.compile.renderer import validate_relative_file_path


class SkillResources(Tool):
    """Read UTF-8 attachments through the task's authenticated client, inside one Skill.

    A task snapshots at most 32 attachments, 256 KiB each and 1 MiB total. Reads
    return complete requested line ranges with hashes and explicit remaining lines.
    Hashes bind execution caches and recovery to attachments read by execution stages.
    Complete snapshots are saved in the task sandbox with their Skill-relative layout
    so commands can use scripts and loaded dependencies through ordinary file paths.
    """

    name = "read_skill_resource"
    description = (
        "Read existing files provided with the Skill that defines this task. "
        "Path is relative to that Skill, or a full Viking URI inside it. "
        "Omit offset/limit to read the complete file. "
        "Line offsets are zero-based; use explicit non-overlapping ranges for long files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, client, root, files):
        self.client, self.root, self.files = client, root.rstrip("/"), files
        self.snapshots: dict[str, str] = {}
        self.hashes: dict[str, str] = {}
        self.lock = asyncio.Lock()
        self.local_root = posixpath.join(
            files.sandbox.sandbox_cwd, COMPILE_STAGING_ROOT, "skills", uuid.uuid4().hex
        )
        self.description += (
            f" Loaded attachments are saved under {self.local_root} with their Skill-relative "
            "paths; reads return local_path for the complete file in the sandbox."
        )

    def path(self, path):
        """Reject traversal, encoded traversal and namespace escape before any client read."""
        if path.startswith("viking://"):
            path = relative_uri_path(self.root, path)
            if not path:
                raise ValueError("Skill resource must be inside the selected Skill")
        path = validate_relative_file_path(path)
        safe_join_viking_uri(self.root, path)
        return path

    async def read(self, path, *, refresh=False):
        """Load a bounded attachment and persist its content version; errors never mean absence."""
        path = self.path(path)
        async with self.lock:
            if path not in self.snapshots or refresh:
                if path not in self.snapshots and len(self.snapshots) >= 32:
                    raise ValueError("Skill dependency limit exceeds 32 files")
                uri = safe_join_viking_uri(self.root, path)
                stat = await self.client.stat(uri)
                if stat.get("isDir") or int(stat.get("size") or 0) > 256 * 1024:
                    raise ValueError("Skill resource must be a file of at most 256 KiB")
                raw = await self.client.download_bytes(uri)
                if len(raw) > 256 * 1024:
                    raise ValueError("Skill resource exceeds 256 KiB")
                size = sum(len(v.encode()) for k, v in self.snapshots.items() if k != path)
                if size + len(raw) > 1024 * 1024:
                    raise ValueError("Skill dependencies exceed 1 MiB")
                content = raw.decode("utf-8")
                await self.files.sandbox.write_file(posixpath.join(self.local_root, path), content)
                self.snapshots[path] = content
                self.hashes[path] = content_hash(raw)
                await self.files.put("skill-dependencies", self.hashes)
            return self.snapshots[path]

    async def valid(self, dependencies):
        """Revalidate cache dependencies against authorized current content before reuse."""
        for path, expected in dependencies.items():
            await self.read(path, refresh=True)
            if self.hashes[path] != expected:
                return False
        return True

    async def execute(self, tool_context=None, path="", offset=0, limit=None):
        """Return the requested lines, never a silently truncated rule or template."""
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("Invalid Skill line range")
        path = self.path(path)
        content = await self.read(path)
        lines = content.splitlines(keepends=True)
        end = len(lines) if limit is None else min(len(lines), offset + limit)
        return json.dumps(
            {
                "path": path,
                "local_path": posixpath.join(self.local_root, path),
                "sha256": self.hashes[path],
                "offset": offset,
                "end": end,
                "total_lines": len(lines),
                "complete": offset == 0 and end == len(lines),
                "text": "".join(lines[offset:end]),
            },
            ensure_ascii=False,
        )


class EvidenceReader(Tool):
    """Read only original source ranges assigned to this operator, never a source catalog."""

    name = "read_evidence"
    description = (
        "Read assigned original evidence. Optional start_line/end_line are one-based, inclusive "
        "lines within the source_range shard; omit both for the complete shard."
    )
    parameters = {
        "type": "object",
        "properties": {
            "source_range": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        "required": ["source_range"],
        "additionalProperties": False,
    }

    def __init__(self, files, data):
        self.files = files
        self.allowed = set()
        self.spans, full = {}, set()
        self.delivered = (
            {
                ref
                for ref, value in data.get("original_evidence", {}).items()
                if value.get("complete", True)
            }
            if isinstance(data, dict)
            else set()
        )
        for item in data.get("inputs", []) if isinstance(data, dict) else []:
            payload = item.get("payload", {})
            if "text" in payload and "uri" in payload:
                self.allowed.add(item["id"])
                self.delivered.add(item["id"])
            self.allowed.update(payload.get("source_ranges", []))
            spans = payload.get("evidence_spans", [])
            full.update(set(payload.get("source_ranges", [])) - {s["source_range"] for s in spans})
            for span in spans:
                self.spans.setdefault(span["source_range"], []).append(
                    (span["start_line"], span["end_line"])
                )
        # A record without locations still needs full evidence, even if a neighbour has hints.
        for reference in full:
            self.spans.pop(reference, None)

    async def read(self, source_range, spans=()):
        """Return exact original excerpts with shard metadata, or the full shard without bounds.

        Reject unauthorized IDs and invalid shard-local lines; merge overlapping/adjacent
        spans. Partial results retain context and explicitly keep full-shard reads available.
        """
        if source_range not in self.allowed:
            raise ValueError("Evidence is outside this assignment")
        value = await self.files.get(f"sources/{source_range}")
        if value is None:
            raise ValueError("Assigned source range is missing")
        lines = value["text"].splitlines(keepends=True)
        merged = []
        for start, end in sorted(spans):
            if (
                type(start) is not int
                or type(end) is not int
                or not 1 <= start <= end <= len(lines)
            ):
                raise ValueError("Evidence lines are outside the original source range")
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        if not merged or merged == [(1, len(lines))]:
            return {**value, "complete": True, "line_count": len(lines)}
        return {
            **{k: v for k, v in value.items() if k != "text"},
            "complete": False,
            "line_count": len(lines),
            "excerpts": [
                {"start_line": start, "end_line": end, "text": "".join(lines[start - 1 : end])}
                for start, end in merged
            ],
        }

    async def execute(self, tool_context=None, source_range="", start_line=None, end_line=None):
        """Read both inclusive line bounds or omit both for full evidence; return JSON."""
        if (start_line is None) != (end_line is None):
            raise ValueError("Supply both start_line and end_line, or neither")
        spans = [] if start_line is None else [(start_line, end_line)]
        return json.dumps(await self.read(source_range, spans), ensure_ascii=False)
