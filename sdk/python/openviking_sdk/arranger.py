# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Declarative context orchestration for the OpenViking Python SDK."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from ._utils import run_async
from .client import AsyncHTTPClient, SyncHTTPClient, VikingURI
from .errors import FailedPreconditionError, NotFoundError
from .options import MCPToolMount

_OWNER_TAG_KEY = "ov-cac-owner"
_KIND_TAG_KEY = "ov-cac-kind"
_SPEC_TAG_KEY = "ov-cac-spec"
_RESERVED_TAG_KEYS = frozenset({_OWNER_TAG_KEY, _KIND_TAG_KEY, _SPEC_TAG_KEY})
_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_.@-]+$")
_TOOL_URI_RE = re.compile(r"^viking://agent/tools/(?P<server>[^/]+)/(?P<tool>[^/]+)\.json$")


class ContextResourceKind(str, Enum):
    LIVE_CONTEXT = "live_context"


class ContextChangeAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class LiveContext:
    """A live context node backed by one registered MCP tool."""

    uri: str
    tool_uri: str
    title: str
    description: Optional[str] = None
    timeout_seconds: int = 60
    arguments: Mapping[str, Any] = field(default_factory=dict)
    tags: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", dict(self.arguments))

    @property
    def kind(self) -> ContextResourceKind:
        return ContextResourceKind.LIVE_CONTEXT


@dataclass(frozen=True)
class ContextChange:
    action: ContextChangeAction
    kind: ContextResourceKind
    uri: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "action": self.action.value,
            "kind": self.kind.value,
            "uri": self.uri,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContextPlan:
    project: str
    root_uri: str
    changes: Tuple[ContextChange, ...]

    @property
    def has_changes(self) -> bool:
        return any(
            change.action
            in {
                ContextChangeAction.CREATE,
                ContextChangeAction.UPDATE,
                ContextChangeAction.DELETE,
            }
            for change in self.changes
        )

    @property
    def has_conflicts(self) -> bool:
        return any(change.action == ContextChangeAction.CONFLICT for change in self.changes)

    def to_dict(self) -> Dict[str, Any]:
        counts = {action.value: 0 for action in ContextChangeAction}
        for change in self.changes:
            counts[change.action.value] += 1
        return {
            "project": self.project,
            "root_uri": self.root_uri,
            "has_changes": self.has_changes,
            "has_conflicts": self.has_conflicts,
            "counts": counts,
            "changes": [change.to_dict() for change in self.changes],
        }


@dataclass(frozen=True)
class ContextApplyResult:
    plan: ContextPlan
    prune: bool
    dry_run: bool
    applied: Tuple[ContextChange, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prune": self.prune,
            "dry_run": self.dry_run,
            "applied": [change.to_dict() for change in self.applied],
            "plan": self.plan.to_dict(),
        }


def _is_context_resource_root(uri: str) -> bool:
    if not uri.startswith("viking://"):
        return False
    parts = uri[len("viking://") :].split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if parts[:1] == ["resources"]:
        return True
    return (
        len(parts) >= 3
        and parts[0] == "user"
        and _USER_ID_RE.fullmatch(parts[1]) is not None
        and parts[2] == "resources"
    )


