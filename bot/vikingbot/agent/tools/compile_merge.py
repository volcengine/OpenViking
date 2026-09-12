"""Persist Resource draft assignments and merge topics through bounded checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import re
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from typing import Any

import yaml

from openviking.core.namespace import relative_uri_path
from openviking.utils.path_safety import safe_join_viking_uri, sanitize_relative_viking_path
from vikingbot.agent.subagent import SubagentManager
from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.compile.models import (
    COMPILE_DRAFT_ROOT,
    COMPILE_OUTPUT_ROOT,
    COMPILE_STAGING_ROOT,
    CompileLimits,
)
from vikingbot.compile.renderer import (
    _split_frontmatter,
    validate_relative_file_path,
    validate_resource_file,
)
from vikingbot.compile.sources import split_source

# Model-facing lists use bounded pages; persisted merge state retains every record.
_REPORT_PAGE_SIZE = 20
_REPORT_MAX_PAGE_SIZE = 50
_REPORT_VIEWS = ("summary", "drafts", "unassigned", "groups", "group", "failures", "conflicts")


def _source_uris(payload: bytes) -> set[str]:
    """Read original source identities from Markdown frontmatter; reject missing evidence."""
    metadata, _ = _split_frontmatter(payload.decode("utf-8"))
    sources = metadata.get("sources")
    if (
        not isinstance(sources, list)
        or not sources
        or any(
            not isinstance(source, dict)
            or not isinstance(source.get("resource"), str)
            or not source["resource"].strip()
            for source in sources
        )
    ):
        raise ValueError("Knowledge drafts require nonempty, valid sources in frontmatter")
    return {source["resource"] for source in sources}


async def validate_merge_coverage(
    sandbox: Any,
    inputs: dict[str, str],
    input_outputs: dict[str, list[str]] | None,
    outputs: dict[str, str],
    *,
    output_payloads: dict[str, bytes] | None = None,
) -> None:
    """Require an output receipt and retained source citations for every assigned draft.

    Inputs map runtime draft IDs to workspace files; outputs map child-relative names
    to submitted workspace files. Receipts must use those exact IDs and output names.
    Final submission supplies output_payloads to check the exact publication snapshot.
    This checks routing and evidence retention, not semantic equivalence of every fact.
    """
    if not isinstance(input_outputs, dict) or set(input_outputs) != set(inputs):
        raise ValueError(
            "input_outputs must map every assigned draft ID exactly once to output paths"
        )
    if len({path.casefold() for path in outputs}) != len(outputs):
        raise ValueError("Merge output paths collide when case is ignored")
    output_sources = {}
    for path, workspace_path in outputs.items():
        payload = (
            output_payloads[path]
            if output_payloads is not None
            else await sandbox.read_file_bytes(workspace_path)
        )
        if not validate_resource_file(path, payload):
            raise ValueError(f"Merge output must be a knowledge page: {path}")
        output_sources[path] = _source_uris(payload)
    for draft_id, input_path in inputs.items():
        paths = input_outputs[draft_id]
        if (
            not isinstance(paths, list)
            or not paths
            or any(not isinstance(path, str) or path not in outputs for path in paths)
        ):
            raise ValueError(f"Draft {draft_id} must map to nonempty submitted output paths")
        sources = _source_uris(await sandbox.read_file_bytes(input_path))
        missing = sources - set().union(*(output_sources[path] for path in paths))
        if missing:
            raise ValueError(f"Draft {draft_id} loses source references: {sorted(missing)}")


class CompileMergeTool(Tool):
    """Persist topic assignments and execute bounded, sequential batches per topic.

    Assignment patches retain unrelated decisions. Each successful batch produces an
    immutable checkpoint and advances character offsets; failed batches advance nothing.
    Groups publish only after all assigned drafts and selected existing pages are covered.
    """

    name = "merge_compile_drafts"
    description = (
        "Patch topic groups by stable name; valid assignments are retained even when other IDs fail. "
        "Prefer reuse=true for independent single drafts needing no content or path changes. "
        "Use run=true after resolving unassigned IDs and conflicts. Runtime batches by actual text "
        "size, including previous results, and resumes failed groups from their last checkpoint. "
        "Replies contain counts, applied changes and bounded issue previews. Source collection "
        "supplies draft catalogs once. Omit arguments for a summary; use view for paginated "
        "drafts, unassigned drafts, groups, failures or conflicts. view=group requires group_name."
    )
    parameters = {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Stable canonical topic name.",
                        },
                        "task": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Deliverable purposes, included/excluded content, concrete revisions and necessary "
                                "format/metadata/citation/configuration requirements for this group. Merge agents "
                                "receive no Skill and cannot read it; provide concise rules, not 'follow the Skill' "
                                "or its full text. Avoid product-wide tasks. Omit to retain. "
                                "Supplying task without reuse disables reuse."
                            ),
                        },
                        "output_pages": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Expected target-relative page paths reserved to this group. Declare new pages before run; existing_pages also reserves paths. Adjust only within task scope, never into another group's reservation. Omit to retain.",
                        },
                        "draft_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Add or move these IDs to this group. Other assignments remain unchanged.",
                        },
                        "existing_pages": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Relevant existing target page URIs, loaded and budgeted by runtime; omit to retain.",
                        },
                        "reuse": {
                            "type": "boolean",
                            "description": (
                                "Prefer true for an independent single draft needing no content or path changes. "
                                "Use false for required revisions and describe them in task; do not set false mechanically. "
                                "New groups without task default to true. Supplying task without reuse disables it; "
                                "other omissions retain the saved choice. "
                                "Runtime reuses only one draft with no existing target pages and an absent destination; "
                                "output ownership and coverage checks still apply."
                            ),
                        },
                    },
                    "required": ["name", "draft_ids"],
                    "additionalProperties": False,
                },
            },
            "groups_file": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Task-relative JSON file containing the groups array; mutually exclusive with "
                    "groups. Runtime loads and validates it without returning the file contents."
                ),
            },
            "run": {
                "type": "boolean",
                "default": False,
                "description": "Execute the saved plan if all inputs have unambiguous owners; retry only unfinished batches.",
            },
            "view": {
                "type": "string",
                "enum": list(_REPORT_VIEWS),
                "default": "summary",
                "description": "Read-only detail query; do not combine with groups or run=true.",
            },
            "group_name": {
                "type": "string",
                "minLength": 1,
                "description": "Exact saved topic name, required only for view=group.",
            },
            "query": {
                "type": "string",
                "description": "Case-insensitive text filter on a detail view's metadata, applied before pagination; does not read bodies.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "Zero-based offset into the selected view; follow next_offset while state is unchanged.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _REPORT_MAX_PAGE_SIZE,
                "default": _REPORT_PAGE_SIZE,
                "description": "Maximum records in a detail page. Full state is saved at state_path.",
            },
        },
        "additionalProperties": False,
    }

    def __init__(
        self,
        manager: SubagentManager,
        limits: CompileLimits,
        source_count: int,
        *,
        target_uri: str,
        load_existing: Callable[[str], Awaitable[bytes | None]],
    ):
        self.manager = manager
        # This bounds the entire batch assignment, including checkpoint bodies and JSON.
        self.input_chars = min(
            limits.merge_input_chars, limits.initial_prompt_chars, limits.agent_context_chars
        )
        self.target_uri = target_uri.rstrip("/")
        self.load_existing = load_existing
        self.source_count = source_count
        self.sources: set[int] = set()
        self.drafts: dict[str, dict[str, Any]] = {}
        self.assignments: dict[str, str] = {}
        self.conflicts: dict[str, list[str]] = {}
        # Groups keep input paths, next character offsets, checkpoint paths and coverage receipts.
        self.groups: dict[str, dict[str, Any]] = {}
        self.completed_drafts: set[str] = set()
        self.required_outputs: set[str] = set()
        self.output_owners: dict[str, str] = {}
        self.active = False
        self.state_path = f"{COMPILE_STAGING_ROOT}/merge-state.json"
        self._save_lock = asyncio.Lock()
        self._texts: dict[str, str] = {}

    def submission_error(self) -> str | None:
        """Block publication until all sources and all assigned groups complete."""
        missing = sorted(set(range(1, self.source_count + 1)) - self.sources)
        if missing:
            return f"Error: source batches have not submitted successfully: {missing}"
        pending = self.drafts.keys() - self.completed_drafts
        if self.active or pending or self.conflicts:
            return f"Error: merge coverage incomplete ({len(pending)} pending drafts); inspect merge_compile_drafts."
        return None

    def _report(self) -> dict[str, Any]:
        """Return complete persistence and coverage state, including internal checkpoints."""
        return {
            "drafts": self.drafts,
            "assignments": self.assignments,
            "unassigned_draft_ids": sorted(self.drafts.keys() - self.assignments.keys()),
            "conflicts": self.conflicts,
            "missing_source_batches": sorted(set(range(1, self.source_count + 1)) - self.sources),
            "pending_draft_ids": sorted(self.drafts.keys() - self.completed_drafts),
            "groups": self.groups,
            "final_output_directory": COMPILE_OUTPUT_ROOT,
            "state_path": self.state_path,
        }

    def _feedback(
        self,
        *,
        changed_assignments: dict[str, str] | None = None,
        changed_groups: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> dict[str, Any]:
        """Describe applied plan changes without replaying catalogs or saved task text.

        Counts cover the full state; conflicts and failed_groups preview at most one
        default page each. Their named views expose the remaining records. Errors
        describe this invocation, and changed_groups identifies saved plan edits.
        """
        statuses = Counter(group["status"] for group in self.groups.values())
        failures = {
            name: group.get("error") or "Merge interrupted; resume with run=true."
            for name, group in sorted(self.groups.items())
            if group.get("error") or group["status"] in {"failed", "interrupted"}
        }
        return {
            "counts": {
                "drafts": len(self.drafts),
                "assigned": len(self.assignments),
                "unassigned": len(self.drafts.keys() - self.assignments.keys()),
                "pending_merge": len(self.drafts.keys() - self.completed_drafts),
                "missing_source_batches": self.source_count - len(self.sources),
                "groups": len(self.groups),
                "groups_by_status": dict(statuses),
                "conflicts": len(self.conflicts),
                "failed_groups": len(failures),
                "output_pages": len(self.required_outputs),
            },
            "changed_assignments": changed_assignments or {},
            "changed_groups": changed_groups or [],
            "conflicts": dict(sorted(self.conflicts.items())[:_REPORT_PAGE_SIZE]),
            "failed_groups": dict(list(failures.items())[:_REPORT_PAGE_SIZE]),
            "errors": errors or [],
            "state_path": self.state_path,
            "final_output_directory": COMPILE_OUTPUT_ROOT,
        }

    def _inspect(
        self, view: str, group_name: str | None, offset: int, limit: int, query: str = ""
    ) -> dict[str, Any]:
        """Read one page from current state without mutating assignments or loading bodies.

        Drafts sort by ID and groups by name. A group page includes its plan and a
        paginated member inventory, with processed_chars measured in Unicode code
        points. Offsets refer to the current filtered view, not a frozen snapshot.
        """
        reply = self._feedback()
        if view == "summary":
            return reply
        if view in {"drafts", "unassigned", "group"}:
            keys = sorted(self.drafts)
            group = None
            if view == "unassigned":
                keys = [key for key in keys if key not in self.assignments]
            elif view == "group":
                if group_name not in self.groups:
                    raise ValueError(f"Unknown group: {group_name}")
                group = self.groups[group_name]
                keys = [key for key in keys if self.assignments.get(key) == group_name]
                reply["group"] = {
                    key: group[key]
                    for key in ("name", "task", "output_pages", "reuse", "status", "batches")
                }
            items = [
                {"id": key, **self.drafts[key], "group": self.assignments.get(key)} for key in keys
            ]
            if group is not None:
                for item in items:
                    item["kind"] = "draft"
                    item["processed_chars"] = group["progress"].get(item["id"], 0)
                existing_uris = set(group["existing_pages"]) | {
                    safe_join_viking_uri(self.target_uri, path)
                    for path in group["existing_paths"].values()
                }
                for uri in sorted(existing_uris):
                    key = "existing-" + hashlib.sha256(uri.encode()).hexdigest()[:16]
                    items.append(
                        {
                            "kind": "existing",
                            "uri": uri,
                            "processed_chars": group["progress"].get(key, 0),
                        }
                    )
        elif view in {"groups", "failures"}:
            members = Counter(self.assignments.values())
            items = [
                {
                    "name": name,
                    "status": group["status"],
                    "draft_count": members[name],
                    "batches": group["batches"],
                    "output_pages": len(group["checkpoints"]),
                    "error": group.get("error"),
                }
                for name, group in sorted(self.groups.items())
                if view == "groups"
                or group.get("error")
                or group["status"] in {"failed", "interrupted"}
            ]
        else:
            items = [
                {"draft_id": key, "groups": owners}
                for key, owners in sorted(self.conflicts.items())
            ]
        if query:
            items = [
                item
                for item in items
                if query.casefold() in json.dumps(item, ensure_ascii=False).casefold()
            ]
        reply.update(
            view=view,
            items=items[offset : offset + limit],
            total=len(items),
            next_offset=offset + limit if offset + limit < len(items) else None,
        )
        return reply

    async def _save(self, sandbox: Any) -> None:
        """Serialize the latest assignments and checkpoints without concurrent file writes."""
        async with self._save_lock:
            await sandbox.write_file_bytes(
                self.state_path, json.dumps(self._report(), ensure_ascii=False, indent=2).encode()
            )

    async def _text(self, sandbox: Any, path: str) -> str:
        """Cache immutable source and child checkpoint files, measured in Unicode characters."""
        if path not in self._texts:
            self._texts[path] = (await sandbox.read_file_bytes(path)).decode("utf-8")
        return self._texts[path]

    async def add_source(self, sandbox: Any, batch: int, result: dict[str, Any]) -> dict[str, Any]:
        """Register validated content pages with stable IDs and their actual character counts."""
        drafts = {}
        for index, path in enumerate(sorted(result["files"]), 1):
            # Unsubmitted source files can be repaired in place between attempts.
            self._texts.pop(path, None)
            text = await self._text(sandbox, path)
            relative = validate_relative_file_path(posixpath.relpath(path, result["draft_root"]))
            if not validate_resource_file(relative, text.encode()):
                raise ValueError(f"Source draft is not a knowledge page: {path}")
            _source_uris(text.encode())
            metadata, body = _split_frontmatter(text)
            tags = metadata.get("tags")
            drafts[f"s{batch}d{index}"] = {
                "path": path,
                "relative_path": relative,
                "chars": len(text),
                "size_bytes": result["file_sizes"][path],
                # Bounded planning hints reuse validated text; deeper inspection is on demand.
                **{
                    field: str(metadata.get(field) or "")[:cap]
                    for field, cap in (("title", 160), ("description", 320), ("type", 80))
                },
                "tags": [str(tag)[:48] for tag in tags[:8]] if isinstance(tags, list) else [],
                "headings": [
                    heading[:120]
                    for heading in re.findall(r"(?m)^ {0,3}#{1,6}[ \t]+[^\r\n]+", body[:4096])[:6]
                ],
            }
        self.drafts.update(drafts)
        self.sources.add(batch)
        try:
            await self._save(sandbox)
        except BaseException:
            self.sources.discard(batch)
            for key in drafts:
                self.drafts.pop(key, None)
            raise
        return {"source_batch": batch, "drafts": drafts, "summary": result["summary"]}

    def _patch(self, updates: list[dict[str, Any]]) -> list[str]:
        """Apply independent valid assignments; retain cross-group conflicts until explicitly resolved.

        Repeating an ID in one group is idempotent. A single owner in a later patch
        resolves a conflict or moves an untouched draft; consumed inputs cannot move.
        """
        errors, proposals = [], {}
        for update in updates:
            name = update["name"]
            group = self.groups.setdefault(
                name,
                {
                    "name": name,
                    "task": f"Merge topic: {name}",
                    "existing_pages": [],
                    # Optional page reservations; task defines the semantic content boundary.
                    "output_pages": [],
                    "reuse": "task" not in update,
                    "status": "planned",
                    "inputs": {},
                    "existing_paths": {},
                    "progress": {},
                    "checkpoints": {},
                    "input_outputs": {},
                    "batches": 0,
                },
            )
            # Custom instructions require agent work unless reuse is explicitly allowed.
            if "task" in update and "reuse" not in update:
                update = {**update, "reuse": False}
            for field in ("task", "reuse", "existing_pages", "output_pages"):
                if field not in update:
                    continue
                value = (
                    list(dict.fromkeys(update[field]))
                    if field in {"existing_pages", "output_pages"}
                    else update[field]
                )
                if field == "output_pages":
                    try:
                        value = [validate_relative_file_path(path) for path in value]
                    except ValueError as exc:
                        errors.append(f"Group {name}: {exc}")
                        continue
                if (group["batches"] or group["status"] == "completed") and value != group[field]:
                    errors.append(f"Group {name} has checkpoints; its {field} is fixed.")
                else:
                    group[field] = value
            for key in set(update["draft_ids"]):
                if key not in self.drafts:
                    errors.append(f"Unknown draft ID: {key}")
                else:
                    proposals.setdefault(key, set()).add(name)
        for key, names in proposals.items():
            owner = self.assignments.get(key)
            if len(names) > 1:
                self.conflicts[key] = sorted(names)
                continue
            name = next(iter(names))
            if self.groups[name]["status"] == "completed" and owner != name:
                errors.append(f"Group {name} is completed; use an unfinished group.")
                continue
            if owner and owner != name:
                previous = self.groups[owner]
                if previous["progress"].get(key, 0) or key in self.completed_drafts:
                    errors.append(f"Draft {key} has processed content in {owner}; keep that owner.")
                    continue
                previous["inputs"].pop(key, None)
            self.assignments[key] = name
            self.conflicts.pop(key, None)
        return errors

    async def _prepare(self, sandbox: Any, group: dict[str, Any]) -> None:
        """Snapshot selected targets before batching; eligible reusable singletons bypass LLM work."""
        keys = [key for key, name in self.assignments.items() if name == group["name"]]
        if group["batches"]:
            group["inputs"].update({key: self.drafts[key]["path"] for key in keys})
            return
        group.update(inputs={}, existing_paths={}, progress={}, checkpoints={}, input_outputs={})
        # Exact destination lookup prevents the reuse path from overwriting an existing page.
        uris = list(group["existing_pages"])
        if group["reuse"] and len(keys) == 1:
            uri = safe_join_viking_uri(self.target_uri, self.drafts[keys[0]]["relative_path"])
            if uri not in uris:
                payload = await self.load_existing(uri)
                if payload is not None:
                    uris.append(uri)
        for uri in uris:
            relative = relative_uri_path(self.target_uri, uri)
            if not relative or posixpath.basename(relative).casefold() in {
                "index.md",
                "_index.md",
                "_index",
            }:
                raise ValueError(f"Existing page must be a content page inside the target: {uri}")
            key = "existing-" + hashlib.sha256(uri.encode()).hexdigest()[:16]
            if key not in group["inputs"]:
                payload = await self.load_existing(uri)
                if payload is None or not validate_resource_file(relative, payload):
                    raise ValueError(f"Existing page is missing or invalid: {uri}")
                _source_uris(payload)
                path = f"{COMPILE_DRAFT_ROOT}/_merge_existing/{key}.md"
                await sandbox.write_file_bytes(path, payload)
                self._texts[path] = payload.decode("utf-8")
                group["inputs"][key] = path
                group["existing_paths"][key] = relative
        group["inputs"].update({key: self.drafts[key]["path"] for key in keys})
        if (
            group["reuse"]
            and len(keys) == 1
            and not group["existing_paths"]
            and (
                not group["output_pages"]
                or group["output_pages"] == [self.drafts[keys[0]]["relative_path"]]
            )
        ):
            key = keys[0]
            relative = self.drafts[key]["relative_path"]
            group["checkpoints"] = {relative: self.drafts[key]["path"]}
            group["input_outputs"] = {key: [relative]}
            group["progress"][key] = self.drafts[key]["chars"]

    async def _assignment(self, sandbox: Any, group: dict[str, Any]) -> str | None:
        """Build one bounded job from complete checkpoints plus exact, nonoverlapping input ranges.

        New text uses at most half the assignment budget. Checkpoints count against
        the same hard bound; when no further range fits, keep progress and report the
        topic as too large instead of handing an unbounded read task to a child.
        """
        task = group["task"] + (
            "\nUpdate the complete checkpoint pages using only the attached input ranges. "
            "Preserve earlier facts, conditions, exceptions and sources. Emit complete updated pages. "
            "Do not reread full source/target files or inspect unassigned topics. Each input range and "
            "checkpoint ID needs an input_outputs receipt. Character offsets are half-open; continuation "
            "flags identify partial lines. Later batches supply the remaining text; do not invent it.\n"
        )
        task += "output_pages: " + json.dumps(group["output_pages"], ensure_ascii=False) + "\n"
        records, bindings, parts = [], {}, {}
        for relative, path in group["checkpoints"].items():
            key = "checkpoint:" + relative
            records.append(
                {"id": key, "output_path": relative, "content": await self._text(sandbox, path)}
            )
            bindings[key] = path
        available = min(
            self.input_chars // 2,
            self.input_chars - len(task) - len(json.dumps(records, ensure_ascii=False)),
        )
        unfinished = False
        for key, path in group["inputs"].items():
            text = await self._text(sandbox, path)
            start = group["progress"].get(key, 0)
            if start >= len(text):
                continue
            unfinished = True
            cap = max(1, available // 2)
            minimum = min(512, len(text) - start)
            while cap >= minimum and available > 0:
                # A bounded window avoids splitting an entire large document into tiny ranges.
                part = split_source(key, text[start : start + 2 * cap], cap)[0]
                end = start + part.end_char
                part_id = f"{key}@{start}:{end}"
                _, body = _split_frontmatter(text)
                record = {
                    "id": part_id,
                    "origin": key,
                    "range": [start, end],
                    "frontmatter": text[: len(text) - len(body)],
                    "context": part.context,
                    "content": text[start:end],
                    "continues_before": start > 0 and text[start - 1] not in "\r\n",
                    "continues_after": part.continues_after,
                    "output_path": group["existing_paths"].get(key),
                }
                cost = len(json.dumps(record, ensure_ascii=False)) + 2
                if cost <= available:
                    records.append(record)
                    bindings[part_id] = path
                    parts[part_id] = {"origin": key, "start": start, "end": end}
                    available -= cost
                    break
                cap //= 2
        if not unfinished:
            return None
        if not parts:
            raise ValueError(
                f"Topic checkpoint leaves no useful input capacity within {self.input_chars} characters. Group stopped; saved progress is retained."
            )
        assignment = task + json.dumps(records, ensure_ascii=False)
        assert len(assignment) <= self.input_chars
        group["batch"] = {"inputs": bindings, "parts": parts, "input_chars": len(assignment)}
        return json.dumps({"task": assignment, "draft_inputs": bindings}, ensure_ascii=False)

    async def _collect(self, sandbox: Any, group: dict[str, Any], result: dict[str, Any]) -> None:
        """Validate a batch and compose its receipts with earlier coverage before advancing offsets."""
        if result["status"] != "completed":
            raise ValueError(result.get("error") or result["status"])
        outputs = {
            validate_relative_file_path(posixpath.relpath(path, result["draft_root"])): path
            for path in result["files"]
        }
        batch = group["batch"]
        receipt = result.get("input_outputs")
        await validate_merge_coverage(sandbox, batch["inputs"], receipt, outputs)
        covered_existing = {
            path
            for key, path in group["existing_paths"].items()
            if group["progress"].get(key, 0)
            or any(part["origin"] == key for part in batch["parts"].values())
        }
        if not covered_existing <= outputs.keys():
            raise ValueError(
                f"Existing target paths must be retained: {sorted(covered_existing - outputs.keys())}"
            )
        composed = {
            key: sorted({new for old in paths for new in receipt["checkpoint:" + old]})
            for key, paths in group["input_outputs"].items()
        }
        for part_id, part in batch["parts"].items():
            key = part["origin"]
            composed[key] = sorted(set(composed.get(key, [])) | set(receipt[part_id]))
        group["input_outputs"] = composed
        group["checkpoints"] = outputs
        for part in batch["parts"].values():
            group["progress"][part["origin"]] = part["end"]
        group["batches"] += 1
        group["status"] = "pending"
        group.pop("error", None)

    def _output_paths(self, group: dict[str, Any]) -> set[str]:
        """Return reserved and generated target-relative paths, including selected updates."""
        paths = set(group["output_pages"]) | set(group["checkpoints"])
        paths.update(relative_uri_path(self.target_uri, uri) for uri in group["existing_pages"])
        keys = [key for key, name in self.assignments.items() if name == group["name"]]
        if group["reuse"] and len(keys) == 1:
            paths.add(self.drafts[keys[0]]["relative_path"])
        return paths - {""}

    def _collision(self, path: str, name: str, owner: str) -> str:
        """Describe both owners and retained results; checkpointed plans cannot be reassigned."""
        locations = {
            label: {
                page: file
                for page, file in self.groups[label]["checkpoints"].items()
                if page.casefold() == path.casefold()
            }
            for label in (name, owner)
        }
        return (
            f"Output collision: {path}; groups/checkpoints (empty = not generated): {locations}. "
            "Patch untouched groups to coordinate ownership or merge inputs; processed inputs and "
            "checkpointed plans are fixed. Retain drafts/checkpoints; do not regenerate unchanged "
            "work, add filename suffixes, or edit merge-state.json."
        )

    async def _publish(self, sandbox: Any, group: dict[str, Any]) -> None:
        """Copy a fully covered group's last checkpoint, rejecting destination collisions."""
        outputs = group["checkpoints"]
        await validate_merge_coverage(sandbox, group["inputs"], group["input_outputs"], outputs)
        for path in outputs:
            owner = self.output_owners.get(path.casefold())
            if owner is None:
                for name in dict.fromkeys(self.assignments.values()):
                    if name != group["name"] and path.casefold() in {
                        p.casefold() for p in self._output_paths(self.groups[name])
                    }:
                        owner = name
                        break
            if owner is not None and owner != group["name"]:
                raise ValueError(self._collision(path, group["name"], owner))
        for relative, path in outputs.items():
            await sandbox.write_file_bytes(
                f"{COMPILE_OUTPUT_ROOT}/{relative}", await sandbox.read_file_bytes(path)
            )
        self.output_owners.update({path.casefold(): group["name"] for path in outputs})
        self.required_outputs.update(outputs)
        self.completed_drafts.update(key for key in group["inputs"] if key in self.drafts)
        group["status"] = "completed"
        group.pop("error", None)

    async def validate_final(self, sandbox: Any, files: dict[str, bytes]) -> None:
        """Recheck final inventory and transitive receipts against the exact publication snapshot."""
        knowledge = {
            path
            for path in files
            if path.casefold().endswith(".md")
            and posixpath.basename(path).casefold() not in {"index.md", "_index.md"}
        }
        if knowledge != self.required_outputs:
            raise ValueError(
                f"Final merge inventory mismatch: missing {sorted(self.required_outputs - knowledge)}, uncollected {sorted(knowledge - self.required_outputs)}"
            )
        for group in self.groups.values():
            if group["status"] == "completed":
                await validate_merge_coverage(
                    sandbox,
                    group["inputs"],
                    group["input_outputs"],
                    group["checkpoints"],
                    output_payloads=files,
                )

    async def execute(
        self,
        tool_context: ToolContext,
        groups: list[dict[str, Any]] | None = None,
        run: bool = False,
        view: str = "summary",
        group_name: str | None = None,
        offset: int = 0,
        limit: int = _REPORT_PAGE_SIZE,
        groups_file: str | None = None,
        query: str = "",
        **kwargs: Any,
    ) -> str:
        """Persist local plan repairs, then run complete plans through the existing bounded queue.

        groups_file loads the same array as groups from a task-relative JSON file.
        File parameters use the same validation as inline groups before any mutation.
        The tool registry validates field types and ranges; query combinations are
        checked here. Detail queries cannot accompany mutations. A failed batch
        pauses only its group; run=true resumes saved offsets. Replies report
        applied changes and issues; persisted state and coverage remain complete.
        """
        if groups_file is not None:
            try:
                if groups is not None:
                    raise ValueError("Use either groups or groups_file, not both.")
                path = sanitize_relative_viking_path(groups_file)
                sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
                groups = json.loads((await sandbox.read_file_bytes(path)).decode("utf-8"))
                errors = self.validate_params({"groups": groups})
                if errors:
                    return json.dumps(self._feedback(errors=errors), ensure_ascii=False)
            except (OSError, ValueError) as exc:
                return json.dumps(self._feedback(errors=[str(exc)]), ensure_ascii=False)
        query_error = None
        if (view == "group") != (group_name is not None):
            query_error = "group_name is required only for view=group."
        elif query and view == "summary":
            query_error = "query requires a detail view."
        elif (groups is not None or run) and (
            view != "summary" or group_name is not None or offset or limit != _REPORT_PAGE_SIZE
        ):
            query_error = "Detail queries cannot be combined with groups or run=true."
        if query_error:
            return json.dumps(self._feedback(errors=[query_error]), ensure_ascii=False)
        if groups is None and not run:
            try:
                reply = self._inspect(view, group_name, offset, limit, query)
            except ValueError as exc:
                reply = self._feedback(errors=[str(exc)])
            return json.dumps(reply, ensure_ascii=False)
        if self.active:
            error = "A merge execution is active."
        elif len(self.sources) != self.source_count:
            error = self.submission_error() or "Collect every source result first."
        else:
            error = self.manager.begin_submission(False)
        if error:
            return json.dumps(self._feedback(errors=[error]), ensure_ascii=False)
        self.active = True
        sandbox = None
        previous_assignments = dict(self.assignments)
        previous_conflicts = set(self.conflicts)
        previous_groups = {
            name: (group["task"], group["reuse"], group["existing_pages"], group["output_pages"])
            for name, group in self.groups.items()
        }
        try:
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            errors = self._patch(groups or [])
            await self._save(sandbox)
            changes = {
                "changed_assignments": {
                    key: name
                    for key, name in self.assignments.items()
                    if key not in self.conflicts
                    and (previous_assignments.get(key) != name or key in previous_conflicts)
                },
                "changed_groups": [
                    name
                    for name, group in self.groups.items()
                    if previous_groups.get(name)
                    != (
                        group["task"],
                        group["reuse"],
                        group["existing_pages"],
                        group["output_pages"],
                    )
                ],
            }
            if errors or not run or self.conflicts or self.drafts.keys() - self.assignments.keys():
                return json.dumps(self._feedback(**changes, errors=errors), ensure_ascii=False)
            ready = deque(
                name
                for name in dict.fromkeys(self.assignments.values())
                if self.groups[name]["status"] != "completed"
            )
            owners = dict(self.output_owners)
            for name in dict.fromkeys(self.assignments.values()):
                for path in sorted(self._output_paths(self.groups[name])):
                    owner = owners.get(path.casefold())
                    if owner is not None and owner != name:
                        errors.append(self._collision(path, name, owner))
                    owners[path.casefold()] = name
            if errors:
                return json.dumps(self._feedback(**changes, errors=errors), ensure_ascii=False)
            prepared = deque()
            for name in ready:
                group = self.groups[name]
                try:
                    await self._prepare(sandbox, group)
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    group.update(status="failed", error=str(exc))
                    continue
                prepared.append(name)
            ready = prepared
            report = await self.manager.wait(block=False)
            while ready or report["running"] or report["queued"] or report["results"]:
                for result in report["results"]:
                    group = self.groups[result["label"]]
                    try:
                        await self._collect(sandbox, group, result)
                        ready.append(group["name"])
                    except (OSError, ValueError, yaml.YAMLError) as exc:
                        group.update(status="failed", error=str(exc))
                    await self._save(sandbox)
                capacity = report["queue_capacity"]
                while ready and capacity:
                    group = self.groups[ready.popleft()]
                    try:
                        assignment = await self._assignment(sandbox, group)
                        if assignment is None:
                            await self._publish(sandbox, group)
                        else:
                            response = await self.manager.spawn(
                                task=assignment,
                                session_key=tool_context.session_key,
                                label=group["name"],
                            )
                            if response.startswith("Error:"):
                                raise ValueError(response)
                            group["status"] = "running"
                            capacity -= 1
                    except (OSError, ValueError, yaml.YAMLError) as exc:
                        group.update(status="failed", error=str(exc))
                    await self._save(sandbox)
                report = await self.manager.wait(block=True)
        finally:
            for group in self.groups.values():
                if group["status"] == "running":
                    group["status"] = "interrupted"
            try:
                if sandbox is not None:
                    await self._save(sandbox)
            finally:
                self.active = False
        return json.dumps(self._feedback(**changes), ensure_ascii=False)
