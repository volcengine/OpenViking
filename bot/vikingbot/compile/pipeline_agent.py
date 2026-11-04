"""Focused pipeline children using the existing AgentLoop and isolated file tools."""

from __future__ import annotations

import asyncio
import inspect
import json
import posixpath
import unicodedata
import uuid
from collections import Counter
from copy import copy

import json_repair

from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.compile import CompileChildTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile import file_ops
from vikingbot.compile.agent_io import make_tool_result_preview
from vikingbot.compile.agent_io import prepare_assignment as prepare_reduce_assignment
from vikingbot.compile.checkpoint import CheckpointCompletion, CheckpointTool
from vikingbot.compile.execution import ReduceExecutionError, reduce_budget
from vikingbot.compile.hashing import content_hash, digest
from vikingbot.compile.models import COMPILE_DRAFT_ROOT
from vikingbot.compile.pipeline_io import TaskFiles, retry_allowed
from vikingbot.compile.renderer import validate_relative_file_path
from vikingbot.compile.results import FileResponse, RecordResponse
from vikingbot.compile.schemas import result_schema
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.providers.base import LLMProvider
from vikingbot.utils.helpers import cal_str_tokens


class ChildProvider(LLMProvider):
    """Meter child requests and bound Reduce context without discarding unsaved work.

    context_tokens reserves output space against the configured text-token capacity;
    closing starts with headroom for saving state. Zero disables context checks.
    Terminal tool calls are isolated so checkpoint and final acceptance cannot race.
    """

    def __init__(
        self,
        model,
        schema,
        stage="agent",
        *,
        submit=None,
        checkpoint=None,
        context_tokens=0,
    ):
        super().__init__()
        self.model = model
        self.stage = stage
        self.submit = submit
        self.failures = Counter()
        self.checkpoint = checkpoint
        self.checkpoint_due = False
        self.context_tokens = context_tokens
        # Tool IDs connect write requests to successful acknowledgements in this window.
        self.write_calls: dict[str, tuple[str, str]] = {}

    def get_default_model(self):
        return self.model.model

    def supports_tool_result_media(self, model=None):
        return self.model.provider.supports_tool_result_media(model or self.model.model)

    @staticmethod
    def request_text(messages, tools):
        """Estimate text/schema load without treating native media encodings as text tokens.

        The original messages remain intact; provider media quotas still apply.
        """
        text_messages = []
        for message in messages:
            value = dict(message)
            if isinstance(value.get("content"), list):
                value["content"] = [
                    part if part.get("type") == "text" else {"type": part.get("type")}
                    for part in value["content"]
                ]
            text_messages.append(value)
        return json.dumps([text_messages, tools], ensure_ascii=False)

    async def chat(self, messages, tools=None, **kwargs):
        """Return a complete, executable response under the window's request budget.

        Truncated responses, conflicting terminal calls and repeated successful
        writes retry before tool execution; exhausted repairs or capacity are terminal.
        """
        while True:
            if self.checkpoint and self.context_tokens:
                available = self.context_tokens - self.model.max_tokens
                # Packing targets do not bound context; reserve 15% of input space for closing.
                if cal_str_tokens(self.request_text(messages, tools), text_type="mixed") >= (
                    available * 0.85
                ):
                    self.checkpoint_due = True
            if self.checkpoint_due:
                tools = [
                    tool
                    for tool in tools or []
                    if tool["function"]["name"] in {"emit", "checkpoint", "write_file", "edit_file"}
                ]
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": "This context window is ending. Finish with emit only if all work "
                        "is complete; otherwise persist the business state and call checkpoint now. "
                        "Reuse state and drafts already written; do not rewrite them to prepare a "
                        "checkpoint. state_ref is saved automatically and must not appear in draft_refs. "
                        "Repair rejected checkpoint arguments instead of rewriting unchanged files. "
                        "Do not start new reading or analysis. A checkpoint is not task completion.",
                    },
                ]
            if self.context_tokens and (
                cal_str_tokens(self.request_text(messages, tools), text_type="mixed")
                + self.model.max_tokens
                > self.context_tokens
            ):
                raise ReduceExecutionError(
                    "Reduce context cannot fit a complete request and output reserve; "
                    "no history is discarded without a validated checkpoint"
                )
            response = await self.model.call(
                self.stage, messages, tools, max_tokens=self.model.max_tokens
            )
            if self.checkpoint_due and any(
                call.name not in {"emit", "checkpoint", "write_file", "edit_file"}
                for call in response.tool_calls
            ):
                error = "Window closing: only write_file, edit_file, checkpoint or final emit is allowed"
                if not retry_allowed(self.failures, error):
                    raise ReduceExecutionError(error)
                self.model.metrics["repairs"] += 1
                messages = [*messages, {"role": "user", "content": error}]
                continue
            if (
                self.checkpoint
                and any(call.name in {"emit", "checkpoint"} for call in response.tool_calls)
                and len(response.tool_calls) != 1
            ):
                error = "Submit emit or checkpoint alone, without any other tool calls"
                if not retry_allowed(self.failures, error):
                    raise ReduceExecutionError(error)
                self.model.metrics["repairs"] += 1
                messages = [*messages, {"role": "user", "content": error}]
                continue
            if response.finish_reason not in {"length", "max_tokens"}:
                if self.checkpoint:
                    writes = {
                        call.id: (
                            unicodedata.normalize(
                                "NFC", posixpath.normpath(call.arguments["path"].replace("\\", "/"))
                            ).casefold(),
                            digest([call.name, call.arguments]),
                        )
                        for call in response.tool_calls
                        if call.name in {"write_file", "edit_file"}
                        and isinstance(call.arguments, dict)
                        and isinstance(call.arguments.get("path"), str)
                    }
                    # Shell commands can mutate scratch without file-tool acknowledgements.
                    if any(call.name == "exec" for call in response.tool_calls):
                        self.write_calls.clear()
                        writes.clear()
                    completed = {}
                    for message in messages:
                        if (
                            message.get("role") == "tool"
                            and message.get("tool_call_id") in self.write_calls
                            and isinstance(message.get("content"), str)
                            and not message["content"].lstrip().startswith("Error:")
                        ):
                            path, signature = self.write_calls[message["tool_call_id"]]
                            completed[path] = signature
                    if any(completed.get(path) == signature for path, signature in writes.values()):
                        error = (
                            "This write already succeeded in this window. Reuse the saved file "
                            "and continue unfinished work, or emit if finished. Use checkpoint "
                            "only to hand off unfinished work when the window is ending. "
                            "Exclude state_ref from draft_refs; repair rejected checkpoint "
                            "arguments without rewriting unchanged files."
                        )
                        if not retry_allowed(self.failures, error):
                            raise ReduceExecutionError(error)
                        self.model.metrics["repairs"] += 1
                        messages = [*messages, {"role": "user", "content": error}]
                        continue
                    self.write_calls.update(writes)
                return response
            error = "Agent output was truncated; use smaller writes or content_ref and complete tool calls"
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