class _ContextArrangerBase:
    def __init__(
        self,
        client: Union[AsyncHTTPClient, SyncHTTPClient],
        name: str,
        root_uri: str = "viking://resources",
        *,
        max_managed_resources: int = 10_000,
    ):
        normalized_name = name.strip().lower()
        if not _OWNER_RE.fullmatch(normalized_name):
            raise ValueError(
                "Context project name must contain only lowercase letters, digits, '.', '_' or '-'"
            )
        if max_managed_resources <= 0:
            raise ValueError("max_managed_resources must be positive")
        # SyncHTTPClient already delegates to its asynchronous backend through
        # run_async. Reuse that backend to avoid nesting calls on the SDK worker loop.
        self._client: AsyncHTTPClient = (
            client._async_client if isinstance(client, SyncHTTPClient) else client
        )
        self.name = normalized_name
        self.root_uri = VikingURI.normalize(root_uri).rstrip("/")
        if not _is_context_resource_root(self.root_uri):
            raise ValueError(
                "Context project root_uri must be within viking://resources or "
                "viking://user/<user_id>/resources"
            )
        self.max_managed_resources = max_managed_resources
        self._resources: Dict[str, LiveContext] = {}

    @property
    def owner_tag(self) -> str:
        return f"{_OWNER_TAG_KEY}={self.name}"

    @property
    def resources(self) -> Tuple[LiveContext, ...]:
        return tuple(self._resources[uri] for uri in sorted(self._resources))

    def live_context(
        self,
        path: str,
        tool_uri: str,
        title: str,
        *,
        description: Optional[str] = None,
        timeout_seconds: int = 60,
        arguments: Optional[Mapping[str, Any]] = None,
        tags: Sequence[str] = (),
    ) -> "_ContextArrangerBase":
        uri = self._resolve_uri(path)
        normalized_tool_uri = VikingURI.normalize(tool_uri)
        if not uri.endswith(".md"):
            raise ValueError("Live context path must end with .md")
        if not _TOOL_URI_RE.fullmatch(normalized_tool_uri):
            raise ValueError("tool_uri must match viking://agent/tools/<server>/<tool>.json")
        if not title.strip():
            raise ValueError("Live context title must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        resource = LiveContext(
            uri=uri,
            tool_uri=normalized_tool_uri,
            title=title,
            description=description,
            timeout_seconds=timeout_seconds,
            arguments=dict(arguments or {}),
            tags=_normalize_tags(tags),
        )
        self._declare(resource)
        return self

    def _declare(self, resource: LiveContext) -> None:
        if resource.uri in self._resources:
            raise ValueError(f"Context resource declared more than once: {resource.uri}")
        self._resources[resource.uri] = resource

    def _resolve_uri(self, path: str) -> str:
        value = path.strip()
        if not value:
            raise ValueError("Context resource path must not be empty")
        if value.startswith("viking://"):
            uri = VikingURI.normalize(value).rstrip("/")
        else:
            parts = value.strip("/").split("/")
            if any(part in {"", ".", ".."} for part in parts):
                raise ValueError(f"Invalid context resource path: {path}")
            uri = f"{self.root_uri.rstrip('/')}/{'/'.join(parts)}"
        prefix = f"{self.root_uri.rstrip('/')}/"
        if uri == self.root_uri or not uri.startswith(prefix):
            raise ValueError(f"Context resource must be below {self.root_uri}: {uri}")
        return uri

    async def _managed_entries(self) -> Dict[str, Dict[str, Any]]:
        try:
            entries = await self._client.ls(
                self.root_uri,
                recursive=True,
                include_tags=True,
                tags=[self.owner_tag],
                node_limit=self.max_managed_resources,
            )
        except NotFoundError:
            return {}
        if len(entries) >= self.max_managed_resources:
            raise FailedPreconditionError(
                "Managed resource scan reached max_managed_resources; increase the limit "
                "before planning or pruning"
            )
        result: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("isDir"):
                continue
            uri = entry.get("uri")
            if isinstance(uri, str):
                result[VikingURI.normalize(uri)] = entry
        return result

    async def _exists(self, uri: str) -> bool:
        try:
            await self._client.stat(uri)
        except NotFoundError:
            return False
        return True

    async def _matches_applied(
        self,
        resource: LiveContext,
        entry: Mapping[str, Any],
        fingerprint: str,
    ) -> bool:
        expected_tags = _tags_by_key(_managed_tags(self.owner_tag, resource))
        if _tags_by_key(entry.get("tags", [])) != expected_tags:
            return False
        if expected_tags.get(_SPEC_TAG_KEY) != fingerprint:
            return False

        match = _TOOL_URI_RE.fullmatch(resource.tool_uri)
        assert match is not None
        try:
            result = await self._client.list_mcp_mounts(
                match.group("server"),
                tool=match.group("tool"),
            )
        except NotFoundError:
            return False
        mounts = result.get("mounts", []) if isinstance(result, dict) else []
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            if VikingURI.normalize(str(mount.get("node_uri", ""))) != resource.uri:
                continue
            return (
                VikingURI.normalize(str(mount.get("tool_uri", ""))) == resource.tool_uri
                and mount.get("arguments", {}) == resource.arguments
            )
        return False

    async def _plan(self) -> ContextPlan:
        managed = await self._managed_entries()
        changes = []
        for uri, resource in sorted(self._resources.items()):
            fingerprint = _fingerprint(resource)
            entry = managed.get(uri)
            if entry is None:
                if await self._exists(uri):
                    changes.append(
                        ContextChange(
                            ContextChangeAction.CONFLICT,
                            resource.kind,
                            uri,
                            "target exists but is not managed by this project",
                        )
                    )
                else:
                    changes.append(
                        ContextChange(
                            ContextChangeAction.CREATE,
                            resource.kind,
                            uri,
                            "target does not exist",
                        )
                    )
                continue

            tags = _tags_by_key(entry.get("tags", []))
            current_kind = tags.get(_KIND_TAG_KEY)
            if current_kind and current_kind != resource.kind.value:
                changes.append(
                    ContextChange(
                        ContextChangeAction.CONFLICT,
                        resource.kind,
                        uri,
                        f"managed target kind is {current_kind}",
                    )
                )
            elif tags.get(_SPEC_TAG_KEY) == fingerprint and await self._matches_applied(
                resource, entry, fingerprint
            ):
                changes.append(
                    ContextChange(
                        ContextChangeAction.UNCHANGED,
                        resource.kind,
                        uri,
                        "declaration matches the applied specification",
                    )
                )
            else:
                changes.append(
                    ContextChange(
                        ContextChangeAction.UPDATE,
                        resource.kind,
                        uri,
                        "actual state differs from the declaration",
                    )
                )

        for uri, entry in sorted(managed.items()):
            if uri in self._resources:
                continue
            tags = _tags_by_key(entry.get("tags", []))
            raw_kind = tags.get(_KIND_TAG_KEY)
            if raw_kind != ContextResourceKind.LIVE_CONTEXT.value:
                changes.append(
                    ContextChange(
                        ContextChangeAction.CONFLICT,
                        ContextResourceKind.LIVE_CONTEXT,
                        uri,
                        f"managed target has unsupported kind {raw_kind!r}",
                    )
                )
                continue
            changes.append(
                ContextChange(
                    ContextChangeAction.DELETE,
                    ContextResourceKind.LIVE_CONTEXT,
                    uri,
                    "managed target is absent from the declaration",
                )
            )

        return ContextPlan(
            project=self.name,
            root_uri=self.root_uri,
            changes=tuple(changes),
        )

    async def _apply(
        self,
        *,
        prune: bool,
        dry_run: bool,
        wait: bool,
    ) -> ContextApplyResult:
        plan = await self._plan()
        if plan.has_conflicts:
            conflicts = [
                change.to_dict()
                for change in plan.changes
                if change.action == ContextChangeAction.CONFLICT
            ]
            raise FailedPreconditionError(
                "Context plan contains conflicts",
                details={"conflicts": conflicts},
            )
        if dry_run:
            return ContextApplyResult(plan=plan, prune=prune, dry_run=True, applied=())

        applied = []
        mount_changes = []
        mounts: list[MCPToolMount] = []
        for change in plan.changes:
            if change.action not in {
                ContextChangeAction.CREATE,
                ContextChangeAction.UPDATE,
            }:
                continue
            resource = self._resources[change.uri]
            mount: MCPToolMount = {
                "tool_uri": resource.tool_uri,
                "node_uri": resource.uri,
                "title": resource.title,
                "timeout_seconds": resource.timeout_seconds,
                "arguments": resource.arguments,
                "tags": _managed_tags(self.owner_tag, resource),
            }
            if resource.description is not None:
                mount["description"] = resource.description
            mounts.append(mount)
            mount_changes.append(change)
        if mounts:
            await self._client.batch_mount_mcp_tools(mounts, wait=wait)
            applied.extend(mount_changes)

        delete_changes = []
        for change in plan.changes:
            if change.action != ContextChangeAction.DELETE:
                continue
            if not prune:
                continue
            delete_changes.append(change)
        if delete_changes:
            try:
                await self._client.batch_unmount_mcp_tools(
                    [change.uri for change in delete_changes],
                    wait=wait,
                )
            except NotFoundError:
                for change in delete_changes:
                    try:
                        await self._client.unmount_mcp_tool(change.uri, wait=wait)
                    except NotFoundError:
                        await self._client.rm(change.uri, wait=wait)
            applied.extend(delete_changes)

        return ContextApplyResult(
            plan=plan,
            prune=prune,
            dry_run=False,
            applied=tuple(applied),
        )


