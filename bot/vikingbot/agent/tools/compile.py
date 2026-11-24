"""Request-local file and image tools for Compile agents."""

from __future__ import annotations

import posixpath
from copy import deepcopy
from typing import Any, Mapping

from openviking.utils.path_safety import sanitize_relative_viking_path
from vikingbot.agent.tools.base import TOOL_RESULT_DIRECTORY, Tool, ToolContext
from vikingbot.agent.tools.ov_file import VikingMultiReadTool
from vikingbot.compile.models import COMPILE_DRAFT_ROOT, COMPILE_STAGING_ROOT


def _normalize_workspace_path(path: str) -> str:
    normalized = sanitize_relative_viking_path(path)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


class CompileImageReadTool(VikingMultiReadTool):
    """Reuse native multi-read while checking the prepared originals' identities."""

    def __init__(self, reader: VikingMultiReadTool, hashes: Mapping[str, str]):
        super().__init__()
        self.reader, self.hashes = reader, hashes

    async def _get_client(self, tool_context):
        return await self.reader._get_client(tool_context)

    async def _release_client(self, tool_context, client):
        await self.reader._release_client(tool_context, client)

    async def _read_image(self, client, uri):
        result = await super()._read_image(client, uri, for_compile=True)
        if result["success"]:
            if uri in self.hashes and result["sha256"] != self.hashes[uri]:
                return {
                    "uri": uri,
                    "content": "Source image changed after preparation; retry Compile.",
                    "success": False,
                    "media_kind": "image",
                }
            result["content"] += f" SHA256: {result['sha256']}."
        return result


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
    readonly_paths maps explicit input aliases to task-owned files outside that
    output directory. Only read_file can access them; inputs/ is reserved for aliases.
    """

    def __init__(
        self,
        tool: Tool,
        draft_root: str,
        *,
        merge_only: bool = False,
        readonly_paths: Mapping[str, str] | None = None,
    ):
        if merge_only and tool.name not in {"read_file", "write_file", "edit_file"}:
            raise ValueError("Merge children accept only bound file tools")
        self.tool = tool
        self.draft_root = draft_root
        self.merge_only = merge_only
        self.readonly_paths = dict(readonly_paths or {})

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
            if self.readonly_paths:
                parameters["properties"][field]["description"] = (
                    "Output path relative to the publication destination. Use the same path "
                    "to write, read and edit an output. assignment.json and the assigned "
                    "inputs/ paths are read-only inputs; other workspace paths are unavailable."
                )
                return parameters
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
        if self.readonly_paths:
            if relative in self.readonly_paths:
                if self.name != "read_file":
                    raise ValueError(f"Assigned input is read-only: {relative}")
                return await self.tool.execute(
                    tool_context, **{**kwargs, field: self.readonly_paths[relative]}
                )
            if relative == "inputs" or relative.startswith("inputs/"):
                raise ValueError(f"Input path is not assigned: {relative}")
        kwargs[field] = posixpath.join(self.draft_root, relative)
        result = await self.tool.execute(tool_context, **kwargs)
        if (
            self.name in {"write_file", "edit_file"}
            and result.startswith("Successfully ")
            and result.endswith(kwargs[field])
        ):
            return result.removesuffix(kwargs[field]) + relative
        return result


__all__ = ["CompileChildTool", "CompileImageReadTool"]