async def prepare_assignment(data, sandbox, root):
    """Store an oversized single-window assignment with its source read handles.

    Derived record payloads remain inline; candidate bodies use text files. The
    complete original assignment remains the caller's validation scope.
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
            if candidate.get("binary"):
                assignment["candidates"].append(candidate)
                continue
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
        checkpoint=None,
    ):
        self.schema, self.validate = schema, validate
        self.sandbox, self.root = sandbox, root
        self.result = None
        self.failures = Counter()
        self.can_retry = True
        self.last_error = ""
        self.data = data or {}
        self.metrics = metrics
        self.checkpoint = checkpoint

    async def read_content(self, relative: str) -> bytes:
        """Read private scratch or an assigned ready snapshot; foreign snapshots are not accessible."""
        supplied = {
            item.get("ready_file", {}).get("content_ref") for item in self.data.get("inputs", [])
        }
        supplied.update(item.get("content_ref") for item in self.data.get("candidates", []))
        if relative in supplied:
            saved = await TaskFiles(self.sandbox).get(relative)
            raw = file_ops.file_bytes(saved)
            actual = content_hash(raw)
            if saved.get("sha256", actual) != actual:
                raise ValueError("Stored file hash mismatch")
            return raw
        return await self.sandbox.read_file_bytes(f"{self.root}/{relative}")

    @property
    def parameters(self):
        parameters = result_schema(self.schema, self.data)
        if self.checkpoint:
            parameters["properties"]["completion"] = CheckpointCompletion.model_json_schema()
        if self.sandbox is None:
            return parameters
        parameters["properties"]["result_ref"] = {
            "type": "string",
            "description": "Relative path used with write_file for a JSON file containing the "
            "complete emit arguments, without result_ref. Submit result_ref alone instead of "
            "inline fields. Use for large results.",
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
                if self.sandbox is None:
                    raise ValueError(
                        "result_ref is unavailable; submit inline emit arguments, without a result file"
                    )
                if len(kwargs) != 1:
                    raise ValueError("result_ref is an alternative to all inline result fields")
                relative = validate_relative_file_path(kwargs["result_ref"])
                raw = await self.sandbox.read_file_bytes(f"{self.root}/{relative}")
                kwargs = json_repair.loads(raw.decode("utf-8"), stream_stable=True)
            if self.checkpoint:
                completion = kwargs.get("completion") if isinstance(kwargs, dict) else None
                self.checkpoint.validate_completion(completion)
                payload = (
                    {key: value for key, value in kwargs.items() if key != "completion"}
                    if isinstance(kwargs, dict)
                    else kwargs
                )
            else:
                payload = kwargs
            result = self.schema.model_validate(payload)
            if isinstance(result, RecordResponse):
                for draft in result.records:
                    if draft.ready_content_ref is not None:
                        if (
                            self.sandbox is None
                            or draft.ready_content is not None
                            or draft.ready_content_base64 is not None
                        ):
                            raise ValueError(
                                "Ready file reference requires scratch access without inline text"
                            )
                        relative = validate_relative_file_path(draft.ready_content_ref)
                        raw = await self.read_content(relative)
                        body = file_ops.encode_content(raw)
                        draft.ready_content = body["content"]
                        draft.ready_content_base64 = body["content_base64"]
                        draft.ready_content_ref = None
            if isinstance(result, FileResponse):
                for draft in result.files:
                    if draft.content_ref is not None:
                        if (
                            self.sandbox is None
                            or draft.content is not None
                            or draft.content_base64 is not None
                            or draft.patches
                        ):
                            raise ValueError(
                                "File reference requires scratch access without content/patches"
                            )
                        relative = validate_relative_file_path(draft.content_ref)
                        raw = await self.read_content(relative)
                        actual = content_hash(raw)
                        if draft.content_sha256 and draft.content_sha256 != actual:
                            raise ValueError("Scratch artifact hash mismatch")
                        body = file_ops.encode_content(raw)
                        draft.content, draft.content_base64 = (
                            body["content"],
                            body["content_base64"],
                        )
                        draft.content_sha256 = actual
                        draft.content_ref = None
            if self.validate:
                checked = self.validate(result)
                if inspect.isawaitable(checked):
                    await checked
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

    Reduce windows share private scratch and validated progress under a group budget.
    Other transforms use a single window. Large files are submitted by reference,
    then hashed and checked; Skill reads use the authenticated task client.
    """

    async def run(system, data, schema, validate, model, *, stage="agent"):
        """Return validated output while preserving original assignment scope across windows.

        Only Reduce uses the context-local continuation budget. Missing progress,
        invalid snapshots or budget exhaustion fail without publishing checkpoints.
        """
        budget = reduce_budget.get() if stage == "reduce_agent" else None
        if budget:
            fingerprint = digest([system, data, schema.model_json_schema(), model.identity])
            session = budget.sessions.setdefault(fingerprint, {})
        else:
            session = {}
        root = session.setdefault("root", f"{COMPILE_DRAFT_ROOT}/{uuid.uuid4().hex}")
        checkpoint = None
        if budget:
            checkpoint = session.get("checkpoint")
            if checkpoint is None:
                checkpoint = CheckpointTool(
                    model.files, model.files.sandbox, root, data, fingerprint, model.metrics
                )
                session["checkpoint"] = checkpoint
        evidence = EvidenceReader(model.files, data)
        submit = EmitResult(
            schema,
            validate,
            model.files.sandbox,
            root,
            data,
            model.metrics,
            checkpoint=checkpoint,
        )
        assignment = session.get("assignment", data)
        if not model.fits(system, data, schema) and "assignment" not in session:
            prepare = prepare_reduce_assignment if checkpoint else prepare_assignment
            assignment = await prepare(data, model.files.sandbox, root)
            session["assignment"] = assignment
            evidence.delivered.clear()
        registry = ToolRegistry(config=loop.config)
        registry.register(submit)
        if checkpoint:
            registry.register(checkpoint)
        if model.resources:
            registry.register(model.resources)
        if evidence.allowed - evidence.delivered or (checkpoint and evidence.allowed):
            registry.register(evidence)
        for name in ("read_file", "write_file", "edit_file"):
            tool = loop.tools.get(name)
            if tool is not None:
                registry.register(CompileChildTool(tool, root, merge_only=True))
        tool = loop.tools.get("exec")
        if (
            loop.config.sandbox.backend == "direct"
            and not loop.config.sandbox.backends.direct.allow_compile_exec
        ):
            tool = None
        if tool is not None:
            await model.files.sandbox.write_file(f"{root}/.keep", "")
            registry.register(CompileChildTool(tool, root))
            system += (
                "\nUse exec with pipelines, Python or shell commands when they improve efficiency."
            )
        child = copy(loop)
        child._preview_tool_result = (
            make_tool_result_preview(model.files.sandbox, root, max_chars=4096)
            if checkpoint
            else complete_tool_result
        )
        instructions = (
            "\nUse emit to submit. Supplied Skill attachments are complete; "
            "they fulfill the Skill's reading requirements without a tool call. Do not reread them. "
            "read_skill_resource is available for additional references. "
            "Use assigned evidence, Skill attachments and your scratch files for this job. "
            "Prefer one inline emit for small finished results; scratch writing and rereading "
            "are optional, not mandatory verification steps. "
            "Write large/multiple files using write_file, then emit content_ref paths "
            "relative to your scratch root, without repeating their content. Runtime hashes "
            "the files and validates paths, revisions and source coverage before acceptance. "
            "For a large record collection, build a JSON result file incrementally with file "
            "tools, then emit only result_ref pointing to that file. Finished independent Map "
            "files may use ready_path plus ready_content_ref and concise routing payloads. "
            "read_skill_resource provides local paths for Skill scripts and their dependencies. "
            "Use edit_file to repair existing JSON."
        )
        if stage == "map_agent":
            instructions = ""  # Map defines its own file access and submission rules.
        if checkpoint:
            instructions += (
                "\nComplete assignments directly with emit when they fit this window; writing "
                "state or calling checkpoint is not required for completion. Work in bounded batches. "
                "For unfinished work that needs another window, persist cumulative state as JSON "
                "in your scratch directory; preserve exact data, provenance and unresolved work. "
                "Use checkpoint before the context window ends, with cumulative processed and "
                "pending input IDs, remaining synthesis/checks and draft references. Reading an "
                "input alone does not complete its processing. Checkpoints are not final outputs. "
                "state_ref is snapshotted automatically: exclude it from draft_refs. Do not "
                "checkpoint just to defer final emit when the complete result is ready. "
                "Continue existing state and drafts rather than rereading or regenerating them. "
                "After a checkpoint, final emit requires completion accounting for remaining "
                "inputs (completed_inputs or excluded_inputs with reasons) and pending_tasks=[]. "
                "Original inputs and historical revisions remain the final validation scope."
            )

        async def window_note(iteration):
            """Reserve closing turns for an explicit checkpoint without changing AgentLoop."""
            if checkpoint and iteration >= max(1, limits.subagent_iterations - 3):
                child.provider.checkpoint_due = True
                return "Window budget is ending: persist state and checkpoint, or emit the complete result."
            return None

        # A checkpoint present at invocation entry needs rollback after a failed attempt.
        restore_on_retry = bool(checkpoint and checkpoint.latest)
        while True:
            if budget:
                budget.start_window()
                model.metrics["reduce_windows"] += 1
            if checkpoint:
                checkpoint.submitted = False
                if checkpoint.latest:
                    if restore_on_retry:
                        await checkpoint.restore()
                        restore_on_retry = False
                    evidence.delivered.clear()
                    if "assignment" not in session:
                        session["assignment"] = await prepare_reduce_assignment(
                            data, model.files.sandbox, root
                        )
                    assignment = session["assignment"]
            context = dict(assignment)
            if checkpoint and checkpoint.latest:
                context["checkpoint"] = {
                    "manifest_file": checkpoint.latest["manifest_file"],
                    "next_action": checkpoint.latest["next_action"],
                    "remaining_inputs": len(checkpoint.latest["pending_inputs"]),
                    "instructions": "Read the checkpoint manifest and saved state; preserve its "
                    "completed work and finish the remaining synthesis before final emit.",
                }
            child.provider = ChildProvider(
                model,
                schema,
                stage,
                submit=submit,
                checkpoint=checkpoint,
                context_tokens=loop.config.agents.context_window_tokens if checkpoint else 0,
            )
            messages = [
                {"role": "system", "content": system + instructions},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ]
            seconds = min(budget.remaining(), budget.window_seconds) if budget else None
            options = {"status_note_provider": window_note} if checkpoint else {}
            execution = child._run_agent_loop(
                messages=messages,
                session_key=session_key,
                publish_events=False,
                tool_registry=registry,
                stop_tool_names=["emit", "checkpoint"] if checkpoint else ["emit"],
                openviking_tool_names=set(registry.tool_names),
                openviking_connection=connection,
                allow_final_fallback=False,
                inject_write_experience=False,
                agent_id=root.rsplit("/", 1)[-1],
                context_compact_budget=None,
                max_iterations=limits.subagent_iterations,
                should_stop=lambda: (
                    not submit.can_retry or (checkpoint is not None and not checkpoint.can_retry)
                ),
                **options,
            )
            try:
                if budget:
                    async with asyncio.timeout(seconds):
                        await execution
                else:
                    await execution
            except TimeoutError as exc:
                if budget:
                    raise budget.error(
                        "window deadline exhausted; latest checkpoint retained"
                    ) from exc
                raise
            if submit.result is not None:
                return submit.result
            if checkpoint and checkpoint.submitted:
                continue
            error = (
                submit.last_error
                or (checkpoint.last_error if checkpoint else "")
                or (
                    "No emit result or validated checkpoint was submitted"
                    if checkpoint
                    else "No emit result was submitted"
                )
            )
            if budget:
                remaining = (
                    len(checkpoint.latest["pending_inputs"])
                    if checkpoint.latest
                    else len(data.get("inputs", []))
                )
                raise budget.error(f"{error}; remaining inputs: {remaining}")
            raise ValueError("Required agent transform ended without validated output: " + error)

    return run