class AsyncContextArranger(_ContextArrangerBase):
    """Asynchronous declarative context arranger."""

    async def plan(self) -> ContextPlan:
        return await self._plan()

    async def apply(
        self,
        *,
        prune: bool = False,
        dry_run: bool = False,
        wait: bool = False,
    ) -> ContextApplyResult:
        return await self._apply(prune=prune, dry_run=dry_run, wait=wait)


class ContextArranger(_ContextArrangerBase):
    """Synchronous declarative context arranger."""

    def plan(self) -> ContextPlan:
        return run_async(self._plan())

    def apply(
        self,
        *,
        prune: bool = False,
        dry_run: bool = False,
        wait: bool = False,
    ) -> ContextApplyResult:
        return run_async(self._apply(prune=prune, dry_run=dry_run, wait=wait))

    def main(self, argv: Optional[Sequence[str]] = None) -> int:
        """Run this project as a small command-line application."""
        parser = argparse.ArgumentParser(
            description=(
                f"Plan or apply OpenViking context arrangement '{self.name}' under {self.root_uri}."
            )
        )
        commands = parser.add_subparsers(dest="command", required=True)

        plan_parser = commands.add_parser(
            "plan",
            help="Show the changes required to reach the declared state.",
        )
        _add_output_argument(plan_parser)

        apply_parser = commands.add_parser(
            "apply",
            help="Converge OpenViking resources to the declared state.",
        )
        apply_parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete owned resources absent from the declaration.",
        )
        apply_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the apply plan without changing OpenViking.",
        )
        apply_parser.add_argument(
            "-w",
            "--wait",
            action="store_true",
            help="Wait until processing is complete.",
        )
        _add_output_argument(apply_parser)

        args = parser.parse_args(argv)
        if args.command == "plan":
            result: Union[ContextPlan, ContextApplyResult] = self.plan()
        else:
            result = self.apply(
                prune=args.prune,
                dry_run=args.dry_run,
                wait=args.wait,
            )
        if args.output == "json":
            output = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
        else:
            output = _format_result_table(result)
        print(output)
        return 0


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        choices=("table", "json"),
        default="table",
        help="Output format (default: table).",
    )


