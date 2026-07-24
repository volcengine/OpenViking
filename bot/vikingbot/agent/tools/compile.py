"""Request-local tools used by the compile structured task."""

from __future__ import annotations

import asyncio
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
from openviking_cli.utils import VikingURI
from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.compile.models import CompileLimits, WikiBundleDraft
from vikingbot.compile.renderer import (
    is_reserved_wiki_page_uri,
    validate_declared_okf_markdown,
    validate_relative_file_path,
    validate_relative_page_path,
)

_LINK_FIELDS = frozenset({"f", "t", "link_type", "weight", "match_text", "description"})


def _normalize_workspace_path(path: str) -> str:
    normalized = sanitize_relative_viking_path(path)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


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
        require_workspace_files: bool = False,
        require_workspace_pages: bool = False,
    ):
        self.source_ids = source_ids
        self.catalog_uris = catalog_uris
        self.file_catalog_uris = set(catalog_uris)
        self.file_catalog_uris.update(file_catalog_uris or ())
        self.target_uri = target_uri.rstrip("/")
        self.limits = limits
        self.require_workspace_files = require_workspace_files
        self.require_workspace_pages = require_workspace_pages
        self.bundle: WikiBundleDraft | None = None
        self.file_payloads: list[bytes | None] = []
        self.skill_name: str | None = None

    @property
    def _is_skill_target(self) -> bool:
        return context_type_for_uri(self.target_uri) == "skill"

    @property
    def name(self) -> str:
        return "submit_wiki_bundle"

    @property
    def description(self) -> str:
        if self._is_skill_target:
            return (
                "Submit one complete OpenViking Skill package. Include every file under "
                "<skill-name>/ and include <skill-name>/SKILL.md."
            )
        return (
            "Submit the final output only after every path and format explicitly required "
            "by the Skill is represented. Treat only actual Wiki content as Wiki pages and "
            "preserve exact-path Skill outputs as artifact files."
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if "raw" in params:
            return ["use the tool schema directly; do not wrap the payload in a JSON string"]
        return super().validate_params(params)

    @property
    def parameters(self) -> dict[str, Any]:
        schema = WikiBundleDraft.model_json_schema()
        required = schema.setdefault("required", [])
        if "files" not in required:
            required.append("files")
        if self._is_skill_target:
            schema["properties"].pop("pages", None)
            schema["properties"].pop("links", None)
            required[:] = [field for field in required if field not in {"pages", "links"}]
            definitions = schema.get("$defs", {})
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
                "Exact anchor text that must appear in the source page draft body outside "
                "frontmatter, code, existing Markdown links, and Citations."
            )
        schema.pop("title", None)
        return schema

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
        raw_links = links or []
        for index, link in enumerate(raw_links):
            if not isinstance(link, Mapping) or set(link) - _LINK_FIELDS:
                return f"Error: links[{index}] contains unknown fields."
        try:
            bundle = WikiBundleDraft.model_validate(
                {"pages": pages or [], "files": files or [], "links": raw_links}
            )
            bundle = await self._materialize_page_bodies(
                bundle, tool_context=tool_context
            )
            payloads = await self._validate_bundle(bundle, tool_context=tool_context)
        except (ValidationError, ValueError) as exc:
            kind = "Skill" if self._is_skill_target else "Wiki"
            return f"Error: Invalid {kind} bundle: {exc}"
        self.bundle = bundle
        self.file_payloads = payloads
        if self._is_skill_target:
            return (
                f"Skill bundle accepted for '{self.skill_name}' with {len(bundle.files)} file(s)."
            )
        return (
            f"Wiki bundle accepted with {len(bundle.pages)} page(s) and "
            f"{len(bundle.files)} file(s)."
        )

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
            sandbox = await tool_context.sandbox_manager.get_sandbox(
                tool_context.session_key
            )
            return await sandbox.read_file_bytes(relative)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"{label} workspace path could not be read: {workspace_path}"
            ) from exc

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
                page.model_copy(
                    update={"body_markdown": body, "body_workspace_path": None}
                )
            )
        return bundle.model_copy(update={"pages": pages})

    async def _validate_bundle(
        self, bundle: WikiBundleDraft, *, tool_context: ToolContext
    ) -> list[bytes | None]:
        target_type = context_type_for_uri(self.target_uri)
        if len(bundle.pages) > self.limits.output_pages:
            raise ValueError("page limit exceeded")
        if len(bundle.files) > self.limits.output_files:
            raise ValueError("file limit exceeded")
        if not bundle.pages and bundle.links:
            raise ValueError("empty bundle must not contain links")
        if target_type == "skill" and (bundle.pages or bundle.links):
            raise ValueError("Skill targets only accept artifact files")
        if bundle.files and target_type not in {"resource", "skill"}:
            raise ValueError(
                "raw artifact files are only supported for Resource targets or exact "
                "Skill namespace targets; re-run ov compile with a supported target"
            )
        if (
            self.require_workspace_files
            and len(bundle.files) > 1
            and any(file.content is not None for file in bundle.files)
        ):
            raise ValueError(
                "multi-file artifact bundles must be generated with write_file and "
                "submitted using workspace_path instead of inline content"
            )
        page_ids: set[int] = set()
        final_uris: set[str] = set()
        total_bytes = 0
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
                if final_uri in self.file_catalog_uris:
                    raise ValueError(
                        f"page {page.page_id} path exists; use its update_uri"
                    )
            if final_uri in final_uris:
                raise ValueError(f"duplicate final Wiki path: {final_uri}")
            final_uris.add(final_uri)
            total_bytes += len(page.body_markdown.encode("utf-8"))

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
            total_bytes += len(content_bytes)
            if total_bytes > self.limits.output_total_bytes:
                raise ValueError("draft content size limit exceeded")
            if target_type == "resource":
                page_type = validate_declared_okf_markdown(final_uri, content_bytes)
                if file.update_uri and final_uri in self.catalog_uris and page_type is None:
                    raise ValueError(
                        f"file {index} updates an existing Wiki page and must retain "
                        "valid OKF frontmatter with a non-empty type"
                    )
            file_payloads.append(payload)

        if total_bytes > self.limits.output_total_bytes:
            raise ValueError("draft content size limit exceeded")
        if target_type == "skill":
            self.skill_name = self._validate_skill_bundle(bundle, file_payloads)
        page_by_id = {page.page_id: page for page in bundle.pages}
        link_errors: list[str] = []
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
                link_errors.append(f"{prefix} match_text is required")
                continue
            source_page = page_by_id[link.f]
            if (
                LinkRenderer._find_match_span(
                    source_page.body_markdown,
                    link.match_text,
                    LinkRenderer.protected_markdown_spans(source_page.body_markdown),
                )
                is None
            ):
                link_errors.append(
                    f"{prefix} from page {link.f} has non-linkable anchor "
                    f"{link.match_text!r}; remove the link or use exact unprotected "
                    "text from that page body"
                )
        if link_errors:
            raise ValueError(f"{len(link_errors)} invalid link(s): " + "; ".join(link_errors))
        return file_payloads

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


__all__ = ["CompileScopedTool", "SubmitWikiBundleTool"]
