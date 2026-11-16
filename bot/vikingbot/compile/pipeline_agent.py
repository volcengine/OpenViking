"""Private file-tool workers; successful natural completion exposes their output directory."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from copy import copy

from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.compile import CompileChildTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile import file_ops
from vikingbot.compile.models import COMPILE_DRAFT_ROOT
from vikingbot.compile.pipeline_io import retry_allowed
from vikingbot.compile.renderer import validate_relative_file_path
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.providers.base import LLMProvider


class ChildProvider(LLMProvider):
    """Route each child request through the task's bounded, metered provider calls."""

    def __init__(self, model, stage="agent"):
        super().__init__()
        self.model = model
        self.stage = stage
        self.failures = Counter()

    def get_default_model(self):
        return self.model.model

    def should_inline_tool_result_media(self, model=None):
        """Send original evidence; model capability is decided by the endpoint."""
        return True

    async def chat(self, messages, tools=None, **kwargs):
        """Retry truncated output without executing incomplete tool calls."""
        while True:
            response = await self.model.call(
                self.stage, messages, tools, max_tokens=self.model.max_tokens
            )
            if response.finish_reason not in {"length", "max_tokens"}:
                return response
            error = "Agent output was truncated; use smaller writes and complete tool calls"
            if not retry_allowed(self.failures, error):
                raise ValueError(error)
            self.model.metrics["repairs"] += 1
            messages = [*messages, {"role": "user", "content": error}]


async def complete_tool_result(result, session_key, *, preserve_complete: bool = False):
    """Keep scoped tool evidence complete for subsequent model requests.

    Child file tools cannot read shared preview spill files, so head/tail previews
    are not a lossless delivery mechanism for these isolated assignments.
    """
    return result


class TargetReader(Tool):
    """Read a known target path and remember its revision before allowing replacement."""

    name = "read_target_file"
    description = "Read an existing file under the output destination before updating it."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to request.to."}
        },
        "required": ["path"],
    }

    def __init__(self, runtime):
        self.runtime = runtime

    async def execute(self, tool_context=None, path=""):
        """Return existing UTF-8 Markdown or an explicit missing-file result; errors propagate."""
        path = validate_relative_file_path(path)
        content = await file_ops.load_old(self.runtime, path)
        if isinstance(content, bytes):
            raise ValueError("Target is not UTF-8 text")
        return json.dumps({"path": path, "content": content}, ensure_ascii=False)


def agent_runner(loop, session_key, connection, limits):
    """Bind metered AgentLoops with isolated file tools and no submission protocol."""

    async def run(system, data, model, *, stage, runtime):
        """Return a private output root only after natural completion; failures expose no files."""
        child_id = uuid.uuid4().hex
        root = f"{COMPILE_DRAFT_ROOT}/{child_id}"
        output_root = f"{root}/output"
        sandbox = model.files.sandbox
        assignment = dict(data)
        assignment["documents"] = []
        readonly_paths = {"assignment.json": f"{root}/assignment.json"}
        for index, document in enumerate(data.get("documents", [])):
            path = f"inputs/{index}.md"
            await sandbox.write_file(
                f"{root}/{path}", await sandbox.read_file(document["workspace_path"])
            )
            assignment["documents"].append({"path": document["path"], "content_file": path})
            readonly_paths[path] = f"{root}/{path}"
        await sandbox.write_file(
            f"{root}/assignment.json", json.dumps(assignment, ensure_ascii=False)
        )
        registry = ToolRegistry(config=loop.config)
        for name in ("read_file", "write_file", "edit_file"):
            tool = loop.tools.get(name)
            if tool is None:
                raise ValueError(f"Compile requires {name}")
            registry.register(
                CompileChildTool(tool, output_root, merge_only=True, readonly_paths=readonly_paths)
            )
        evidence = EvidenceReader(model.files, data)
        if evidence.allowed:
            registry.register(evidence)
        registry.register(runtime.resources)
        registry.register(TargetReader(runtime))
        search = runtime.tools.get("openviking_search")
        if search is not None and runtime.tool_context is not None:

            async def search_target(tool_context=None, **arguments):
                arguments["target_uri"] = runtime.target
                return await search.execute(runtime.tool_context, **arguments)

            scoped = copy(search)
            scoped.execute = search_target
            registry.register(scoped)
        child = copy(loop)
        child.provider = ChildProvider(model, stage)
        child.preview_hook = complete_tool_result
        final, _, _, _, _ = await child._run_agent_loop(
            messages=[
                {
                    "role": "system",
                    "content": system + "\nRead assignment.json with read_file. "
                    "Read all assigned materials. assignment.json and assigned inputs/ paths "
                    "are read-only. Use write_file and edit_file for deliverable Markdown, "
                    "with paths relative to the publication destination. Read and edit your "
                    "outputs using the same paths. Write only deliverables, without scratch files. "
                    "Finish with a brief completion message after all writes succeed. "
                    "A completion message alone does not produce files.",
                },
                {
                    "role": "user",
                    "content": "Process assignment.json and write the requested Markdown files.",
                },
            ],
            session_key=session_key,
            publish_events=False,
            tool_registry=registry,
            openviking_tool_names=set(registry.tool_names),
            openviking_connection=connection,
            allow_final_fallback=False,
            require_natural_completion=True,
            inject_write_experience=False,
            agent_id=child_id,
            context_compact_budget=None,
            max_iterations=limits.subagent_iterations,
        )
        if final is None:
            raise ValueError("Compile worker exhausted its tool rounds before completion")
        return output_root

    return run