def _format_result_table(result: Union[ContextPlan, ContextApplyResult]) -> str:
    if isinstance(result, ContextApplyResult):
        plan = result.plan
        title = (
            "OpenViking Context Apply (dry run)"
            if result.dry_run
            else "OpenViking Context Apply Result"
        )
    else:
        plan = result
        title = "OpenViking Context Plan"

    counts = {action.value: 0 for action in ContextChangeAction}
    for change in plan.changes:
        counts[change.action.value] += 1

    lines = [
        title,
        f"Arrangement: {plan.project}",
        f"Root:        {plan.root_uri}",
    ]
    if isinstance(result, ContextApplyResult):
        lines.extend(
            [
                f"Prune:       {'yes' if result.prune else 'no'}",
                f"Dry run:     {'yes' if result.dry_run else 'no'}",
                f"Applied:     {len(result.applied)}",
            ]
        )
    if isinstance(result, ContextApplyResult) and not result.dry_run:
        delete_summary = (
            f"{counts['delete']} deleted"
            if result.prune
            else f"{counts['delete']} pending deletion"
        )
        lines.append(
            "Summary:     "
            f"{counts['create']} created, "
            f"{counts['update']} updated, "
            f"{delete_summary}, "
            f"{counts['unchanged']} unchanged"
        )
    else:
        lines.append(
            "Summary:     "
            f"{counts['create']} create, "
            f"{counts['update']} update, "
            f"{counts['delete']} delete, "
            f"{counts['unchanged']} unchanged, "
            f"{counts['conflict']} conflict"
        )
    lines.append("")
    if not plan.changes:
        lines.append("No resources declared.")
    else:
        lines.extend(
            _format_change_rows(
                plan.changes,
                applied=isinstance(result, ContextApplyResult) and not result.dry_run,
                prune=result.prune if isinstance(result, ContextApplyResult) else False,
            )
        )
    return "\n".join(lines)


