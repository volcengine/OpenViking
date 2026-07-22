"""Request-local tools used by the compile structured task."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from pydantic import ValidationError

from openviking.core.namespace import context_type_for_uri, relative_uri_path
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking.utils.path_safety import (
    safe_join_viking_uri,
    sanitize_relative_viking_path,
    validate_safe_viking_uri_path,
)
from openviking_cli.utils import VikingURI
from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.compile.models import CompileLimits, WikiBundleDraft
from vikingbot.compile.renderer import (
    is_reserved_wiki_page_uri,
    validate_relative_file_path,
    validate_relative_page_path,
)

_LINK_FIELDS = frozenset({"f", "t", "link_type", "weight", "match_text", "description"})


def _uri_in_roots(uri: str, roots: tuple[str, ...]) -> bool:
    normalized = str(uri or "").strip().rstrip("/")
    if not normalized.startswith("viking://"):
        return False
    try:
        normalized = validate_safe_viking_uri_path(normalized)
    except ValueError:
        return False
    return any(
        normalized == root.rstrip("/") or bool(relative_uri_path(root, normalized))
        for root in roots
    )


class CompileScopedTool(Tool):
    """Guard an existing OpenViking read tool without changing its implementation."""

    def __init__(
        self,
        tool: Tool,
        *,
        roots: tuple[str, ...],
        limits: CompileLimits,
        result_budget: dict[str, int],
        budget_lock: asyncio.Lock,
    ):
        self._tool = tool
        self._roots = roots
        self._limits = limits
        self._result_budget = result_budget
        self._budget_lock = budget_lock

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._tool.parameters

    async def execute(self, tool_context: ToolContext, **kwargs: Any) -> str:
        uris: list[str] = []
        if self.name == "openviking_search":
            value = kwargs.get("target_uri")
            if not value:
                return "Error: Compile search requires target_uri within the task scope."
            uris.append(str(value))
        elif self.name in {"openviking_list", "openviking_grep", "openviking_glob"}:
            value = kwargs.get("uri")
            if not value or str(value).rstrip("/") in {"viking:", "viking://"}:
                return f"Error: Compile {self.name} requires uri within the task scope."
            uris.append(str(value))
            if self.name == "openviking_list" and kwargs.get("recursive"):
                kwargs["node_limit"] = min(
                    int(kwargs.get("node_limit") or self._limits.target_catalog_pages),
                    self._limits.target_catalog_pages,
                )
        elif self.name == "openviking_multi_read":
            values = kwargs.get("uris")
            if not isinstance(values, list) or not values:
                return "Error: Compile multi-read requires at least one URI."
            if len(values) > self._limits.tool_uri_count:
                return "Error: Compile multi-read URI limit exceeded."
            uris.extend(str(value) for value in values)

        if len(uris) > self._limits.tool_uri_count:
            return "Error: Compile tool URI limit exceeded."
        for uri in uris:
            if not _uri_in_roots(uri, self._roots):
                return f"Error: URI is outside the Compile task scope: {uri}"

        result = await self._tool.execute(tool_context, **kwargs)
        if isinstance(result, str) and result.startswith("Error") and not result.startswith("Error:"):
            result = "Error: " + result[len("Error") :].lstrip(" :")
        rendered = str(result)
        size = len(rendered.encode("utf-8"))
        if size > self._limits.tool_result_bytes:
            return "Error: Compile tool result exceeds the per-call size limit."
        async with self._budget_lock:
            total = self._result_budget.get("bytes", 0) + size
            if total > self._limits.tool_total_result_bytes:
                return "Error: Compile task tool-result budget exceeded."
            self._result_budget["bytes"] = total
        return rendered


class SubmitWikiBundleTool(Tool):
    def __init__(
        self,
        *,
        source_ids: set[str],
        catalog_uris: set[str],
        file_catalog_uris: set[str] | None = None,
        target_uri: str,
        limits: CompileLimits,
    ):
        self.source_ids = source_ids
        self.catalog_uris = catalog_uris
        self.file_catalog_uris = file_catalog_uris or set()
        self.target_uri = target_uri.rstrip("/")
        self.limits = limits
        self.bundle: WikiBundleDraft | None = None
        self.file_payloads: list[bytes | None] = []

    @property
    def name(self) -> str:
        return "submit_wiki_bundle"

    @property
    def description(self) -> str:
        return "Submit the final validated Wiki pages and explicitly declared raw output files."

    @property
    def parameters(self) -> dict[str, Any]:
        schema = WikiBundleDraft.model_json_schema()
        link_def = schema.get("$defs", {}).get("WikiLink", {})
        match_schema = link_def.get("properties", {}).get("match_text")
        if isinstance(match_schema, dict):
            match_schema["description"] = (
                "Exact anchor text that must appear in the source page draft body outside "
                "frontmatter, code, existing Markdown links, and Citations."
            )
        schema.pop("title", None)
        return schema

    async def execute(
        self,
        tool_context: ToolContext,
        pages: list[dict[str, Any]],
        files: list[dict[str, Any]] | None = None,
        links: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        self.bundle = None
        self.file_payloads = []
        raw_links = links or []
        for index, link in enumerate(raw_links):
            if not isinstance(link, Mapping) or set(link) - _LINK_FIELDS:
                return f"Error: links[{index}] contains unknown fields."
        try:
            bundle = WikiBundleDraft.model_validate(
                {"pages": pages, "files": files or [], "links": raw_links}
            )
            payloads = await self._validate_bundle(bundle, tool_context=tool_context)
        except (ValidationError, ValueError) as exc:
            return f"Error: Invalid Wiki bundle: {exc}"
        self.bundle = bundle
        self.file_payloads = payloads
        return (
            f"Wiki bundle accepted with {len(bundle.pages)} page(s) and "
            f"{len(bundle.files)} file(s)."
        )

    async def _validate_bundle(
        self, bundle: WikiBundleDraft, *, tool_context: ToolContext
    ) -> list[bytes | None]:
        if len(bundle.pages) > self.limits.output_pages:
            raise ValueError("page limit exceeded")
        if len(bundle.files) > self.limits.output_files:
            raise ValueError("file limit exceeded")
        if not bundle.pages and bundle.links:
            raise ValueError("empty bundle must not contain links")
        if bundle.files and context_type_for_uri(self.target_uri) != "resource":
            raise ValueError(
                "raw artifact files are only supported for Resource targets; "
                "re-run ov compile with a viking://resources/... target"
            )
        page_ids: set[int] = set()
        final_uris: set[str] = set()
        total_bytes = 0
        for page in bundle.pages:
            if page.page_id in page_ids:
                raise ValueError(f"duplicate page_id: {page.page_id}")
            page_ids.add(page.page_id)
            if not page.title.strip() or not page.page_type.strip() or not page.summary.strip():
                raise ValueError(f"page {page.page_id} has empty required fields")
            if "\n" in page.summary.strip() or "\r" in page.summary.strip():
                raise ValueError(f"page {page.page_id} summary must be one line")
            if page.body_markdown.lstrip().startswith("---"):
                raise ValueError(f"page {page.page_id} must not include YAML frontmatter")
            if not page.source_ids or any(source_id not in self.source_ids for source_id in page.source_ids):
                raise ValueError(f"page {page.page_id} has invalid source_ids")
            if page.update_uri:
                final_uri = page.update_uri.rstrip("/")
                if is_reserved_wiki_page_uri(final_uri):
                    raise ValueError(f"page {page.page_id} cannot update a reserved Wiki file")
                if final_uri not in self.catalog_uris:
                    raise ValueError(f"page {page.page_id} update_uri is not in the catalog")
                if page.path_hint:
                    raise ValueError(f"page {page.page_id} cannot rename an update")
            else:
                hint = page.path_hint or VikingURI.sanitize_segment(page.title.strip())
                relative = validate_relative_page_path(hint)
                final_uri = safe_join_viking_uri(self.target_uri, relative).rstrip("/")
                if final_uri in self.catalog_uris:
                    raise ValueError(
                        f"page {page.page_id} path exists; use its update_uri"
                    )
            if final_uri in final_uris:
                raise ValueError(f"duplicate final Wiki path: {final_uri}")
            final_uris.add(final_uri)
            total_bytes += len(page.body_markdown.encode("utf-8"))

        file_payloads: list[bytes | None] = []
        for index, file in enumerate(bundle.files):
            if file.update_uri:
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
                try:
                    workspace_path = sanitize_relative_viking_path(file.workspace_path or "")
                    if tool_context.sandbox_manager is None:
                        raise ValueError("task sandbox is unavailable")
                    sandbox = await tool_context.sandbox_manager.get_sandbox(
                        tool_context.session_key
                    )
                    payload = await sandbox.read_file_bytes(workspace_path)
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError(
                        f"file {index} workspace_path could not be read: {file.workspace_path}"
                    ) from exc
                content_bytes = payload
            total_bytes += len(content_bytes)
            file_payloads.append(payload)

        if total_bytes > self.limits.output_total_bytes:
            raise ValueError("draft content size limit exceeded")
        for link in bundle.links:
            if link.f is None or link.t is None or link.f == link.t:
                raise ValueError("link endpoints must be non-null and non-self")
            if link.f not in page_ids or link.t not in page_ids:
                raise ValueError("link endpoints must reference bundle pages")
            if not link.match_text:
                raise ValueError("link match_text is required")
            source_page = next(page for page in bundle.pages if page.page_id == link.f)
            if (
                LinkRenderer._find_match_span(
                    source_page.body_markdown,
                    link.match_text,
                    LinkRenderer.protected_markdown_spans(source_page.body_markdown),
                )
                is None
            ):
                raise ValueError(f"link anchor is not linkable: {link.match_text!r}")
        return file_payloads


__all__ = ["CompileScopedTool", "SubmitWikiBundleTool"]
