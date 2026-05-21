# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Storage helpers for externalized session tool results."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_id(value: str, *, fallback: str = "tool") -> str:
    safe = _SAFE_ID_RE.sub("_", value or "").strip("._-")
    return (safe or fallback)[:96]


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_tool_result_id(tool_id: str, sha256: str) -> str:
    if tool_id:
        return f"tr_{_safe_id(tool_id)}_{sha256[:16]}"
    return f"tr_{sha256[:24]}"


def make_preview(
    content: str,
    *,
    preview_chars: int,
    ref: str = "",
    tool_name: str = "",
    sha256: str = "",
    reason: str = "",
    original_chars: Optional[int] = None,
) -> str:
    """Build a deterministic head+tail preview."""
    original = len(content) if original_chars is None else original_chars
    if preview_chars <= 0 or len(content) <= preview_chars:
        body = content
    else:
        half = max(1, preview_chars // 2)
        body = (
            "--- BEGIN PREVIEW HEAD ---\n"
            f"{content[:half]}\n"
            "--- END PREVIEW HEAD ---\n\n"
            "--- BEGIN PREVIEW TAIL ---\n"
            f"{content[-half:]}\n"
            "--- END PREVIEW TAIL ---"
        )

    header = [
        "[OpenViking tool result externalized]",
        f"tool_name: {tool_name or 'tool'}",
        f"original_chars: {original}",
        f"preview_chars: {min(len(content), max(preview_chars, 0))}",
    ]
    if ref:
        header.append(f"ref: {ref}")
    if sha256:
        header.append(f"sha256: {sha256[:16]}")
    if reason:
        header.append(f"reason: {reason}")
    return "\n".join(header) + "\n\n" + body


@dataclass
class StoredToolResult:
    tool_result_id: str
    storage_uri: str
    output_uri: str
    metadata_uri: str
    metadata: Dict[str, Any]


class ToolResultStore:
    """Persist raw tool outputs outside session messages."""

    def __init__(self, viking_fs: VikingFS, session_uri: str, session_id: str, ctx: Any):
        self._viking_fs = viking_fs
        self._session_uri = session_uri
        self._session_id = session_id
        self._ctx = ctx

    def _base_uri(self) -> str:
        return f"{self._session_uri}/tool-results"

    def _result_uri(self, tool_result_id: str) -> str:
        self._validate_tool_result_id(tool_result_id)
        return f"{self._base_uri()}/{tool_result_id}"

    @staticmethod
    def _validate_tool_result_id(tool_result_id: str) -> None:
        if not tool_result_id or _SAFE_ID_RE.search(tool_result_id) or "/" in tool_result_id:
            raise InvalidArgumentError(
                "Invalid tool_result_id",
                details={"tool_result_id": tool_result_id},
            )

    async def write(
        self,
        *,
        content: str,
        tool_id: str,
        tool_name: str,
        message_id: str,
        agent_id: Optional[str],
        created_at: Optional[str],
        preview_chars: int,
        mime_type: str = "text/plain",
    ) -> StoredToolResult:
        digest = sha256_text(content)
        tool_result_id = build_tool_result_id(tool_id, digest)
        storage_uri = self._result_uri(tool_result_id)
        output_uri = f"{storage_uri}/output.txt"
        metadata_uri = f"{storage_uri}/metadata.json"

        try:
            existing_metadata = await self.read_metadata(tool_result_id)
            if existing_metadata.get("sha256") == digest:
                return StoredToolResult(
                    tool_result_id=tool_result_id,
                    storage_uri=storage_uri,
                    output_uri=output_uri,
                    metadata_uri=metadata_uri,
                    metadata=existing_metadata,
                )
        except NotFoundError:
            pass

        metadata = {
            "tool_result_id": tool_result_id,
            "session_id": self._session_id,
            "message_id": message_id,
            "tool_id": tool_id,
            "tool_name": tool_name,
            "agent_id": agent_id,
            "created_at": created_at,
            "original_chars": len(content),
            "preview_chars": min(len(content), max(preview_chars, 0)),
            "sha256": digest,
            "mime_type": mime_type,
            "storage_uri": storage_uri,
            "output_uri": output_uri,
            "offset_unit": "unicode_code_point",
        }
        await self._viking_fs.write_file(output_uri, content, ctx=self._ctx)
        await self._viking_fs.write_file(
            metadata_uri,
            json.dumps(metadata, ensure_ascii=False, indent=2),
            ctx=self._ctx,
        )
        return StoredToolResult(
            tool_result_id=tool_result_id,
            storage_uri=storage_uri,
            output_uri=output_uri,
            metadata_uri=metadata_uri,
            metadata=metadata,
        )

    async def read_metadata(self, tool_result_id: str) -> Dict[str, Any]:
        metadata_uri = f"{self._result_uri(tool_result_id)}/metadata.json"
        try:
            raw = await self._viking_fs.read_file(metadata_uri, ctx=self._ctx)
        except NotFoundError:
            raise
        except Exception as exc:
            raise NotFoundError(tool_result_id, "tool result") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidArgumentError(
                "Invalid tool result metadata",
                details={"tool_result_id": tool_result_id},
            ) from exc

    async def read(
        self,
        tool_result_id: str,
        *,
        offset: int = 0,
        limit: int = 20_000,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        if offset < 0:
            raise InvalidArgumentError("offset must be greater than or equal to 0")
        if limit < -1:
            raise InvalidArgumentError("limit must be -1 or greater than or equal to 0")

        metadata = await self.read_metadata(tool_result_id)
        content = await self._viking_fs.read_file(
            f"{self._result_uri(tool_result_id)}/output.txt",
            ctx=self._ctx,
        )
        end = None if limit == -1 else offset + limit
        chunk = content[offset:end]
        total_chars = len(content)
        has_more = end is not None and end < total_chars
        result = {
            "tool_result_id": tool_result_id,
            "content": chunk,
            "offset": offset,
            "limit": limit,
            "offset_unit": "unicode_code_point",
            "total_chars": total_chars,
            "has_more": has_more,
        }
        if include_metadata:
            result["metadata"] = metadata
        return result

    async def search(
        self,
        tool_result_id: str,
        *,
        query: str,
        limit: int = 20,
        context_chars: int = 300,
    ) -> Dict[str, Any]:
        if not query:
            raise InvalidArgumentError("query must not be empty")
        if limit <= 0:
            raise InvalidArgumentError("limit must be greater than 0")

        content = await self._viking_fs.read_file(
            f"{self._result_uri(tool_result_id)}/output.txt",
            ctx=self._ctx,
        )
        matches = []
        start = 0
        while len(matches) < limit:
            idx = content.find(query, start)
            if idx < 0:
                break
            left = max(0, idx - context_chars)
            right = min(len(content), idx + len(query) + context_chars)
            matches.append(
                {
                    "offset": idx,
                    "offset_unit": "unicode_code_point",
                    "snippet": content[left:right],
                }
            )
            start = idx + max(1, len(query))
        return {"tool_result_id": tool_result_id, "matches": matches}

    async def list(self, *, tool_name: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        if limit <= 0:
            raise InvalidArgumentError("limit must be greater than 0")
        node_limit = max(limit, 100_000) if tool_name else limit
        try:
            entries = await self._viking_fs.ls(
                self._base_uri(),
                output="original",
                node_limit=node_limit,
                ctx=self._ctx,
            )
        except NotFoundError:
            entries = []

        results: List[Dict[str, Any]] = []
        for entry in entries:
            if not entry.get("isDir"):
                continue
            tool_result_id = entry.get("name", "")
            try:
                metadata = await self.read_metadata(tool_result_id)
            except Exception:
                continue
            if tool_name and metadata.get("tool_name") != tool_name:
                continue
            results.append(metadata)
            if len(results) >= limit:
                break
        return {"tool_results": results}
