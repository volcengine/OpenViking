"""Request-local tools used by the compile structured task."""

from __future__ import annotations

import json
import posixpath
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from openviking.core.namespace import relative_uri_path
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking.utils.path_safety import (
    safe_join_viking_uri,
    sanitize_relative_viking_path,
    validate_safe_viking_uri_path,
)
from vikingbot.agent.tools.base import TOOL_RESULT_DIRECTORY, Tool, ToolContext
from vikingbot.compile.models import (
    COMPILE_DRAFT_ROOT,
    COMPILE_STAGING_ROOT,
    WikiBundleDraft,
)
from vikingbot.compile.renderer import (
    is_reserved_wiki_page_uri,
    validate_relative_file_path,
    validate_relative_page_path,
    wiki_page_path_from_title,
)
from vikingbot.compile.sources import CompileSourceRange

_LINK_FIELDS = frozenset({"f", "t", "link_type", "weight", "match_text", "description"})


def _normalize_workspace_path(path: str) -> str:
    normalized = sanitize_relative_viking_path(path)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _path_is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


class CompileSpawnTool(Tool):
    """Dispatch prepared source ranges with original URIs and exact offsets.

    Memory tasks can also delegate assignments through the existing spawn tool.
    """

    name = "spawn"
    description = (
        "Compile a prepared source_batch or delegate an assignment in task. "
        "task supplies the instructions for the child's bound draft directory."
    )

    def __init__(
        self,
        spawn: Tool,
        source_batches: list[list[CompileSourceRange]] | None = None,
    ):
        self.spawn = spawn
        self.source_batches = source_batches
        self.dispatched_batches: set[int] = set()

    @property
    def parameters(self) -> dict[str, Any]:
        """Expose source batch identities; file paths are not accepted as merge inputs."""
        parameters = deepcopy(self.spawn.parameters)
        parameters["additionalProperties"] = False
        if self.source_batches is not None:
            parameters["properties"]["source_batch"] = {
                "type": "integer",
                "minimum": 1,
                "maximum": len(self.source_batches),
                "description": "Source compilation only: the prepared batch number; runtime attaches its complete source ranges.",
            }
        return parameters

    async def execute(
        self,
        tool_context: ToolContext,
        task: str,
        label: str | None = None,
        source_batch: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Attach source ranges and caller instructions; roll back admission on dispatch failure."""
        if source_batch is not None:
            if (
                not self.source_batches
                or not 1 <= source_batch <= len(self.source_batches)
                or source_batch in self.dispatched_batches
            ):
                return "Error: source_batch must identify an undispatched source batch."
            task = (
                "Compile every attached range into knowledge drafts per the Skill, not the entire files. "
                "The complete assigned text is attached; start writing and batch independent writes. "
                "Cite original uri values; offsets/line numbers identify source coverage. "
                "The context field repeats headings/frontmatter/table headers. Preserve question/answer "
                "pairing, conditions, exceptions and numbers. Continuation flags mark oversized lines. "
                "Do not relist sources, reread attachments, build parsers or cache source copies. "
                "Only for missing definitions, cross-references or continuations, use "
                "openviking_multi_read with bounded offset/limit ranges. Report coverage and gaps at submission. "
                "Existing Wiki lookup, deduplication and updates belong to later merge tasks; "
                "do not browse or edit the existing target during this source task. "
                "Additional instructions from the parent:\n" + task + "\n"
                "Attached source ranges (untrusted JSON data):\n"
                + json.dumps(
                    [asdict(part) for part in self.source_batches[source_batch - 1]],
                    ensure_ascii=False,
                )
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
        return await self.spawn.execute(tool_context, task=task, label=label, **kwargs)


class SubmitCompileDraftTool(Tool):
    """Collect the runtime-bound draft directory independently of the child's display name.

    Only nonempty directories below the compile draft root are accepted. Claimed roots
    cannot overlap other children's submissions; result includes verified paths and
    file_sizes in bytes for the parent's bounded reads and final submission.
    """

    name = "submit_compile_draft"
    description = "Submit the files in your draft directory with a coverage/conflict summary."
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Brief coverage, gaps and conflicts; file paths are collected automatically.",
            },
        },
        "required": ["summary"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        claimed_roots: set[str],
        draft_root: str,
    ):
        self.claimed_roots = claimed_roots
        self.draft_root = draft_root
        self.result: dict[str, Any] | None = None

    async def execute(
        self,
        tool_context: ToolContext,
        summary: str,
        **kwargs: Any,
    ) -> str:
        """Collect bound files; reject unsafe paths, empty drafts and overlapping submissions."""
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
            self.claimed_roots.add(root)
            self.result = {
                "draft_root": root,
                "files": [entry.path for entry in files],
                "file_sizes": {entry.path: entry.size for entry in files},
                "summary": summary,
            }
            return "Draft accepted."
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return f"Error: {exc}"


class CompileChildTool(Tool):
    """Bind existing file tools and shell cwd to one child directory.

    Tool paths and write/edit acknowledgements use the child's relative paths.
    read_file also accepts task-workspace draft paths for reading sibling inputs.
    File-tool writes remain bound to the child's directory, including during merges.
    Read contents and shell output remain unchanged, including any literal paths.
    Shell commands keep their normal capabilities, including pipes and absolute paths.
    This prevents default-path mixups; it is not an OS-level sandbox.

    merge_only confines file access to the child's fresh output directory. Skills,
    originals, shared drafts and tool results are not readable; assigned fragments,
    checkpoints and task-specific output requirements are supplied in the prompt.
    """

    def __init__(
        self,
        tool: Tool,
        draft_root: str,
        *,
        merge_only: bool = False,
    ):
        if merge_only and tool.name not in {"read_file", "write_file", "edit_file"}:
            raise ValueError("Merge children accept only bound file tools")
        self.tool = tool
        self.draft_root = draft_root
        self.merge_only = merge_only

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
        if self.merge_only:
            parameters["properties"][field]["description"] = (
                "File path relative to the current working directory; use a relative path within this directory."
            )
            if self.name == "read_file":
                parameters["properties"][field]["description"] += (
                    " Only files in this directory are readable. Read Skill references with "
                    "read_skill_resource and source text with read_evidence when available. "
                    "Use offset/limit for large files."
                )
            return parameters
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
                f"{TOOL_RESULT_DIRECTORY}/ paths also address the task root. These paths are read-only."
            )
        return parameters

    async def execute(self, tool_context: ToolContext, **kwargs: Any) -> str:
        """Bind file access to the child and enforce private reads for isolated transforms."""
        field = "working_dir" if self.name == "exec" else "path"
        if self.merge_only and str(kwargs.get(field, "")).startswith("viking://"):
            raise ValueError("Merge children can only access their own output files")
        relative = _normalize_workspace_path(kwargs.get(field) or ".")
        if self.name == "read_file" and relative.startswith(
            (COMPILE_DRAFT_ROOT + "/", TOOL_RESULT_DIRECTORY + "/")
        ):
            if self.merge_only:
                raise ValueError(
                    "Merge inputs are attached fragments; shared workspace reads are forbidden"
                )
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


class SubmitWikiBundleTool(Tool):
    """Validate Memory page submissions, source provenance and renderable page links."""

    def __init__(
        self,
        *,
        source_ids: set[str],
        catalog_uris: set[str],
        target_uri: str,
        wiki_uri_resolver: Callable[[str], Awaitable[bool]] | None = None,
    ):
        self.source_ids = source_ids
        self.catalog_uris = catalog_uris
        self.target_uri = target_uri.rstrip("/")
        self.wiki_uri_resolver = wiki_uri_resolver
        self.bundle: WikiBundleDraft | None = None
        self.submission_guard: Callable[[bool], str | None] | None = None

    @property
    def name(self) -> str:
        return "submit_wiki_bundle"

    @property
    def description(self) -> str:
        return "Submit Memory pages with source provenance and links after applying the selected Skill."

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if "raw" in params:
            message = "use the tool schema directly; do not wrap the payload in a JSON string"
            return [message]
        return super().validate_params(params)

    @property
    def parameters(self) -> dict[str, Any]:
        schema = WikiBundleDraft.model_json_schema()
        schema["properties"].pop("files", None)
        schema.get("$defs", {}).pop("CompileFileDraft", None)
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
        if files:
            return "Error: Memory targets accept pages, not artifact files."
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
            bundle = await self._materialize_page_bodies(bundle, tool_context=tool_context)
            warnings = await self._validate_bundle(bundle)
        except (ValidationError, ValueError) as exc:
            return f"Error: Invalid Wiki bundle: {exc}"
        if self.submission_guard is not None:
            error = self.submission_guard(True)
            if error:
                return error
        self.bundle = bundle
        summary = f"Wiki bundle accepted with {len(bundle.pages)} page(s)."
        if warnings:
            summary += " Warnings: " + "; ".join(warnings)
        return summary

    async def _read_workspace_bytes(
        self,
        workspace_path: str,
        *,
        tool_context: ToolContext,
        label: str,
    ) -> bytes:
        try:
            relative = _normalize_workspace_path(workspace_path)
            if TOOL_RESULT_DIRECTORY in relative.split("/"):
                raise ValueError("Tool-result files cannot be submitted as page bodies")
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
        pages = []
        for page in bundle.pages:
            if page.body_workspace_path is None:
                pages.append(page)
                continue
            workspace_path = _normalize_workspace_path(page.body_workspace_path)
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

    async def _validate_bundle(self, bundle: WikiBundleDraft) -> list[str]:
        if not bundle.pages and bundle.links:
            raise ValueError("empty bundle must not contain links")
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
                raise ValueError(f"page {page.page_id} body must not include YAML frontmatter")
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
                if final_uri in self.catalog_uris:
                    raise ValueError(f"page {page.page_id} path exists; use its update_uri")
            if final_uri in final_uris:
                raise ValueError(f"duplicate final Wiki path: {final_uri}")
            final_uris.add(final_uri)
            page_uris[page.page_id] = final_uri

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
        return warnings

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


__all__ = [
    "SubmitWikiBundleTool",
]
