"""Focused pipeline children using the existing AgentLoop and isolated file tools."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from copy import copy

import json_repair

from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.compile import CompileChildTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile.models import COMPILE_DRAFT_ROOT
from vikingbot.compile.pipeline_io import retry_allowed
from vikingbot.compile.plan import (
    FileResponse,
    RecordResponse,
    content_hash,
    result_schema,
)
from vikingbot.compile.renderer import validate_relative_file_path
from vikingbot.compile.skill_resources import EvidenceReader, SkillScript
from vikingbot.providers.base import LLMProvider


class ChildProvider(LLMProvider):
    """Route each child request through the task's bounded, metered provider calls."""

    def __init__(self, model, schema, stage="agent", *, submit=None):
        super().__init__()
        self.model = model
        self.stage = stage
        self.submit = submit
        self.failures = Counter()

    def get_default_model(self):
        return self.model.model

    async def chat(self, messages, tools=None, **kwargs):
        """Retry truncated output without executing incomplete tool calls."""
        while True:
            response = await self.model.call(
                self.stage, messages, tools, max_tokens=self.model.max_tokens
            )
            if response.finish_reason not in {"length", "max_tokens"}:
                return response
            error = "Agent output was truncated; use smaller writes or content_ref and complete tool calls"
            if not retry_allowed(self.failures, error):
                raise ValueError(error)
            self.model.metrics["repairs"] += 1
            messages = [*messages, {"role": "user", "content": error}]


async def complete_tool_result(result, session_key):
    """Keep scoped tool evidence complete for subsequent model requests.

    Child file tools cannot read shared preview spill files, so head/tail previews
    are not a lossless delivery mechanism for these isolated assignments.
    """
    return result


async def prepare_assignment(data, sandbox, root):
    """Store an oversized assignment in private scratch, replacing source bodies with read handles.

    The caller retains the full data for lineage validation and enables evidence reads.
    Candidate bodies use text files so line-based reads do not expand an entire JSON string.
    """
    assignment = {k: v for k, v in data.items() if k != "original_evidence"}
    assignment["inputs"] = []
    for item in data.get("inputs", []):
        payload = item.get("payload", {})
        if "text" in payload and "uri" in payload:
            payload = {k: v for k, v in payload.items() if k != "text"}
            payload["source_ranges"] = [item["id"]]
        assignment["inputs"].append({**item, "payload": payload})
    if "candidates" in data:
        assignment["candidates"] = []
        for index, candidate in enumerate(data["candidates"]):
            path = f"candidate-{index}.txt"
            await sandbox.write_file(f"{root}/{path}", candidate["content"])
            assignment["candidates"].append(
                {**{k: v for k, v in candidate.items() if k != "content"}, "content_file": path}
            )
    await sandbox.write_file(
        f"{root}/assignment.json", json.dumps(assignment, ensure_ascii=False, indent=2)
    )
    return {
        "assignment_file": "assignment.json",
        "instructions": "Read assignment.json with read_file offset/limit. Read source_ranges with read_evidence; "
        "source text is not included. Read candidate content_file paths in ranges. "
        "Process all assigned inputs before submitting the complete result.",
    }


class EmitResult(Tool):
    """Validate child results with independent retry allowances for distinct errors."""

    name = "emit"
    description = "Submit the complete typed transformation result."

    def __init__(
        self,
        schema,
        validate,
        sandbox=None,
        root="",
        data=None,
        metrics=None,
    ):
        self.schema, self.validate = schema, validate
        self.sandbox, self.root = sandbox, root
        self.result = None
        self.failures = Counter()
        self.can_retry = True
        self.last_error = ""
        self.data = data or {}
        self.metrics = metrics

    @property
    def parameters(self):
        parameters = result_schema(self.schema, self.data)
        parameters["properties"]["result_ref"] = {
            "type": "string",
            "description": "Alternative to inline fields: relative path to a complete JSON result "
            "in your scratch directory, matching this result schema. Use for large record collections.",
        }
        parameters["required"] = []
        return parameters

    async def execute(self, tool_context, **kwargs):
        """Return bounded validation feedback; only valid output terminates the child."""
        try:
            if (
                set(kwargs) == {"raw"}
                and isinstance(kwargs["raw"], str)
                and "raw" not in self.schema.model_fields
            ):
                kwargs = json_repair.loads(kwargs["raw"], stream_stable=True)
            if isinstance(kwargs, dict) and "result_ref" in kwargs:
                if len(kwargs) != 1 or self.sandbox is None:
                    raise ValueError("result_ref is an alternative to all inline result fields")
                relative = validate_relative_file_path(kwargs["result_ref"])
                raw = await self.sandbox.read_file_bytes(
                    f"{self.root}/{relative}", max_bytes=8 * 1024 * 1024
                )
                kwargs = json_repair.loads(raw.decode("utf-8"), stream_stable=True)
            result = self.schema.model_validate(kwargs)
            if isinstance(result, RecordResponse):
                total_bytes = 0
                for draft in result.records:
                    if draft.ready_content_ref is not None:
                        if self.sandbox is None or draft.ready_content is not None:
                            raise ValueError(
                                "Ready file reference requires scratch access without inline text"
                            )
                        relative = validate_relative_file_path(draft.ready_content_ref)
                        raw = await self.sandbox.read_file_bytes(
                            f"{self.root}/{relative}", max_bytes=8 * 1024 * 1024
                        )
                        total_bytes += len(raw)
                        if total_bytes > 16 * 1024 * 1024:
                            raise ValueError("Ready artifacts exceed 16 MiB per submission")
                        draft.ready_content = raw.decode("utf-8")
                        draft.ready_content_ref = None
            if isinstance(result, FileResponse):
                total_bytes = 0
                for draft in result.files:
                    if draft.content_ref is not None:
                        if self.sandbox is None or draft.content is not None or draft.patches:
                            raise ValueError(
                                "File reference requires scratch access without content/patches"
                            )
                        relative = validate_relative_file_path(draft.content_ref)
                        raw = await self.sandbox.read_file_bytes(
                            f"{self.root}/{relative}", max_bytes=8 * 1024 * 1024
                        )
                        total_bytes += len(raw)
                        if total_bytes > 16 * 1024 * 1024:
                            raise ValueError("Child artifacts exceed 16 MiB")
                        actual = content_hash(raw)
                        if draft.content_sha256 and draft.content_sha256 != actual:
                            raise ValueError("Scratch artifact hash mismatch")
                        draft.content, draft.content_sha256 = raw.decode("utf-8"), actual
                        draft.content_ref = None
            if self.validate:
                self.validate(result)
            self.result = result
            return "Result accepted."
        except (ValueError, TypeError, OSError) as exc:
            self.last_error = str(exc)[:1600]
            self.can_retry = retry_allowed(self.failures, self.last_error)
            if self.metrics is not None:
                self.metrics["validation_failures"] += 1
                if self.can_retry:
                    self.metrics["repairs"] += 1
            feedback = "Error: " + self.last_error
            if (
                self.can_retry
                and self.sandbox is not None
                and isinstance(kwargs, dict)
                and "result_ref" not in kwargs
            ):
                # Keep complete rejected data private; edits still pass every emit validator.
                candidate = f"rejected-{uuid.uuid4().hex}.json"
                try:
                    content = (
                        kwargs["raw"]
                        if set(kwargs) == {"raw"} and isinstance(kwargs["raw"], str)
                        else json.dumps(kwargs, ensure_ascii=False)
                    )
                    await self.sandbox.write_file(f"{self.root}/{candidate}", content)
                    feedback += (
                        f"\nRepair {candidate} with edit_file, then emit only "
                        f'{{"result_ref":"{candidate}"}}.'
                    )
                except (OSError, ValueError):
                    pass
            return feedback