def _format_change_rows(
    changes: Sequence[ContextChange],
    *,
    applied: bool,
    prune: bool,
) -> list[str]:
    rows = [("RESULT" if applied else "ACTION", "TYPE", "URI", "DETAIL")]
    for change in changes:
        action = change.action.value.upper()
        detail = change.reason
        if applied:
            action, detail = _applied_change_display(change.action, prune=prune)
        rows.append((action, change.kind.value, change.uri, detail))

    terminal_width = max(80, shutil.get_terminal_size(fallback=(120, 24)).columns)
    action_width = max(len(row[0]) for row in rows)
    kind_width = max(len(row[1]) for row in rows)
    available_width = max(44, terminal_width - action_width - kind_width - 6)
    uri_width = max(24, int(available_width * 0.6))
    reason_width = max(20, available_width - uri_width)
    widths = (action_width, kind_width, uri_width, reason_width)

    output = []
    for index, row in enumerate(rows):
        wrapped_cells = []
        for column, cell in enumerate(row):
            wrapped_cells.append(
                textwrap.wrap(
                    str(cell),
                    width=widths[column],
                    break_long_words=True,
                    break_on_hyphens=False,
                )
                or [""]
            )
        row_height = max(len(cell) for cell in wrapped_cells)
        for line_index in range(row_height):
            output.append(
                "  ".join(
                    (cell[line_index] if line_index < len(cell) else "").ljust(widths[column])
                    for column, cell in enumerate(wrapped_cells)
                ).rstrip()
            )
        if index == 0:
            output.append("  ".join("-" * width for width in widths))
    return output


def _applied_change_display(
    action: ContextChangeAction,
    *,
    prune: bool,
) -> Tuple[str, str]:
    if action == ContextChangeAction.CREATE:
        return "CREATED", "created successfully"
    if action == ContextChangeAction.UPDATE:
        return "UPDATED", "updated successfully"
    if action == ContextChangeAction.DELETE:
        if prune:
            return "DELETED", "deleted successfully"
        return "PENDING DELETE", "run apply --prune to delete"
    if action == ContextChangeAction.UNCHANGED:
        return "UNCHANGED", "already matches declaration"
    return "CONFLICT", "not applied"


def _normalize_tags(tags: Iterable[str]) -> Tuple[str, ...]:
    values: Dict[str, str] = {}
    for raw_tag in tags:
        tag = str(raw_tag).strip().lower()
        if tag.count("=") != 1:
            raise ValueError(f"Context tag must use strict k=v format: {raw_tag}")
        key, value = tag.split("=", 1)
        if not key or not value:
            raise ValueError(f"Context tag key and value must be non-empty: {raw_tag}")
        if key in _RESERVED_TAG_KEYS:
            raise ValueError(f"Context tag key is reserved: {key}")
        values[key] = value
    return tuple(f"{key}={values[key]}" for key in sorted(values))


def _tags_by_key(tags: Iterable[Any]) -> Dict[str, str]:
    result = {}
    for raw_tag in tags:
        tag = str(raw_tag).strip().lower()
        if tag.count("=") == 1:
            key, value = tag.split("=", 1)
            result[key] = value
    return result


def _resource_spec(resource: LiveContext) -> Dict[str, Any]:
    return {
        "kind": resource.kind.value,
        "uri": resource.uri,
        "tool_uri": resource.tool_uri,
        "title": resource.title,
        "description": resource.description,
        "timeout_seconds": resource.timeout_seconds,
        "arguments": resource.arguments,
        "tags": list(resource.tags),
    }


def _fingerprint(resource: LiveContext) -> str:
    try:
        encoded = json.dumps(
            _resource_spec(resource),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Context resource is not JSON serializable: {resource.uri}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _managed_tags(owner_tag: str, resource: LiveContext) -> list[str]:
    return [
        *resource.tags,
        owner_tag,
        f"{_KIND_TAG_KEY}={resource.kind.value}",
        f"{_SPEC_TAG_KEY}={_fingerprint(resource)}",
    ]
