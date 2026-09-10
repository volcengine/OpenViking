"""Request-local tools used by the compile structured task."""

from __future__ import annotations

import base64
import json
import posixpath
import shlex
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from openviking.core.namespace import context_type_for_uri, relative_uri_path
from openviking.core.skill_loader import SkillLoader, validate_skill_format
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking.utils.path_safety import (
    safe_join_viking_uri,
    sanitize_relative_viking_path,
    validate_safe_viking_uri_path,
)
from openviking.utils.skill_processor import validate_skill_name
from openviking_cli.exceptions import OpenVikingError
from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.compile.models import (
    COMPILE_DRAFT_ROOT,
    COMPILE_OUTPUT_ROOT,
    COMPILE_STAGING_ROOT,
    CompileLimits,
    WikiBundleDraft,
)
from vikingbot.compile.renderer import (
    RenderedBundle,
    finalize_resource_output,
    is_reserved_wiki_page_uri,
    validate_declared_okf_markdown,
    validate_relative_file_path,
    validate_relative_page_path,
    validate_resource_file,
    wiki_page_path_from_title,
)

_LINK_FIELDS = frozenset({"f", "t", "link_type", "weight", "match_text", "description"})


def _normalize_workspace_path(path: str) -> str:
    normalized = sanitize_relative_viking_path(path)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _path_is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