def agent_runner(loop, session_key, connection, limits):
    """Bind an isolated child adapter; queueing/cancellation remain owned by Pipeline.

    Each call has fresh history, a private scratch root and the existing subagent
    iteration limit. Large files are submitted by reference, then hashed and checked.
    Skill reads use the authenticated task client; source/history catalogs stay hidden.
    """

    async def run(system, data, schema, validate, model, *, stage="agent"):
        child_id = uuid.uuid4().hex
        root = f"{COMPILE_DRAFT_ROOT}/{child_id}"
        evidence = EvidenceReader(model.files, data)
        submit = EmitResult(schema, validate, model.files.sandbox, root, data, model.metrics)
        assignment = data
        if not model.fits(system, data, schema):
            assignment = await prepare_assignment(data, model.files.sandbox, root)
            evidence.delivered.clear()
        registry = ToolRegistry(config=loop.config)
        registry.register(submit)
        if model.resources:
            registry.register(model.resources)
            registry.register(SkillScript(model.resources))
        if evidence.allowed - evidence.delivered:
            registry.register(evidence)
        for name in ("read_file", "write_file", "edit_file"):
            tool = loop.tools.get(name)
            if tool is not None:
                registry.register(CompileChildTool(tool, root, merge_only=True))
        child = copy(loop)
        child.provider = ChildProvider(model, schema, stage, submit=submit)
        child._preview_tool_result = complete_tool_result
        instructions = (
            "\nUse emit to submit. Supplied Skill attachments are complete; "
            "they fulfill the Skill's reading requirements without a tool call. Do not reread them. "
            "read_skill_resource is available for additional references. "
            "Only assigned evidence and your isolated scratch files are available. "
            "Prefer one inline emit for small finished results; scratch writing and rereading "
            "are optional, not mandatory verification steps. "
            "Write large/multiple files using write_file, then emit content_ref paths "
            "relative to your scratch root, without repeating their content. Runtime hashes "
            "the files and validates paths, revisions and source coverage before acceptance. "
            "For a large record collection, build a JSON result file incrementally with file "
            "tools, then emit only result_ref pointing to that file. Finished independent Map "
            "files may use ready_path plus ready_content_ref and concise routing payloads. "
            "Use run_skill_script for Python scripts supplied by the selected Skill. "
            "Scratch files are data; writing a script does not execute it. "
            "Use edit_file to repair existing JSON."
        )
        await child._run_agent_loop(
            messages=[
                {
                    "role": "system",
                    "content": system + instructions,
                },
                {"role": "user", "content": json.dumps(assignment, ensure_ascii=False)},
            ],
            session_key=session_key,
            publish_events=False,
            tool_registry=registry,
            stop_tool_names=["emit"],
            openviking_tool_names=set(registry.tool_names),
            openviking_connection=connection,
            allow_final_fallback=False,
            inject_write_experience=False,
            agent_id=child_id,
            context_compact_budget=None,
            max_iterations=limits.subagent_iterations,
            should_stop=lambda: not submit.can_retry,
        )
        if submit.result is None:
            raise ValueError(
                "Required agent transform ended without validated output: "
                + (submit.last_error or "No emit result was submitted")
            )
        return submit.result

    return run
