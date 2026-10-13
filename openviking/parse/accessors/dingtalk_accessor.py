# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DingTalk Docs accessor backed by the configured read-only MCP services."""

import asyncio
import csv
import hashlib
import io
import json
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Union
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from openviking.utils.network_guard import build_httpx_request_validation_hooks
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError
from openviking_cli.utils.config.dingtalk_config import DingTalkConfig, DingTalkIdentityConfig

from .base import DataAccessor, LocalResource, SourceType
from .dingtalk_client import DingTalkClient, DingTalkMCPError, sensitive_download_logs
from .mime_types import get_preferred_extension

_DINGTALK_HOSTS = frozenset({"alidocs.dingtalk.com", "docs.dingtalk.com"})
_NODE_PATH_RE = re.compile(r"^/i/nodes/([A-Za-z0-9]{16,64})/?$")
_SPACE_PATH_RE = re.compile(r"^/i/spaces/([A-Za-z0-9]{8,64})(?:/overview)?/?$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(((?:[^()]|\([^()]*\))+?)\)")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(((?:[^()]|\([^()]*\))+?)\)")
_REMOTE_ASSET_RE = re.compile(
    r"https://[^\s)\"'<>]*\.(?:aliyuncs\.com|alicdn\.com)/[^\s)\"'<>]+",
    re.IGNORECASE,
)
_ATTACHMENT_RESOURCE_KEYS = frozenset({"resourceId", "resourceID", "resource_id"})
_MAX_REDIRECTS = 3


class DingTalkImportError(OpenVikingError):
    pass


@dataclass
class _Limits:
    max_nodes: int
    max_depth: int
    max_bytes: int
    nodes: int = 0
    bytes: int = 0

    def add_node(self) -> None:
        if self.nodes >= self.max_nodes:
            raise DingTalkImportError(
                f"DingTalk import exceeded the node limit ({self.max_nodes})",
                code="RESOURCE_EXHAUSTED",
            )
        self.nodes += 1

    def add_bytes(self, size: int) -> None:
        if size < 0 or self.bytes + size > self.max_bytes:
            raise DingTalkImportError(
                f"DingTalk import exceeded the byte limit ({self.max_bytes})",
                code="RESOURCE_EXHAUSTED",
            )
        self.bytes += size


@dataclass
class _State:
    root: Path
    source_url: str
    limits: _Limits
    request_validator: Callable[[str], None] | None
    available_services: frozenset[str]
    previous_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_node_id: str = ""
    processed: set[str] = field(default_factory=set)
    manifest: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(
        default_factory=lambda: {
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "unsupported": 0,
            "reused": 0,
            "success_nodes": [],
            "failed_nodes": [],
            "skipped_nodes": [],
            "unsupported_nodes": [],
            "reused_nodes": [],
            "metadata_reused_nodes": [],
            "content_reused_nodes": [],
            "unreadable_reused_nodes": [],
            "unreadable_nodes": [],
        }
    )