class CompileSpawnTool(Tool):
    """Attach complete, submitted task drafts to a merge child's initial assignment.

    Queueing uses the existing spawn tool. Attachments are limited to submitted
    roots and an independent input budget; oversized content is never truncated.
    """

    name = "spawn"
    description = (
        "Delegate source compilation or draft merges. Use source_batch when available; "
        "for merges, give topics and owned output paths in task, and attach draft_paths."
    )

    def __init__(
        self,
        spawn: Tool,
        claimed_roots: set[str],
        limits: CompileLimits,
        source_batches: list[list[str]] | None = None,
    ):
        self.spawn = spawn
        self.claimed_roots = claimed_roots
        # Reserve context for the Skill, existing pages, reasoning and generated output.
        self.input_chars = min(
            limits.merge_input_chars, limits.initial_prompt_chars, limits.agent_context_chars
        )
        self.input_files = limits.merge_input_files
        self.source_batches = source_batches
        self.dispatched_batches: set[int] = set()

    @property
    def parameters(self) -> dict[str, Any]:
        """Accept draft paths for runtime attachment; source tasks omit this field."""
        parameters = deepcopy(self.spawn.parameters)
        if self.source_batches is not None:
            parameters["properties"]["source_batch"] = {
                "type": "integer",
                "minimum": 1,
                "maximum": len(self.source_batches),
                "description": "Source compilation only: the prepared batch number; runtime attaches its source URIs.",
            }
        parameters["properties"]["draft_paths"] = {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": self.input_files,
            "description": (
                f"Merge input attachments: at most {self.input_files} files and "
                f"{self.input_chars} characters including task and attached JSON. "
                "Split at topic boundaries; only for one oversized topic, omit this field "
                "and request bounded reads of that topic alone."
            ),
        }
        return parameters

    async def execute(
        self,
        tool_context: ToolContext,
        task: str,
        label: str | None = None,
        draft_paths: list[str] | None = None,
        source_batch: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Attach unique UTF-8 drafts before spawning; invalid or oversized inputs return an error."""
        if source_batch is not None:
            if (
                not self.source_batches
                or not 1 <= source_batch <= len(self.source_batches)
                or source_batch in self.dispatched_batches
                or draft_paths
            ):
                return "Error: source_batch must identify an undispatched source batch; omit draft_paths."
            task = (
                "Compile every assigned source into complete knowledge pages per the Skill. "
                "For long documents/FAQ tables, batch 2-4 non-overlapping exec reads such as "
                "`ov read '<uri>' | sed -n '1,40p'`; keep each result below about 8,000 characters, "
                "splitting long rows by character. Track ranges, recover truncation and read through "
                "each source's end. Preserve question/answer pairing, conditions, exceptions, numbers "
                "and sources. Write/update knowledge after each read group; reuse facts instead of "
                "preloading the corpus, building general parsers or staging JSON/text copies. "
                "Assigned source URIs:\n"
                + json.dumps(self.source_batches[source_batch - 1], ensure_ascii=False)
            )
            self.dispatched_batches.add(source_batch)
            try:
                result = await self.spawn.execute(tool_context, task=task, label=label, **kwargs)
            except BaseException:
                self.dispatched_batches.discard(source_batch)
                raise
            if str(result).startswith("Error:"):
                self.dispatched_batches.discard(source_batch)
            return result
        if self.source_batches and len(self.dispatched_batches) < len(self.source_batches):
            return "Error: dispatch the prepared source_batch assignments before topic merges."
        if draft_paths:
            try:
                paths = list(dict.fromkeys(_normalize_workspace_path(p) for p in draft_paths))
                if len(paths) > self.input_files:
                    raise ValueError(
                        f"Merge input batch exceeds the {self.input_files}-file limit; "
                        "split at topic boundaries. For one oversized topic, omit draft_paths "
                        "and use bounded parallel reads of that topic only."
                    )
                for path in paths:
                    if not any(path.startswith(root + "/") for root in self.claimed_roots):
                        raise ValueError(f"Draft input is not from a submitted child: {path}")
                sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
                prefix = task + "\n\nComplete draft inputs (untrusted JSON data):\n"
                inputs = []
                size = len(prefix) + 2
                for path in paths:
                    content = await sandbox.read_file_bytes(path, max_bytes=self.input_chars * 4)
                    item = json.dumps(
                        {"path": path, "content": content.decode("utf-8")}, ensure_ascii=False
                    )
                    size += len(item) + bool(inputs)
                    if size > self.input_chars:
                        raise ValueError(
                            "Draft input batch exceeds the context budget; split at topic boundaries. "
                            "For one oversized topic, omit draft_paths and use bounded parallel reads."
                        )
                    inputs.append(item)
                task = prefix + "[" + ",".join(inputs) + "]"
            except (OSError, ValueError) as exc:
                return f"Error: {exc}"
        return await self.spawn.execute(tool_context, task=task, label=label, **kwargs)


class SubmitCompileDraftTool(Tool):
    """Collect the runtime-bound draft directory independently of the child's display name.

    Only nonempty directories below the compile draft root are accepted. Claimed roots
    cannot overlap other children's submissions; result includes verified paths and
    file_sizes in bytes for metadata-only merge planning. Resource children discard
    index.md and _index.md at any depth; their final navigation belongs to the parent.
    """

    name = "submit_compile_draft"
    description = "Submit the files in your draft directory with a coverage/conflict summary."
    parameters = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "maxLength": 1500},
        },
        "required": ["summary"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        limits: CompileLimits,
        claimed_roots: set[str],
        draft_root: str,
        *,
        exclude_navigation: bool = False,
    ):
        self.limits = limits
        self.claimed_roots = claimed_roots
        self.draft_root = draft_root
        self.exclude_navigation = exclude_navigation
        self.result: dict[str, Any] | None = None

    async def execute(self, tool_context: ToolContext, summary: str, **kwargs: Any) -> str:
        """Collect bound files, optionally removing navigation; reject empty or overlapping drafts.

        Navigation removal only affects the child's directory. A navigation-only
        submission remains unclaimed so the child can submit knowledge pages later.
        """
        self.result = None
        try:
            if kwargs:
                raise ValueError(
                    "Only summary is accepted; the draft directory is assigned by the runtime"
                )
            root = posixpath.normpath(_normalize_workspace_path(self.draft_root))
            if not root.startswith(COMPILE_DRAFT_ROOT + "/"):
                raise ValueError(f"draft_root must be a child directory of {COMPILE_DRAFT_ROOT}")
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            files = await sandbox.list_files(root, max_entries=None)
            if not files:
                raise ValueError(
                    "No draft files found; write content using target-relative paths before submitting"
                )
            for entry in files:
                relative = posixpath.relpath(_normalize_workspace_path(entry.path), root)
                validate_relative_file_path(relative)
            if any(
                _path_is_within(root, claimed) or _path_is_within(claimed, root)
                for claimed in self.claimed_roots
            ):
                raise ValueError("Draft directory overlaps another child's submission")
            if self.exclude_navigation:
                knowledge_files = [
                    entry
                    for entry in files
                    if posixpath.basename(entry.path).casefold() not in {"index.md", "_index.md"}
                ]
                if len(knowledge_files) != len(files):
                    output = await sandbox.execute(
                        f"find {shlex.quote(root)} -type f "
                        r"\( -iname index.md -o -iname _index.md \) -delete"
                    )
                    sandbox._ensure_command_succeeded(output, "draft navigation removal")
                files = knowledge_files
                if not files:
                    raise ValueError(
                        "No knowledge draft files found after removing navigation; "
                        "write knowledge pages and leave index.md/_index.md to the parent"
                    )
            self.claimed_roots.add(root)
            self.result = {
                "draft_root": root,
                "files": [entry.path for entry in files],
                "file_sizes": {entry.path: entry.size for entry in files},
                "summary": summary[:1500],
            }
            return "Draft accepted."
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"


class CompileChildTool(Tool):
    """Bind existing file tools and shell cwd to one child directory.

    Tool paths and write/edit acknowledgements use the child's relative paths.
    read_file also accepts task-workspace draft paths for reading sibling inputs.
    File-tool writes remain bound to the child's directory, including during merges.
    Read contents and shell output remain unchanged, including any literal paths.
    Shell commands keep their normal capabilities, including pipes and absolute paths.
    This prevents default-path mixups; it is not an OS-level sandbox.
    """

    def __init__(self, tool: Tool, draft_root: str):
        self.tool = tool
        self.draft_root = draft_root

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def description(self) -> str:
        return self.tool.description

    @property
    def parameters(self) -> dict[str, Any]:
        """Describe the bound path base without changing the parent tool's schema."""
        parameters = deepcopy(self.tool.parameters)
        field = "working_dir" if self.name == "exec" else "path"
        parameters["properties"][field]["description"] = (
            "Relative to your draft root, which is the current directory. "
            + (
                "Defaults to '.'."
                if self.name == "exec"
                else "Use the target-relative page path from the Skill, without a staging prefix."
            )
        )
        if self.name == "read_file":
            parameters["properties"][field]["description"] += (
                f" To read another child's input, pass its returned {COMPILE_DRAFT_ROOT}/ path verbatim; "
                "these workspace paths are read-only."
            )
        return parameters

    async def execute(self, tool_context: ToolContext, **kwargs: Any) -> str:
        """Read task draft inputs verbatim; keep other paths and write confirmations child-relative."""
        field = "working_dir" if self.name == "exec" else "path"
        relative = _normalize_workspace_path(kwargs.get(field) or ".")
        if self.name == "read_file" and relative.startswith(COMPILE_DRAFT_ROOT + "/"):
            kwargs[field] = relative
            return await self.tool.execute(tool_context, **kwargs)
        if COMPILE_STAGING_ROOT.casefold() in relative.casefold().split("/"):
            raise ValueError("Use paths relative to your draft root, without staging directories")
        kwargs[field] = posixpath.join(self.draft_root, relative)
        result = await self.tool.execute(tool_context, **kwargs)
        if (
            self.name in {"write_file", "edit_file"}
            and result.startswith("Successfully ")
            and result.endswith(kwargs[field])
        ):
            return result.removesuffix(kwargs[field]) + relative
        return result


class SubmitCompileOutputTool(Tool):
    """Validate generated Resource files and prepare upserts without deleting omitted targets."""

    def __init__(
        self,
        *,
        target_uri: str,
        source_roots: Mapping[str, str],
        limits: CompileLimits,
    ):
        self.target_uri = target_uri.rstrip("/")
        self.source_roots = dict(source_roots)
        self.limits = limits
        self.bundle: RenderedBundle | None = None
        self.page_count = 0
        self.file_count = 0
        # Only final output validation starts the bounded repair phase; queue guards do not.
        self.validation_attempts = 0
        self.warnings: list[str] = []
        self._accept_partial = False
        # Task-owned children must be collected before output can be finalized.
        self.submission_guard: Callable[[bool], str | None] | None = None

    @property
    def name(self) -> str:
        return "submit_wiki_bundle"

    @property
    def description(self) -> str:
        return (
            "Submit the complete Resource output from the designated final output directory. "
            "Pass no pages, files, paths, or content; "
            "Compile preserves omitted existing target files and commits "
            "only validated changes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    async def execute(self, tool_context: ToolContext, **kwargs: Any) -> str:
        self.bundle = None
        self.page_count = 0
        self.file_count = 0
        if kwargs:
            return "Error: submit_wiki_bundle takes no arguments for a Resource output."
        if tool_context.sandbox_manager is None:
            return "Error: Invalid output directory: task sandbox is unavailable"
        if self.submission_guard is not None:
            # Final validation and its bounded repair do not launch more children.
            error = self.submission_guard(True)
            if error:
                return error
        self.validation_attempts += 1
        try:
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            entries = await sandbox.list_files(
                COMPILE_OUTPUT_ROOT,
                max_entries=None,
            )
            output_files: dict[str, bytes] = {}
            paths_by_case: dict[str, str] = {}
            output_prefix = f"{COMPILE_OUTPUT_ROOT}/"
            errors: list[str] = []
            for entry in entries:
                workspace_path = _normalize_workspace_path(entry.path)
                if not workspace_path.startswith(output_prefix):
                    raise ValueError(
                        f"output inventory returned an out-of-tree path: {workspace_path}"
                    )
                relative = validate_relative_file_path(workspace_path.removeprefix(output_prefix))
                prior = paths_by_case.setdefault(relative.casefold(), relative)
                if prior != relative:
                    raise ValueError(f"case-colliding output paths: {prior}, {relative}")
                if entry.size < 0:
                    raise ValueError(f"output file has an invalid size: {relative}")
                payload = await sandbox.read_file_bytes(workspace_path)
                try:
                    validate_resource_file(relative, payload)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                output_files[relative] = payload

            if errors and not self._accept_partial:
                raise ValueError("\n".join(errors)[:6000])
            if self._accept_partial:
                self.warnings = [
                    "Final repair stopped; only valid final-output files are published.",
                    *errors,
                ]
                if not output_files:
                    raise ValueError("No valid final-output files remain")

            finalized = finalize_resource_output(
                output_files,
                target_uri=self.target_uri,
                source_roots=self.source_roots,
            )
            rendered = RenderedBundle(link_count=finalized.link_count)
            self.page_count = len(finalized.wiki_paths)
            self.file_count = len(finalized.files)
            for path, payload in sorted(finalized.files.items()):
                uri = safe_join_viking_uri(self.target_uri, path).rstrip("/")
                is_wiki = path in finalized.wiki_paths
                rendered.operations.append(
                    {
                        "uri": uri,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                        "mode": "upsert",
                    }
                )
                if is_wiki:
                    rendered.wiki_uris.append(uri)
            self.bundle = rendered
        except (OSError, ValueError) as exc:
            return f"Error: Invalid output directory: {exc}"

        changed = len(rendered.operations)
        return (
            f"Resource output accepted with {changed} changed file(s) and "
            f"{self.page_count} Wiki page(s) in the submitted output."
        )

    async def accept_valid_output(self, tool_context: ToolContext) -> str:
        """Finalize usable output after the repair budget, keeping invalid files in the workspace.

        This runtime-only fallback never asks the model to retry and never publishes
        files that fail normal per-file validation. All valid files are retained.
        """
        self._accept_partial = True
        return await self.execute(tool_context)


class SubmitWikiBundleTool(Tool):
    def __init__(
        self,
        *,
        source_ids: set[str],
        catalog_uris: set[str],
        file_catalog_uris: set[str] | None = None,
        target_uri: str,
        limits: CompileLimits,
        require_workspace_files: bool = False,
        require_workspace_pages: bool = False,
        workspace_baseline: set[str] | None = None,
        wiki_uri_resolver: Callable[[str], Awaitable[bool]] | None = None,
        exec_enabled: bool = True,
    ):
        self.source_ids = source_ids
        self.catalog_uris = catalog_uris
        self.file_catalog_uris = set(catalog_uris)
        self.file_catalog_uris.update(file_catalog_uris or ())
        self.target_uri = target_uri.rstrip("/")
        self.limits = limits
        self.require_workspace_files = require_workspace_files
        self.require_workspace_pages = require_workspace_pages
        self.workspace_baseline = (
            None
            if workspace_baseline is None
            else {_normalize_workspace_path(path) for path in workspace_baseline}
        )
        self.wiki_uri_resolver = wiki_uri_resolver
        self.exec_enabled = exec_enabled
        self.bundle: WikiBundleDraft | None = None
        self.file_payloads: list[bytes | None] = []
        self.skill_name: str | None = None
        self.submission_guard: Callable[[bool], str | None] | None = None

    @property
    def _is_skill_target(self) -> bool:
        return context_type_for_uri(self.target_uri) == "skill"

    @property
    def name(self) -> str:
        return "submit_wiki_bundle"

    @property
    def description(self) -> str:
        artifact_writers = "write_file or exec" if self.exec_enabled else "write_file"
        workspace_notice = (
            f" Generate artifact files with {artifact_writers}, then reference them with "
            "workspace_path; do not inline file content."
            if self.require_workspace_files
            else ""
        )
        if self._is_skill_target:
            return (
                "Submit one complete OpenViking Skill package. Include every file under "
                "<skill-name>/ and include <skill-name>/SKILL.md."
                f"{workspace_notice}"
            )
        return (
            "Submit the final output only after every path and format explicitly required "
            "by the Skill is represented. Treat only actual Wiki content as Wiki pages and "
            f"preserve exact-path Skill outputs as artifact files.{workspace_notice}"
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if "raw" in params:
            message = "use the tool schema directly; do not wrap the payload in a JSON string"
            if self.require_workspace_files:
                artifact_writers = "write_file or exec" if self.exec_enabled else "write_file"
                message += (
                    f"; generate artifact files with {artifact_writers} and submit them using "
                    "workspace_path instead of inline content"
                )
            return [message]
        return super().validate_params(params)

    @property
    def parameters(self) -> dict[str, Any]:
        schema = WikiBundleDraft.model_json_schema()
        required = schema.setdefault("required", [])
        if "files" not in required:
            required.append("files")
        definitions = schema.get("$defs", {})
        if self.require_workspace_files:
            file_schema = definitions.get("CompileFileDraft", {})
            file_properties = file_schema.get("properties", {})
            if isinstance(file_properties, dict):
                file_properties.pop("content", None)
            file_required = file_schema.setdefault("required", [])
            if "content" in file_required:
                file_required.remove("content")
            if "workspace_path" not in file_required:
                file_required.append("workspace_path")
        if self._is_skill_target:
            schema["properties"].pop("pages", None)
            schema["properties"].pop("links", None)
            required[:] = [field for field in required if field not in {"pages", "links"}]
            definitions.pop("WikiPageDraft", None)
            definitions.pop("WikiLink", None)
            file_schema = definitions.get("CompileFileDraft", {})
            file_schema.get("properties", {}).pop("update_uri", None)
            file_required = file_schema.setdefault("required", [])
            if "path" not in file_required:
                file_required.append("path")
            schema.pop("title", None)
            return schema
        if self.require_workspace_pages:
            page_def = schema.get("$defs", {}).get("WikiPageDraft", {})
            page_properties = page_def.get("properties", {})
            if isinstance(page_properties, dict):
                page_properties.pop("body_markdown", None)
            page_required = page_def.setdefault("required", [])
            if "body_markdown" in page_required:
                page_required.remove("body_markdown")
            if "body_workspace_path" not in page_required:
                page_required.append("body_workspace_path")
        link_def = schema.get("$defs", {}).get("WikiLink", {})
        match_schema = link_def.get("properties", {}).get("match_text")
        if isinstance(match_schema, dict):
            match_schema["description"] = (
                "Exact anchor text that must either appear in the source page draft body "
                "outside frontmatter, code, existing Markdown links, and Citations, or "
                "already be part of a Markdown link to the target page."
            )
        schema.pop("title", None)
        return schema

    @property
    def resource_inputs(self) -> dict[str, str]:
        return {
            "/files/*/workspace_path": "local_file",
            "/pages/*/body_workspace_path": "local_file",
        }

    async def execute(
        self,
        tool_context: ToolContext,
        pages: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
        links: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        self.bundle = None
        self.file_payloads = []
        self.skill_name = None
        if self.submission_guard is not None:
            error = self.submission_guard(False)
            if error:
                return error
        raw_links = links or []
        for index, link in enumerate(raw_links):
            if not isinstance(link, Mapping) or set(link) - _LINK_FIELDS:
                return f"Error: links[{index}] contains unknown fields."
        try:
            bundle = WikiBundleDraft.model_validate(
                {"pages": pages or [], "files": files or [], "links": raw_links}
            )
            warnings = await self._validate_workspace_manifest(
                bundle,
                tool_context=tool_context,
            )
            bundle = await self._materialize_page_bodies(bundle, tool_context=tool_context)
            payloads, bundle_warnings = await self._validate_bundle(
                bundle, tool_context=tool_context
            )
            warnings.extend(bundle_warnings)
        except (ValidationError, ValueError) as exc:
            kind = "Skill" if self._is_skill_target else "Wiki"
            return f"Error: Invalid {kind} bundle: {exc}"
        if self.submission_guard is not None:
            error = self.submission_guard(True)
            if error:
                return error
        self.bundle = bundle
        self.file_payloads = payloads
        if self._is_skill_target:
            summary = (
                f"Skill bundle accepted for '{self.skill_name}' with {len(bundle.files)} file(s)."
            )
        else:
            summary = (
                f"Wiki bundle accepted with {len(bundle.pages)} page(s) and "
                f"{len(bundle.files)} file(s)."
            )
        if warnings:
            summary += " Warnings: " + "; ".join(warnings)
        return summary

    async def _list_workspace_files(
        self,
        *,
        tool_context: ToolContext,
    ) -> set[str]:
        if tool_context.sandbox_manager is None:
            raise ValueError("task sandbox is unavailable")
        sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
        files: set[str] = set()
        pending = [""]
        while pending:
            directory = pending.pop()
            try:
                entries = await sandbox.list_dir(directory or ".")
            except Exception as exc:
                raise ValueError("task workspace could not be inspected") from exc
            for name, is_dir in entries:
                relative = _normalize_workspace_path(f"{directory}/{name}" if directory else name)
                if _path_is_within(relative, COMPILE_STAGING_ROOT):
                    continue
                if name in {".git", "__pycache__"}:
                    continue
                if is_dir:
                    pending.append(relative)
                elif not relative.endswith((".pyc", ".pyo")):
                    files.add(relative)
        return files

    async def _validate_workspace_manifest(
        self,
        bundle: WikiBundleDraft,
        *,
        tool_context: ToolContext,
    ) -> list[str]:
        if context_type_for_uri(self.target_uri) != "resource":
            return []
        page_paths = {
            _normalize_workspace_path(page.body_workspace_path)
            for page in bundle.pages
            if page.body_workspace_path is not None
        }
        artifact_paths = {
            _normalize_workspace_path(file.workspace_path)
            for file in bundle.files
            if file.workspace_path is not None
        }
        errors: list[str] = []
        warnings: list[str] = []
        page_workspace_root = f"{COMPILE_STAGING_ROOT}/wiki_pages"
        invalid_pages = sorted(
            path for path in page_paths if not _path_is_within(path, page_workspace_root)
        )
        if self.require_workspace_pages and invalid_pages:
            errors.append(
                "Wiki page body workspace paths must be editable files under "
                f"{page_workspace_root}/: " + ", ".join(invalid_pages)
            )
        invalid_artifacts = sorted(
            path for path in artifact_paths if _path_is_within(path, page_workspace_root)
        )
        if invalid_artifacts:
            errors.append(
                "Skill artifact workspace paths must not use the Wiki page body area "
                f"under {page_workspace_root}/: " + ", ".join(invalid_artifacts)
            )

        if self.workspace_baseline is not None:
            current_files = await self._list_workspace_files(tool_context=tool_context)
            generated_artifacts = current_files - self.workspace_baseline
            missing_artifacts = sorted(generated_artifacts - artifact_paths)
            if missing_artifacts:
                if self.require_workspace_files:
                    errors.append(
                        "generated Skill artifacts are missing from files; preserve their "
                        "required paths and submit them unchanged: " + ", ".join(missing_artifacts)
                    )
                else:
                    warnings.append(
                        "workspace files were not submitted and will be ignored: "
                        + ", ".join(missing_artifacts)
                    )
        if errors:
            raise ValueError("; ".join(errors))
        return warnings

    async def _read_workspace_bytes(
        self,
        workspace_path: str,
        *,
        tool_context: ToolContext,
        label: str,
    ) -> bytes:
        try:
            relative = _normalize_workspace_path(workspace_path)
            if tool_context.sandbox_manager is None:
                raise ValueError("task sandbox is unavailable")
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            return await sandbox.read_file_bytes(relative)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"{label} workspace path could not be read: {workspace_path}") from exc

    async def _materialize_page_bodies(
        self,
        bundle: WikiBundleDraft,
        *,
        tool_context: ToolContext,
    ) -> WikiBundleDraft:
        artifact_workspace_paths = {
            _normalize_workspace_path(file.workspace_path)
            for file in bundle.files
            if file.workspace_path is not None
        }
        pages = []
        for page in bundle.pages:
            if self.require_workspace_pages and page.body_markdown is not None:
                raise ValueError(
                    f"page {page.page_id} body must be generated with write_file and "
                    "submitted using body_workspace_path instead of inline Markdown"
                )
            if page.body_workspace_path is None:
                pages.append(page)
                continue
            workspace_path = _normalize_workspace_path(page.body_workspace_path)
            if workspace_path in artifact_workspace_paths:
                raise ValueError(
                    f"page {page.page_id} body must be a separate reader-oriented "
                    "workspace file, not an exact artifact file"
                )
            raw = await self._read_workspace_bytes(
                workspace_path,
                tool_context=tool_context,
                label=f"page {page.page_id} body",
            )
            try:
                body = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"page {page.page_id} body_workspace_path must contain UTF-8 Markdown"
                ) from exc
            pages.append(
                page.model_copy(update={"body_markdown": body, "body_workspace_path": None})
            )
        return bundle.model_copy(update={"pages": pages})

    async def _validate_bundle(
        self, bundle: WikiBundleDraft, *, tool_context: ToolContext
    ) -> tuple[list[bytes | None], list[str]]:
        target_type = context_type_for_uri(self.target_uri)
        if not bundle.pages and bundle.links:
            raise ValueError("empty bundle must not contain links")
        if target_type == "skill" and (bundle.pages or bundle.links):
            raise ValueError("Skill targets only accept artifact files")
        if bundle.files and target_type not in {"resource", "skill"}:
            raise ValueError(
                "raw artifact files are only supported for Resource targets or exact "
                "Skill namespace targets; re-run ov compile with a supported target"
            )
        if self.require_workspace_files and any(file.content is not None for file in bundle.files):
            artifact_writers = "write_file or exec" if self.exec_enabled else "write_file"
            raise ValueError(
                f"artifact files must be generated with {artifact_writers} and submitted "
                "using workspace_path instead of inline content"
            )
        page_ids: set[int] = set()
        page_uris: dict[int, str] = {}
        final_uris: set[str] = set()
        for page in bundle.pages:
            if page.body_markdown is None:
                raise ValueError(f"page {page.page_id} body was not materialized")
            if page.page_id in page_ids:
                raise ValueError(f"duplicate page_id: {page.page_id}")
            page_ids.add(page.page_id)
            if not page.title.strip() or not page.page_type.strip() or not page.summary.strip():
                raise ValueError(f"page {page.page_id} has empty required fields")
            if "\n" in page.summary.strip() or "\r" in page.summary.strip():
                raise ValueError(f"page {page.page_id} summary must be one line")
            if page.body_markdown.lstrip().startswith("---"):
                raise ValueError(
                    f"page {page.page_id} must not include YAML frontmatter. If this is a "
                    "Skill-prescribed artifact, do not edit or strip its frontmatter; submit "
                    f"it through files and create a separate Wiki body under "
                    f"{COMPILE_STAGING_ROOT}/wiki_pages/"
                )
            source_ids = list(
                dict.fromkeys(source_id for source_id in page.source_ids if source_id)
            )
            if source_ids and any(source_id not in self.source_ids for source_id in source_ids):
                valid = ", ".join(sorted(self.source_ids))
                raise ValueError(
                    f"page {page.page_id} source_ids must reference supplied source roots "
                    f"(one of: {valid})"
                )
            page.source_ids = source_ids or sorted(self.source_ids)
            if page.update_uri:
                final_uri = page.update_uri.rstrip("/")
                if is_reserved_wiki_page_uri(final_uri):
                    raise ValueError(f"page {page.page_id} cannot update a reserved Wiki file")
                if not await self._is_wiki_uri(final_uri):
                    raise ValueError(
                        f"page {page.page_id} update_uri is not an existing OKF Wiki page"
                    )
                if page.path_hint:
                    raise ValueError(f"page {page.page_id} cannot rename an update")
            else:
                hint = page.path_hint or wiki_page_path_from_title(page.title)
                relative = validate_relative_page_path(hint)
                final_uri = safe_join_viking_uri(self.target_uri, relative).rstrip("/")
                if final_uri in self.file_catalog_uris:
                    raise ValueError(f"page {page.page_id} path exists; use its update_uri")
            if final_uri in final_uris:
                raise ValueError(f"duplicate final Wiki path: {final_uri}")
            final_uris.add(final_uri)
            page_uris[page.page_id] = final_uri

        file_payloads: list[bytes | None] = []
        for index, file in enumerate(bundle.files):
            if target_type == "skill":
                if file.update_uri:
                    raise ValueError("Skill bundles require relative path entries, not update_uri")
                relative = validate_relative_file_path(file.path or "")
                final_uri = safe_join_viking_uri(self.target_uri, relative).rstrip("/")
            elif file.update_uri:
                final_uri = validate_safe_viking_uri_path(file.update_uri).rstrip("/")
                if is_reserved_wiki_page_uri(final_uri):
                    raise ValueError(f"file {index} cannot update a reserved file")
                if final_uri not in self.file_catalog_uris:
                    raise ValueError(f"file {index} update_uri is not in the catalog")
            else:
                relative = validate_relative_file_path(file.path or "")
                final_uri = safe_join_viking_uri(self.target_uri, relative).rstrip("/")
                if final_uri in self.file_catalog_uris:
                    raise ValueError(f"file {index} path exists; use its update_uri")
            if final_uri in final_uris:
                raise ValueError(f"duplicate final output path: {final_uri}")
            final_uris.add(final_uri)

            if file.content is not None:
                payload = None
                content_bytes = file.content.encode("utf-8")
            else:
                payload = await self._read_workspace_bytes(
                    file.workspace_path or "",
                    tool_context=tool_context,
                    label=f"file {index}",
                )
                content_bytes = payload
            if target_type == "resource":
                page_type = validate_declared_okf_markdown(final_uri, content_bytes)
                existing_wiki = bool(file.update_uri and await self._is_wiki_uri(final_uri))
                if existing_wiki and page_type is None:
                    raise ValueError(
                        f"file {index} updates an existing Wiki page and must retain "
                        "valid OKF frontmatter with a non-empty type"
                    )
            file_payloads.append(payload)

        if target_type == "skill":
            self.skill_name = self._validate_skill_bundle(bundle, file_payloads)
        page_by_id = {page.page_id: page for page in bundle.pages}
        link_errors: list[str] = []
        warnings: list[str] = []
        valid_links: list[Any] = []
        for index, link in enumerate(bundle.links):
            prefix = f"links[{index}]"
            if link.f is None or link.t is None:
                link_errors.append(f"{prefix} endpoints must be non-null")
                continue
            if link.f == link.t:
                link_errors.append(f"{prefix} must not be a self-link")
                continue
            if link.f not in page_ids or link.t not in page_ids:
                link_errors.append(f"{prefix} endpoints must reference bundle pages")
                continue
            if not link.match_text:
                warnings.append(f"{prefix} has no match_text and was dropped")
                continue
            source_page = page_by_id[link.f]
            if not LinkRenderer.can_render_link(
                source_page.body_markdown,
                link.match_text,
                page_uris[link.f],
                page_uris[link.t],
            ):
                warnings.append(
                    f"{prefix} anchor {link.match_text!r} was not found and the link was dropped"
                )
                continue
            valid_links.append(link)
        if link_errors:
            raise ValueError(f"{len(link_errors)} invalid link(s): " + "; ".join(link_errors))
        bundle.links = valid_links
        return file_payloads, warnings

    async def _is_wiki_uri(self, uri: str) -> bool:
        if not relative_uri_path(self.target_uri, validate_safe_viking_uri_path(uri)):
            return False
        if uri in self.catalog_uris:
            return True
        if self.wiki_uri_resolver is None:
            return False
        if await self.wiki_uri_resolver(uri):
            self.catalog_uris.add(uri)
            return True
        return False

    @staticmethod
    def _validate_skill_bundle(bundle: WikiBundleDraft, file_payloads: list[bytes | None]) -> str:
        if not bundle.files:
            raise ValueError("Skill bundle must contain files")

        skill_names: set[str] = set()
        contents: dict[str, bytes] = {}
        for index, file in enumerate(bundle.files):
            relative = validate_relative_file_path(file.path or "")
            parts = relative.split("/")
            if len(parts) < 2:
                raise ValueError(f"file {index} must be under <skill-name>/, got: {relative}")
            skill_names.add(parts[0])
            payload = (
                file.content.encode("utf-8") if file.content is not None else file_payloads[index]
            )
            if payload is None:
                raise ValueError(f"file {index} has no materialized content")
            contents[relative] = payload

        if len(skill_names) != 1:
            raise ValueError("Skill bundle must contain exactly one top-level Skill directory")
        skill_name = next(iter(skill_names))
        skill_md_path = f"{skill_name}/SKILL.md"
        skill_md = contents.get(skill_md_path)
        if skill_md is None:
            raise ValueError(f"Skill bundle must include {skill_md_path}")
        try:
            skill_md_text = skill_md.decode("utf-8")
            parsed = SkillLoader.parse(skill_md_text, source_path=skill_md_path)
            parsed_name = validate_skill_name(parsed.get("name"))
        except (UnicodeDecodeError, ValueError, OpenVikingError, yaml.YAMLError) as exc:
            raise ValueError(str(exc)) from exc
        if parsed_name != skill_name:
            raise ValueError(f"Skill name '{parsed_name}' does not match directory '{skill_name}'")
        validation = validate_skill_format(
            skill_md_text,
            strict=True,
            skill_dir_name=skill_name,
            source_path=skill_md_path,
        )
        if not validation["valid"]:
            messages = [
                str(issue.get("message") or issue.get("rule") or "invalid Skill")
                for issue in validation["errors"]
            ]
            raise ValueError("; ".join(messages))
        return skill_name


__all__ = [
    "SubmitCompileOutputTool",
    "SubmitWikiBundleTool",
]
