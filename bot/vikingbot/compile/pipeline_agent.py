"""Private file-tool workers; natural completion returns their validated Markdown files."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from copy import copy

from vikingbot.agent.loop import _PlainTextDelivered, _PlainTextFinal
from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.compile import CompileChildTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile import file_ops
from vikingbot.compile.models import COMPILE_DRAFT_ROOT
from vikingbot.compile.pipeline_io import retry_allowed
from vikingbot.compile.renderer import validate_relative_file_path
from vikingbot.compile.results import Document, IncompleteWorkerOutput, InvalidWorkerOutput
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
    description = "Read a target file you need to update. Use read_file for your drafts."
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

    async def run(system, data, model, *, stage, runtime) -> list[Document]:
        """Return validated files only after natural completion; failures expose no files."""
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
        can_update_target = runtime.target_has_content is not False or bool(runtime.changes)
        if can_update_target:
            registry.register(TargetReader(runtime))
        search = runtime.tools.get("openviking_search") if can_update_target else None
        if search is not None and runtime.tool_context is not None:

            async def search_target(tool_context=None, **arguments):
                arguments["target_uri"] = runtime.target
                return await search.execute(runtime.tool_context, **arguments)

            scoped = copy(search)
            scoped.execute = search_target
            registry.register(scoped)
        child = copy(loop)
        child.provider = ChildProvider(model, stage)
        child._preview_tool_result = complete_tool_result

        completed_outputs: list[Document] | None = None
        validation_error: InvalidWorkerOutput | None = None
        completion_error: Exception | None = None

        async def check_completion(context):
            nonlocal completed_outputs, validation_error, completion_error
            try:
                completed_outputs = await runtime.collect_outputs(output_root, stage)
            except InvalidWorkerOutput as error:
                validation_error = error
                messages = child.context.add_assistant_message(context.messages, context.text, [])
                return _PlainTextDelivered(
                    messages=messages,
                    tools_used=[],
                    next_prompt=(
                        f"Fix only this output issue: {error}. Keep valid Markdown and its facts. "
                        "Once fixed, reply Done."
                    ),
                )
            except Exception as error:
                # AgentLoop swallows callback exceptions for ordinary chat replies.
                # Let the pipeline handle unusable outputs and infrastructure failures.
                completion_error = error
            return _PlainTextFinal()

        async def remind_completion(iteration):
            remaining = limits.subagent_iterations - iteration + 1
            if remaining in (20, 5, 1):
                return (
                    f"{remaining} model/tool rounds remain, including this one. "
                    "Reply Done as soon as the requested files are ready."
                )
            return None

        final, _, _, _, _ = await child._run_agent_loop(
            messages=[
                {
                    "role": "system",
                    "content": system + "\nRead assignment.json with read_file, then read all "
                    "assigned evidence. Reuse evidence and Skill rules already in context. "
                    "Check each page against its evidence and the applicable Skill rules before "
                    "writing it. Write only deliverable Markdown at publication-relative paths. "
                    "Read more or edit a written page to resolve a specific remaining issue. "
                    "Finish with a brief completion message after all required writes succeed.",
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
            on_plain_text=check_completion,
            status_note_provider=remind_completion,
            inject_write_experience=False,
            agent_id=child_id,
            context_compact_budget=None,
            max_iterations=limits.subagent_iterations,
        )
        if completion_error is not None:
            raise completion_error
        if final is None or completed_outputs is None:
            message = "Compile worker exhausted its tool rounds before completion"
            if validation_error is not None:
                message += f". Last output validation error: {validation_error}"
            raise IncompleteWorkerOutput(message) from validation_error
        return completed_outputs

    return run