def _payload_body(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("result", "data"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            return nested
    return payload


def _required_items(payload: Mapping[str, Any], operation: str, *keys: str) -> list[Any]:
    body = _payload_body(payload)
    for key in (*keys, "items"):
        if key not in body:
            continue
        value = body[key]
        if isinstance(value, list):
            return value
        raise DingTalkImportError(f"DingTalk {operation} returned an invalid list")
    raise DingTalkImportError(f"DingTalk {operation} did not return its expected list")


def _first(payload: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    body = _payload_body(payload)
    for key in keys:
        if key in body and body[key] is not None:
            return body[key]
    return default


def _canonical_source_url(value: str, node_id: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").rstrip(".")
    if parsed.scheme == "https" and host in _DINGTALK_HOSTS:
        return urlunparse(("https", host, parsed.path, "", "", ""))
    return f"https://alidocs.dingtalk.com/i/nodes/{node_id}"


def _safe_id(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extension(value: Any, content_type: str | None = None) -> str:
    raw = str(value or "").strip().lower().lstrip(".")
    if raw and re.fullmatch(r"[a-z0-9]{1,12}", raw):
        return f".{raw}"
    if content_type:
        preferred = get_preferred_extension(content_type.split(";", 1)[0].strip())
        if preferred:
            return preferred
    return ".bin"


def _node_id(info: Mapping[str, Any]) -> str:
    value = info.get("nodeId") or info.get("id") or info.get("dentryUuid")
    if not isinstance(value, str) or not value:
        raise DingTalkImportError("DingTalk returned a node without a stable node ID")
    return value


def _markdown_header(info: Mapping[str, Any], source_url: str) -> str:
    title = str(info.get("name") or info.get("title") or "Untitled")
    node_id = _node_id(info)
    updated = info.get("updateTime") or info.get("modifiedTime")
    lines = [f"# {title}", "", f"- DingTalk node ID: `{node_id}`", f"- Source: {source_url}"]
    if updated is not None:
        lines.append(f"- DingTalk updated: {updated}")
    return "\n".join(lines) + "\n\n"


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def _column_number(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{1,5}", value):
        raise DingTalkImportError("DingTalk returned an invalid worksheet column")
    number = 0
    for char in value:
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _find_values(value: Any, keys: frozenset[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item:
                found.append(item)
            found.extend(_find_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_values(item, keys))
    return list(dict.fromkeys(found))


def _source_marker(info: Mapping[str, Any]) -> dict[str, Any] | None:
    for key in ("version", "versionId", "revision", "revisionId"):
        value = info.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and value != "":
            return {"field": key, "value": value}
    for key in ("updateTime", "modifiedTime"):
        value = info.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return {"field": key, "value": value}
    return None


def _previous_nodes(value: Any, processing_key: Any) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(value, Mapping)
        or value.get("complete") is not True
        or not isinstance(processing_key, str)
        or not processing_key
        or value.get("processing_key") != processing_key
        or not isinstance(value.get("manifest"), list)
    ):
        return {}
    return {
        item["node_id"]: dict(item)
        for item in value["manifest"]
        if isinstance(item, Mapping)
        and isinstance(item.get("node_id"), str)
        and isinstance(item.get("content_sha256"), str)
    }


class DingTalkAccessor(DataAccessor):
    """Materialize DingTalk nodes into a stable local directory tree."""

    def __init__(
        self,
        config: DingTalkConfig | None = None,
        client_factory: Callable[[DingTalkIdentityConfig], Any] = DingTalkClient,
    ) -> None:
        self._config = config
        self._client_factory = client_factory

    @property
    def priority(self) -> int:
        return 110

    def can_handle(self, source: Union[str, Path], **kwargs: Any) -> bool:
        if isinstance(source, Path):
            return False
        parsed = urlparse(str(source))
        return (
            parsed.scheme.lower() in {"http", "https"}
            and (parsed.hostname or "").rstrip(".") in _DINGTALK_HOSTS
        )

    def _config_value(self) -> DingTalkConfig:
        if self._config is None:
            from openviking_cli.utils.config import get_openviking_config

            self._config = get_openviking_config().dingtalk
        return self._config

    @staticmethod
    def _parse_source(source: str) -> tuple[str, str, str]:
        parsed = urlparse(source)
        host = (parsed.hostname or "").rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise InvalidArgumentError("DingTalk source URL has an invalid port") from exc
        if (
            parsed.scheme != "https"
            or host not in _DINGTALK_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
        ):
            raise InvalidArgumentError("DingTalk source must be an HTTPS DingTalk Docs URL")
        node = _NODE_PATH_RE.fullmatch(parsed.path)
        if node:
            return "node", node.group(1), _canonical_source_url(source, node.group(1))
        space = _SPACE_PATH_RE.fullmatch(parsed.path)
        if space:
            canonical = f"https://{host}/i/spaces/{space.group(1)}/overview"
            return "workspace", space.group(1), canonical
        raise InvalidArgumentError(
            "Unsupported DingTalk URL path; expected /i/nodes/<nodeId> or "
            "/i/spaces/<workspaceId>/overview"
        )

    async def access(self, source: Union[str, Path], **kwargs: Any) -> LocalResource:
        source_kind, source_id, source_url = self._parse_source(str(source))
        identity_name = kwargs.pop("dingtalk_identity", None)
        max_nodes = kwargs.pop("dingtalk_max_nodes", 1000)
        max_depth = kwargs.pop("dingtalk_max_depth", 20)
        max_bytes = kwargs.pop("dingtalk_max_bytes", 512 * 1024 * 1024)
        previous_state = kwargs.pop("dingtalk_previous_state", None)
        processing_key = kwargs.pop("dingtalk_processing_key", None)
        previous_digest = kwargs.pop("dingtalk_previous_digest", None)
        request_validator = kwargs.pop("request_validator", None)
        unknown_dingtalk = sorted(key for key in kwargs if key.startswith("dingtalk_"))
        if unknown_dingtalk:
            raise InvalidArgumentError(
                f"Unsupported DingTalk import options: {', '.join(unknown_dingtalk)}"
            )
        if not isinstance(identity_name, str) or not identity_name:
            raise InvalidArgumentError("dingtalk_identity is required")
        identity = self._config_value().identities.get(identity_name)
        if identity is None:
            raise InvalidArgumentError(f"Unknown DingTalk identity: {identity_name}")
        if (
            not isinstance(max_nodes, int)
            or isinstance(max_nodes, bool)
            or max_nodes <= 0
            or not isinstance(max_depth, int)
            or isinstance(max_depth, bool)
            or max_depth < 0
            or not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes <= 0
        ):
            raise InvalidArgumentError("DingTalk limits must be positive integers (depth may be 0)")

        temp_root = Path(tempfile.mkdtemp(prefix="ov_dingtalk_"))
        output_root = temp_root / f"dingtalk_{_safe_id(source_id)}"
        output_root.mkdir()
        state = _State(
            root=output_root,
            source_url=source_url,
            limits=_Limits(max_nodes=max_nodes, max_depth=max_depth, max_bytes=max_bytes),
            request_validator=request_validator,
            available_services=frozenset(
                name
                for name in ("doc", "sheets", "ai_table")
                if name == "doc" or getattr(identity, name) is not None
            ),
            previous_by_id=_previous_nodes(previous_state, processing_key),
        )
        run_id = str(uuid.uuid4())
        client = self._client_factory(identity)
        try:
            if source_kind == "workspace":
                state.limits.add_node()
                state.manifest.append(
                    {
                        "node_id": source_id,
                        "relative_path": ".",
                        "type": "workspace",
                        "title": source_id,
                        "source_url": source_url,
                        "updated_at": None,
                    }
                )
                await self._walk_folder(
                    client,
                    state,
                    output_root,
                    depth=0,
                    ancestors=frozenset(),
                    workspace_id=source_id,
                )
                state.report["success"] += 1
                state.report["success_nodes"].append(source_id)
            else:
                await self._process_node(
                    client,
                    state,
                    source_id,
                    output_root,
                    depth=0,
                    ancestors=frozenset(),
                )
        except BaseException as exc:
            state.report["failed"] += 1
            state.report["failed_nodes"].append(state.current_node_id or source_id)
            details = {
                "dingtalk_report": dict(state.report),
                "dingtalk_limits": {
                    "max_nodes": max_nodes,
                    "max_depth": max_depth,
                    "max_bytes": max_bytes,
                    "nodes": state.limits.nodes,
                    "bytes": state.limits.bytes,
                },
            }
            shutil.rmtree(temp_root, ignore_errors=True)
            if not isinstance(exc, Exception):
                raise
            if isinstance(exc, (InvalidArgumentError, OpenVikingError)):
                if isinstance(exc, OpenVikingError):
                    for key, value in details.items():
                        exc.details.setdefault(key, value)
                raise
            if isinstance(exc, DingTalkMCPError) and exc.permission_denied:
                raise DingTalkImportError(
                    "DingTalk import was denied by the selected identity",
                    code="PERMISSION_DENIED",
                    details=details,
                ) from None
            if isinstance(exc, DingTalkMCPError):
                details["dingtalk_failure"] = {
                    "node_id": state.current_node_id or source_id,
                    "operation": exc.operation,
                    "reason_code": exc.reason_code,
                    "retryable": exc.retryable,
                }
                reason = f" ({exc.reason_code})" if exc.reason_code else ""
                raise DingTalkImportError(
                    f"DingTalk {exc.operation} failed{reason}; no partial content was kept",
                    code="UNAVAILABLE",
                    details=details,
                ) from None
            raise DingTalkImportError(
                "DingTalk import failed; no partial content was kept",
                code="UNAVAILABLE",
                details=details,
            ) from None

        limitations = [
            "DingTalk attachment discovery is limited to top-level attachment blocks; "
            "imports fail if a remote DingTalk asset reference remains."
        ]
        unreadable_count = len(state.report["unreadable_nodes"])
        if unreadable_count:
            limitations.append(
                f"Skipped {unreadable_count} unreadable DingTalk file(s); "
                "they will be retried on the next sync."
            )

        return LocalResource(
            path=output_root,
            source_type=SourceType.DINGTALK,
            original_source=source_url,
            meta={
                "_cleanup_path": str(temp_root),
                "dingtalk_identity": identity_name,
                "dingtalk_run_id": run_id,
                "dingtalk_processing_key": processing_key,
                "dingtalk_previous_digest": previous_digest,
                "dingtalk_manifest": state.manifest,
                "dingtalk_report": state.report,
                "dingtalk_limits": {
                    "max_nodes": max_nodes,
                    "max_depth": max_depth,
                    "max_bytes": max_bytes,
                    "nodes": state.limits.nodes,
                    "bytes": state.limits.bytes,
                },
                "dingtalk_limitations": limitations,
            },
            is_temporary=True,
        )

    async def _process_node(
        self,
        client: Any,
        state: _State,
        node_ref: str,
        parent: Path,
        *,
        depth: int,
        ancestors: frozenset[str],
    ) -> None:
        state.current_node_id = node_ref
        if depth > state.limits.max_depth:
            raise DingTalkImportError(
                f"DingTalk import exceeded the depth limit ({state.limits.max_depth})",
                code="RESOURCE_EXHAUSTED",
            )
        state.limits.add_node()
        info = _payload_body(await client.call("doc", "get_document_info", {"nodeId": node_ref}))
        info = await self._resolve_shortcut(client, state, info, ancestors)
        node_id = _node_id(info)
        state.current_node_id = node_id
        if node_id in ancestors:
            raise DingTalkImportError("DingTalk shortcut or folder cycle detected")
        if node_id in state.processed:
            state.report["skipped"] += 1
            state.report["skipped_nodes"].append(node_id)
            return
        state.processed.add(node_id)
        node_ancestors = ancestors | {node_id}
        node_type = str(info.get("nodeType") or "").lower()
        extension = str(info.get("extension") or "").lower().lstrip(".")
        source_url = _canonical_source_url(str(info.get("docUrl") or ""), node_id)

        if node_type == "folder":
            folder = parent / _safe_id(node_id)
            folder.mkdir(exist_ok=True)
            state.manifest.append(self._manifest_entry(state, info, source_url, folder, "folder"))
            await self._walk_folder(
                client,
                state,
                folder,
                depth=depth,
                ancestors=node_ancestors,
                folder_id=node_id,
            )
            state.report["success"] += 1
            state.report["success_nodes"].append(node_id)
            return
        if extension == "adoc":
            final_target = parent / f"{_safe_id(node_id)}.md"
            kind = "document"
        elif extension == "axls":
            if "sheets" not in state.available_services:
                raise DingTalkImportError(
                    "The selected DingTalk identity has no Sheets MCP endpoint",
                    code="FAILED_PRECONDITION",
                )
            final_target = parent / f"{_safe_id(node_id)}.md"
            kind = "sheet"
        elif extension == "able":
            if "ai_table" not in state.available_services:
                raise DingTalkImportError(
                    "The selected DingTalk identity has no AI Table MCP endpoint",
                    code="FAILED_PRECONDITION",
                )
            final_target = parent / f"{_safe_id(node_id)}.md"
            kind = "ai_table"
        elif node_type == "file" and str(info.get("contentType") or "").upper() != "ALIDOC":
            final_target = parent / f"{_safe_id(node_id)}{_extension(extension)}"
            kind = "file"
        else:
            state.report["unsupported"] += 1
            state.report["unsupported_nodes"].append(node_id)
            raise DingTalkImportError(
                f"Unsupported DingTalk node type (nodeType={node_type or 'unknown'}, "
                f"extension={extension or 'unknown'})",
                code="FAILED_PRECONDITION",
            )

        previous = state.previous_by_id.get(node_id)
        entry = self._manifest_entry(state, info, source_url, final_target, kind)
        if previous is not None and self._can_reuse_metadata(previous, entry):
            self._record_reuse(state, entry, previous, "metadata")
            return

        scratch = state.root.parent / ".dingtalk_materialized" / _safe_id(node_id)
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        target = scratch / final_target.name
        try:
            if kind == "document":
                await self._write_document(client, state, info, source_url, target)
            elif kind == "sheet":
                await self._write_sheet(client, state, info, source_url, target)
            elif kind == "ai_table":
                await self._write_ai_table(client, state, info, source_url, target)
            else:
                await self._write_cloud_file(client, state, info, target)
        except (DingTalkMCPError, OpenVikingError) as exc:
            safe_codes = {"UNKNOWN", "UNAVAILABLE", "PERMISSION_DENIED", "NOT_FOUND"}
            if depth == 0 or (not isinstance(exc, DingTalkMCPError) and exc.code not in safe_codes):
                raise
            shutil.rmtree(scratch, ignore_errors=True)
            operation = exc.operation if isinstance(exc, DingTalkMCPError) else f"{kind}.read"
            reason_code = exc.reason_code if isinstance(exc, DingTalkMCPError) else exc.code
            state.report["skipped"] += 1
            state.report["skipped_nodes"].append(node_id)
            state.report["unreadable_nodes"].append(
                {
                    "node_id": node_id,
                    "operation": operation,
                    "reason_code": reason_code,
                }
            )
            entry.update({"unreadable": True, "requires_content_check": True})
            if previous is not None:
                self._record_reuse(state, entry, previous, "unreadable")
            else:
                entry["reused"] = False
                state.manifest.append(entry)
            return

        entry["content_sha256"] = self._tree_digest(scratch, target)
        scratch_files = sorted(path for path in scratch.rglob("*") if path.is_file())
        entry["source_paths"] = [
            (final_target.parent / path.relative_to(scratch)).relative_to(state.root).as_posix()
            for path in scratch_files
        ]
        entry["requires_content_check"] = kind != "document" or len(scratch_files) > 1
        if (
            previous is not None
            and previous.get("relative_path") == entry["relative_path"]
            and previous.get("type") == entry["type"]
            and previous.get("content_sha256") == entry["content_sha256"]
        ):
            shutil.rmtree(scratch, ignore_errors=True)
            self._record_reuse(state, entry, previous, "content")
            return

        self._merge_materialized(scratch, final_target.parent)
        entry["reused"] = False
        state.manifest.append(entry)
        state.report["success"] += 1
        state.report["success_nodes"].append(node_id)
        state.report.setdefault("changed_nodes", []).append(node_id)

    async def _resolve_shortcut(
        self,
        client: Any,
        state: _State,
        initial: Mapping[str, Any],
        ancestors: frozenset[str],
    ) -> Mapping[str, Any]:
        info = initial
        chain: set[str] = set()
        while isinstance(info.get("linkSourceInfo"), Mapping):
            target = info["linkSourceInfo"].get("nodeId") or info["linkSourceInfo"].get("id")
            if not isinstance(target, str) or not target:
                raise DingTalkImportError("DingTalk shortcut is missing its target node ID")
            if target in chain or target in ancestors:
                raise DingTalkImportError("DingTalk shortcut cycle detected")
            chain.add(target)
            state.limits.add_node()
            state.current_node_id = target
            info = _payload_body(await client.call("doc", "get_document_info", {"nodeId": target}))
        return info

    async def _walk_folder(
        self,
        client: Any,
        state: _State,
        parent: Path,
        *,
        depth: int,
        ancestors: frozenset[str],
        folder_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        cursor: str | None = None
        cursors: set[str] = set()
        pages = 0
        while True:
            pages += 1
            if pages > state.limits.max_nodes + 1:
                raise DingTalkImportError("DingTalk folder pagination exceeded its safe limit")
            arguments: dict[str, Any] = {"pageSize": 50}
            if folder_id:
                arguments["folderId"] = folder_id
            else:
                arguments["workspaceId"] = workspace_id
            if cursor:
                arguments["pageToken"] = cursor
            state.current_node_id = folder_id or workspace_id or ""
            payload = await client.call("doc", "list_nodes", arguments)
            for item in _required_items(payload, "folder listing", "nodes", "list"):
                if not isinstance(item, Mapping):
                    raise DingTalkImportError("DingTalk returned an invalid folder entry")
                child_id = _node_id(item)
                await self._process_node(
                    client,
                    state,
                    child_id,
                    parent,
                    depth=depth + 1,
                    ancestors=ancestors,
                )
            next_cursor = _first(payload, "nextPageToken", "nextToken", default=None)
            if not next_cursor:
                if bool(_first(payload, "hasMore", default=False)):
                    raise DingTalkImportError(
                        "DingTalk folder pagination reported more results without a cursor"
                    )
                return
            if not isinstance(next_cursor, str) or next_cursor in cursors:
                raise DingTalkImportError("DingTalk folder pagination returned a repeated cursor")
            cursors.add(next_cursor)
            cursor = next_cursor

    async def _write_document(
        self,
        client: Any,
        state: _State,
        info: Mapping[str, Any],
        source_url: str,
        target: Path,
    ) -> None:
        node_id = _node_id(info)
        payload = await client.call(
            "doc", "get_document_content", {"nodeId": node_id, "format": "markdown"}
        )
        markdown = _first(payload, "markdown")
        if not isinstance(markdown, str):
            raise DingTalkImportError("DingTalk document did not return Markdown content")
        self._ensure_fits(state, len(markdown.encode("utf-8")))
        markdown = await self._localize_images(state, markdown, target.parent)
        markdown = await self._localize_attachments(client, state, node_id, markdown, target.parent)
        markdown = await self._localize_linked_assets(state, markdown, target.parent)
        if _REMOTE_ASSET_RE.search(markdown):
            raise DingTalkImportError(
                "DingTalk document still contains a remote image or attachment reference; "
                "nested attachments are not safely importable",
                code="FAILED_PRECONDITION",
            )
        self._write_text(state, target, _markdown_header(info, source_url) + markdown)

    async def _localize_images(
        self,
        state: _State,
        markdown: str,
        parent: Path,
    ) -> str:
        replacements: dict[str, str] = {}
        for match in _IMAGE_RE.finditer(markdown):
            raw_target = match.group(2).strip()
            url = raw_target.split(maxsplit=1)[0].strip("<>")
            if not url.startswith("https://"):
                continue
            data, content_type = await self._download(
                state,
                url,
                headers={},
            )
            digest = hashlib.sha256(data).hexdigest()
            suffix = _extension(Path(urlparse(url).path).suffix, content_type)
            relative = Path("assets") / f"{digest}{suffix}"
            asset = parent / relative
            if not asset.exists():
                asset.parent.mkdir(exist_ok=True)
                asset.write_bytes(data)
            replacements[match.group(0)] = f"![{match.group(1)}]({relative.as_posix()})"
        for original, replacement in replacements.items():
            markdown = markdown.replace(original, replacement)
        return markdown

    async def _localize_attachments(
        self,
        client: Any,
        state: _State,
        node_id: str,
        markdown: str,
        parent: Path,
    ) -> str:
        blocks: list[Any] = []
        start = 0
        pages = 0
        while True:
            pages += 1
            if pages > 1000:
                raise DingTalkImportError("DingTalk attachment pagination exceeded its safe limit")
            payload = await client.call(
                "doc",
                "list_document_blocks",
                {
                    "nodeId": node_id,
                    "blockType": "attachment",
                    "format": "element",
                    "startIndex": start,
                    "endIndex": start + 99,
                },
            )
            page = _required_items(payload, "attachment listing", "blocks", "elements", "list")
            blocks.extend(page)
            if not bool(_first(payload, "hasMore", default=False)):
                break
            # Indices address all root blocks, before the attachment filter.
            next_start = _first(payload, "nextIndex", "nextStartIndex", default=start + 100)
            if not isinstance(next_start, int) or next_start <= start:
                raise DingTalkImportError("DingTalk attachment pagination did not advance")
            start = next_start

        appended: list[str] = []
        for block in blocks:
            resource_ids = _find_values(block, _ATTACHMENT_RESOURCE_KEYS)
            if not resource_ids:
                raise DingTalkImportError("DingTalk attachment block is missing its resource ID")
            for resource_id in resource_ids:
                payload = await client.call(
                    "doc",
                    "download_doc_attachment",
                    {"nodeId": node_id, "resourceId": resource_id},
                )
                url = _first(payload, "downloadUrl", "resourceUrl")
                if isinstance(url, list):
                    url = url[0] if url else None
                if not isinstance(url, str) or not url:
                    raise DingTalkImportError("DingTalk attachment did not return a download URL")
                data, content_type = await self._download(state, url, headers={})
                suffix = _extension(Path(urlparse(url).path).suffix, content_type)
                relative = Path("attachments") / f"{_safe_id(resource_id)}{suffix}"
                destination = parent / relative
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes(data)
                urls = _find_values(block, frozenset({"url", "downloadUrl", "resourceUrl"}))
                replaced = False
                for old_url in urls:
                    if old_url in markdown:
                        markdown = markdown.replace(old_url, relative.as_posix())
                        replaced = True
                if not replaced:
                    appended.append(f"- [{resource_id}]({relative.as_posix()})")
        if appended:
            markdown = markdown.rstrip() + "\n\n## Attachments\n\n" + "\n".join(appended) + "\n"
        return markdown

    async def _localize_linked_assets(
        self,
        state: _State,
        markdown: str,
        parent: Path,
    ) -> str:
        replacements: dict[str, str] = {}
        for match in _LINK_RE.finditer(markdown):
            url = match.group(2).strip().split(maxsplit=1)[0].strip("<>")
            if url in replacements or not _REMOTE_ASSET_RE.fullmatch(url):
                continue
            data, content_type = await self._download(state, url, headers={})
            suffix = _extension(Path(urlparse(url).path).suffix, content_type)
            relative = Path("attachments") / f"{hashlib.sha256(data).hexdigest()}{suffix}"
            destination = parent / relative
            if not destination.exists():
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes(data)
            replacements[url] = relative.as_posix()
        for original, replacement in replacements.items():
            markdown = markdown.replace(original, replacement)
        return markdown

    async def _write_sheet(
        self,
        client: Any,
        state: _State,
        info: Mapping[str, Any],
        source_url: str,
        target: Path,
    ) -> None:
        node_id = _node_id(info)
        sheets_payload = await client.call("sheets", "get_all_sheets", {"nodeId": node_id})
        sheets = _required_items(sheets_payload, "worksheet listing", "sheets", "list")
        if not sheets:
            raise DingTalkImportError("DingTalk sheet did not return any worksheets")
        parts = [_markdown_header(info, source_url)]
        for sheet in sheets:
            if not isinstance(sheet, Mapping):
                raise DingTalkImportError("DingTalk returned an invalid worksheet")
            sheet_id = sheet.get("id") or sheet.get("sheetId")
            if not isinstance(sheet_id, str) or not sheet_id:
                raise DingTalkImportError("DingTalk worksheet is missing its ID")
            detail = _payload_body(
                await client.call("sheets", "get_sheet", {"nodeId": node_id, "sheetId": sheet_id})
            )
            if "nonEmptyRange" not in detail:
                raise DingTalkImportError("DingTalk worksheet is missing its non-empty range")
            non_empty = detail["nonEmptyRange"]
            rows, columns = 0, 0
            if non_empty is not None:
                if not isinstance(non_empty, Mapping):
                    raise DingTalkImportError("DingTalk worksheet returned an invalid range")
                last_row = non_empty.get("lastRow")
                columns = _column_number(non_empty.get("lastColumn"))
                if not isinstance(last_row, int) or isinstance(last_row, bool) or last_row < 1:
                    raise DingTalkImportError("DingTalk worksheet returned an invalid last row")
                rows = last_row
            csv_chunks: list[str] = []
            if rows > 0 and columns > 0:
                await self._read_sheet_range(
                    client,
                    state,
                    node_id,
                    sheet_id,
                    1,
                    rows,
                    columns,
                    csv_chunks,
                )
            name = str(sheet.get("name") or detail.get("name") or sheet_id)
            parts.extend([f"## {name}\n", *csv_chunks])
            if detail.get("mergedRanges"):
                parts.append("Merged cells: " + json.dumps(detail["mergedRanges"]) + "\n")
        content = "\n".join(parts)
        if _REMOTE_ASSET_RE.search(content):
            raise DingTalkImportError(
                "DingTalk sheet contains a temporary remote asset that cannot be preserved",
                code="FAILED_PRECONDITION",
            )
        self._write_text(state, target, content)

    async def _read_sheet_range(
        self,
        client: Any,
        state: _State,
        node_id: str,
        sheet_id: str,
        start_row: int,
        end_row: int,
        columns: int,
        output: list[str],
        start_column: int = 1,
    ) -> None:
        if len(output) >= 1000:
            raise DingTalkImportError(
                "DingTalk worksheet exceeded 1000 blocks", code="RESOURCE_EXHAUSTED"
            )
        cell_range = f"{_column_name(start_column)}{start_row}:{_column_name(columns)}{end_row}"
        payload = await client.call(
            "sheets",
            "get_range_as_csv",
            {
                "nodeId": node_id,
                "sheetId": sheet_id,
                "range": cell_range,
                "annotateRowNumbers": False,
                "maxChars": 200000,
                "valueRenderOption": "formatted_value",
            },
        )
        returned = _first(payload, "returnedRange")
        incomplete = (
            _first(payload, "hasMore") is not False
            or bool(_first(payload, "truncationReasons", default=[]))
            or not isinstance(returned, str)
            or returned.rsplit("!", 1)[-1].replace("$", "").upper() != cell_range
        )
        if incomplete:
            if start_row < end_row:
                midpoint = (start_row + end_row) // 2
                ranges = [
                    (start_row, midpoint, start_column, columns),
                    (midpoint + 1, end_row, start_column, columns),
                ]
            elif start_column < columns:
                midpoint = (start_column + columns) // 2
                ranges = [
                    (start_row, end_row, start_column, midpoint),
                    (start_row, end_row, midpoint + 1, columns),
                ]
            else:
                raise DingTalkImportError("DingTalk worksheet response is incomplete")
            for first_row, last_row, first_col, last_col in ranges:
                await self._read_sheet_range(
                    client,
                    state,
                    node_id,
                    sheet_id,
                    first_row,
                    last_row,
                    last_col,
                    output,
                    first_col,
                )
            return
        value = _first(payload, "csv", "content")
        if not isinstance(value, str):
            raise DingTalkImportError("DingTalk sheet range did not return CSV content")
        row_indices = _required_items(payload, "worksheet row mapping", "rowIndices")
        col_indices = _required_items(payload, "worksheet column mapping", "colIndices")
        if any(
            not isinstance(row, int) or isinstance(row, bool) or not start_row <= row <= end_row
            for row in row_indices
        ) or row_indices != sorted(set(row_indices)):
            raise DingTalkImportError("DingTalk worksheet returned invalid row positions")
        col_numbers = [_column_number(col) for col in col_indices]
        if any(not start_column <= col <= columns for col in col_numbers) or col_numbers != sorted(
            set(col_numbers)
        ):
            raise DingTalkImportError("DingTalk worksheet returned invalid column positions")
        try:
            csv_rows = list(csv.reader(io.StringIO(value), strict=True))
        except csv.Error:
            raise DingTalkImportError("DingTalk worksheet returned invalid CSV") from None
        if len(csv_rows) != len(row_indices) or any(
            len(row) != len(col_indices) for row in csv_rows
        ):
            raise DingTalkImportError(
                "DingTalk worksheet data does not match its row/column mapping"
            )
        block = (
            f"Range: {cell_range}\nRows: {json.dumps(row_indices)}\n"
            f"Columns: {json.dumps(col_indices)}\n\n```csv\n{value.rstrip()}\n```\n"
        )
        self._ensure_fits(
            state,
            sum(len(chunk.encode("utf-8")) for chunk in output) + len(block.encode("utf-8")),
        )
        output.append(block)

    async def _write_ai_table(
        self,
        client: Any,
        state: _State,
        info: Mapping[str, Any],
        source_url: str,
        target: Path,
    ) -> None:
        base_id = _node_id(info)
        await client.call("ai_table", "get_base", {"baseId": base_id})
        tables_payload = await client.call("ai_table", "get_tables", {"baseId": base_id})
        tables = _required_items(tables_payload, "AI table listing", "tables", "list")
        if not tables:
            raise DingTalkImportError(
                "DingTalk AI table returned no tables; the document node ID may not map to a base ID"
            )
        parts = [_markdown_header(info, source_url)]
        for table in tables:
            if not isinstance(table, Mapping):
                raise DingTalkImportError("DingTalk returned an invalid AI table")
            table_id = table.get("id") or table.get("tableId")
            if not isinstance(table_id, str) or not table_id:
                raise DingTalkImportError("DingTalk AI table is missing its table ID")
            fields = await client.call(
                "ai_table", "get_fields", {"baseId": base_id, "tableId": table_id}
            )
            field_items = _required_items(fields, "AI field listing", "fields", "list")
            self._ensure_fits(
                state,
                len(json.dumps(field_items, ensure_ascii=False, default=str).encode("utf-8")),
            )
            records = await self._query_all_records(client, state, base_id, table_id)
            name = str(table.get("name") or table_id)
            export = {
                "fields": field_items,
                "records": records,
            }
            parts.extend(
                [
                    f"## {name}\n",
                    "```json\n"
                    + json.dumps(export, ensure_ascii=False, indent=2, default=str)
                    + "\n```\n",
                ]
            )
        content = "\n".join(parts)
        if _REMOTE_ASSET_RE.search(content):
            raise DingTalkImportError(
                "DingTalk AI table contains a temporary remote asset that cannot be preserved",
                code="FAILED_PRECONDITION",
            )
        self._write_text(state, target, content)

    async def _query_all_records(
        self,
        client: Any,
        state: _State,
        base_id: str,
        table_id: str,
    ) -> list[Any]:
        for restart in range(2):
            records: list[Any] = []
            records_bytes = 2
            cursor: str | None = None
            seen: set[str] = set()
            pages = 0
            try:
                while True:
                    pages += 1
                    if pages > 1000:
                        raise DingTalkImportError(
                            "DingTalk AI table pagination exceeded its safe limit"
                        )
                    arguments: dict[str, Any] = {
                        "baseId": base_id,
                        "tableId": table_id,
                        "limit": 100,
                    }
                    if cursor:
                        arguments["cursor"] = cursor
                    payload = await client.call("ai_table", "query_records", arguments)
                    page = _required_items(payload, "AI record query", "records", "list")
                    page_bytes = sum(
                        len(json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")) + 1
                        for record in page
                    )
                    self._ensure_fits(state, records_bytes + page_bytes)
                    records_bytes += page_bytes
                    records.extend(page)
                    next_cursor = _first(payload, "nextCursor", default=None)
                    if not next_cursor:
                        if bool(_first(payload, "hasMore", default=False)):
                            raise DingTalkImportError(
                                "DingTalk AI table reported more records without a cursor"
                            )
                        return records
                    if not isinstance(next_cursor, str) or next_cursor.startswith("error-v1:"):
                        raise DingTalkImportError(
                            "DingTalk AI table returned a non-resumable cursor"
                        )
                    if next_cursor in seen:
                        raise DingTalkImportError("DingTalk AI table pagination repeated a cursor")
                    seen.add(next_cursor)
                    cursor = next_cursor
            except DingTalkMCPError as exc:
                if (
                    exc.reason_code in {"INVALID_CURSOR", "CURSOR_SNAPSHOT_CHANGED"}
                    and restart == 0
                ):
                    continue
                if exc.reason_code == "CURSOR_SNAPSHOT_UNAVAILABLE":
                    raise DingTalkImportError(
                        "DingTalk AI table cannot provide a consistent record snapshot"
                    ) from None
                if exc.reason_code in {"NON_RESUMABLE_ERROR_CURSOR", "CURSOR_OFFSET_LIMIT"}:
                    raise DingTalkImportError(
                        "DingTalk AI table pagination cannot continue safely"
                    ) from None
                raise
        raise DingTalkImportError("DingTalk AI table changed while records were being read")

    async def _write_cloud_file(
        self,
        client: Any,
        state: _State,
        info: Mapping[str, Any],
        target: Path,
    ) -> None:
        payload = await client.call("doc", "download_file", {"nodeId": _node_id(info)})
        urls = _first(payload, "resourceUrl", "downloadUrl")
        if isinstance(urls, str):
            url = urls
        elif isinstance(urls, list) and urls and isinstance(urls[0], str):
            url = urls[0]
        else:
            raise DingTalkImportError("DingTalk file did not return a download URL")
        headers = _first(payload, "headers", default={})
        if not isinstance(headers, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            raise DingTalkImportError("DingTalk file returned invalid download headers")
        data, _ = await self._download(state, url, headers=dict(headers))
        target.write_bytes(data)

    async def _download(
        self,
        state: _State,
        url: str,
        *,
        headers: dict[str, str],
    ) -> tuple[bytes, str | None]:
        for attempt in range(3):
            try:
                return await self._download_once(state, url, headers=headers)
            except DingTalkImportError as exc:
                if exc.code in {"PERMISSION_DENIED", "INVALID_ARGUMENT", "RESOURCE_EXHAUSTED"}:
                    raise
                if attempt == 2:
                    raise
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 2:
                    raise DingTalkImportError(
                        "DingTalk download failed after retries", code="UNAVAILABLE"
                    ) from None
            await asyncio.sleep(0.2 * (2**attempt))
        raise DingTalkImportError("DingTalk download failed", code="UNAVAILABLE")

    async def _download_once(
        self,
        state: _State,
        url: str,
        *,
        headers: dict[str, str],
    ) -> tuple[bytes, str | None]:
        current = url
        current_headers = headers
        chunks: list[bytes] = []
        counted = 0
        event_hooks = build_httpx_request_validation_hooks(state.request_validator)
        try:
            async with sensitive_download_logs():
                async with httpx.AsyncClient(
                    timeout=30.0,
                    follow_redirects=False,
                    trust_env=False,
                    event_hooks=event_hooks or {},
                ) as http_client:
                    for redirect in range(_MAX_REDIRECTS + 1):
                        parsed = urlparse(current)
                        if parsed.scheme != "https" or not parsed.hostname:
                            raise DingTalkImportError(
                                "DingTalk download URL must use HTTPS", code="INVALID_ARGUMENT"
                            )
                        if state.request_validator is not None:
                            state.request_validator(current)
                        async with http_client.stream(
                            "GET", current, headers=current_headers
                        ) as response:
                            if response.status_code in {401, 403}:
                                raise DingTalkImportError(
                                    "DingTalk download permission denied", code="PERMISSION_DENIED"
                                )
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location or redirect == _MAX_REDIRECTS:
                                    raise DingTalkImportError(
                                        "DingTalk download redirect could not be followed",
                                        code="UNAVAILABLE",
                                    )
                                next_url = urljoin(current, location)
                                if self._origin(next_url) != self._origin(current):
                                    current_headers = {}
                                current = next_url
                                continue
                            if response.status_code == 429 or response.status_code >= 500:
                                raise DingTalkImportError(
                                    "DingTalk download service is temporarily unavailable",
                                    code="UNAVAILABLE",
                                )
                            if not 200 <= response.status_code < 300:
                                raise DingTalkImportError(
                                    "DingTalk download request was rejected",
                                    code="NOT_FOUND"
                                    if response.status_code in {404, 410}
                                    else "UNAVAILABLE",
                                )
                            async for chunk in response.aiter_bytes():
                                state.limits.add_bytes(len(chunk))
                                counted += len(chunk)
                                chunks.append(chunk)
                            return b"".join(chunks), response.headers.get("content-type")
        except BaseException:
            state.limits.bytes -= counted
            raise
        raise DingTalkImportError("DingTalk download failed", code="UNAVAILABLE")

    @staticmethod
    def _origin(url: str) -> tuple[str, str | None, int | None]:
        parsed = urlparse(url)
        return (
            parsed.scheme.lower(),
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else None),
        )

    @staticmethod
    def _manifest_entry(
        state: _State,
        info: Mapping[str, Any],
        source_url: str,
        path: Path,
        kind: str,
    ) -> dict[str, Any]:
        return {
            "node_id": _node_id(info),
            "relative_path": path.relative_to(state.root).as_posix(),
            "type": kind,
            "title": str(info.get("name") or info.get("title") or "Untitled"),
            "source_url": source_url,
            "updated_at": info.get("updateTime") or info.get("modifiedTime"),
            "source_marker": _source_marker(info),
        }

    @staticmethod
    def _can_reuse_metadata(
        previous: dict[str, Any] | None,
        current: dict[str, Any],
    ) -> bool:
        return bool(
            previous
            and previous.get("requires_content_check") is False
            and current.get("source_marker") is not None
            and previous.get("source_marker") == current.get("source_marker")
            and previous.get("relative_path") == current.get("relative_path")
            and previous.get("type") == current.get("type")
            and previous.get("title") == current.get("title")
        )

    @staticmethod
    def _record_reuse(
        state: _State,
        entry: dict[str, Any],
        previous: dict[str, Any],
        mode: str,
    ) -> None:
        for key in ("content_sha256", "requires_content_check", "source_paths"):
            if key in previous:
                entry[key] = previous[key]
        if mode == "unreadable":
            # The retained bytes still represent the old path and version.
            # A failed read must not make the new version look processed.
            entry["relative_path"] = previous["relative_path"]
            entry["source_marker"] = previous.get("source_marker")
            entry["requires_content_check"] = True
        entry.update({"reused": True, "reuse_mode": mode})
        state.manifest.append(entry)
        state.report["success"] += 1
        state.report["success_nodes"].append(entry["node_id"])
        state.report["reused"] += 1
        state.report["reused_nodes"].append(entry["node_id"])
        state.report[f"{mode}_reused_nodes"].append(entry["node_id"])

    @staticmethod
    def _tree_digest(root: Path, document: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix().encode("utf-8")
            data = path.read_bytes()
            if path == document and path.suffix.lower() == ".md":
                data = re.sub(
                    rb"\A(#[^\n]*\n\n- DingTalk node ID: [^\n]*\n- Source: [^\n]*\n)"
                    rb"- DingTalk updated: [^\n]*\n",
                    rb"\1",
                    data,
                    count=1,
                )
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()

    @staticmethod
    def _merge_materialized(source: Path, destination: Path) -> None:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
        shutil.rmtree(source, ignore_errors=True)

    @staticmethod
    def _ensure_fits(state: _State, additional_size: int) -> None:
        if additional_size < 0 or state.limits.bytes + additional_size > state.limits.max_bytes:
            raise DingTalkImportError(
                f"DingTalk import exceeded the byte limit ({state.limits.max_bytes})",
                code="RESOURCE_EXHAUSTED",
            )

    @staticmethod
    def _write_text(state: _State, target: Path, content: str) -> None:
        data = content.encode("utf-8")
        state.limits.add_bytes(len(data))
        target.write_bytes(data)
