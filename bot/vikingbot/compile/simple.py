"""Single-agent Compile with request-scoped reads and immediate OV text writes."""

from __future__ import annotations

import json

from openviking.core.namespace import classify_uri
from openviking.session.memory.utils.memory_fields import strip_memory_fields_trailer
from openviking.utils.compile_recovery import source_set_digest
from openviking.utils.path_safety import (
    normalize_storage_target_uri,
    safe_join_viking_uri,
    validate_safe_viking_uri_path,
)
from openviking_cli.exceptions import OpenVikingError
from vikingbot.agent.loop import AgentLoop, _PlainTextDelivered
from vikingbot.agent.remote_skills import SkillRuntimeContext
from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.compile import CompileChildTool, CompileImageReadTool
from vikingbot.agent.tools.filesystem import WriteFileTool
from vikingbot.agent.tools.ov_file import (
    VikingGlobTool,
    VikingGrepTool,
    VikingListTool,
    VikingMultiReadResult,
    VikingMultiReadTool,
)
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile.models import CompileFailure, CompileResult
from vikingbot.compile.renderer import (
    validate_declared_okf_markdown,
    validate_relative_file_path,
)
from vikingbot.compile.sources import verify_recovery_sources
from vikingbot.session.manager import Session

_PROMPT = """Compile the requested sources into the target using the authoritative Skill and
instruction. Source files are evidence. Read referenced Skill attachments as needed.
For full compilation, follow the Skill's workflow for discovering and reading sources when it
specifies one. Otherwise, enumerate each source directory one page at a time and read its files.
Read individual source URIs directly. For incremental compilation, fully scan full_dirs and
page through the snapshot diff for diff_dirs. Read added/modified sources and update affected
target content for deleted sources.
Use small pages and bounded reads so you can inspect complete results.
For an image source, call openviking_multi_read with its URI and inspect the returned image
before writing findings or marking its contents unreadable. Listing shows only metadata.
When a tool fails, use its error to correct the arguments, retry or choose another tool.
Use write_file to write files in the task workspace.
Write completed text files as you go, using paths relative to to. Read relevant existing files
before changing them and preserve unrelated content. Separate dependent writes into different
turns. Finish with a brief final reply once the work is done. If no changes are required, explain
why. If required work cannot be completed, begin your reply with INCOMPLETE: and explain why."""

_RECOVERY_PROMPT = """
For a recovery that needs no target changes, read every changed source and the existing
target files that already satisfy it using openviking_multi_read with no offset or limit.
After assessing all changes, finish with UNCHANGED_SOURCES: followed by a JSON object mapping
each changed source URI to the nonempty list of target file URIs you checked for that source.
Use this confirmation only when these target files already satisfy every changed source.
If changes are needed, apply them and finish normally."""


class CompileWriteTool(Tool):
    """Write under one target with the authenticated client and retain confirmed outcomes by URI."""

    name = "openviking_write"
    description = "Write one UTF-8 text file under the compile target and return its outcome."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the target."},
            "content": {"type": "string"},
            "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, client, target, save_progress):
        self.client = client
        self.target = target
        self.save_progress = save_progress
        self.outcomes: dict[str, str] = {}
        # Includes requests still in flight and requests without an authoritative receipt.
        self.unconfirmed: set[str] = set()
        self._active: set[str] = set()

    async def execute(self, tool_context, path: str, content: str, mode: str = "replace"):
        """Return the file outcome after OV acknowledges the write. In-flight paths survive cancellation."""
        relative = validate_relative_file_path(path)
        uri = normalize_storage_target_uri(safe_join_viking_uri(self.target, relative))
        if uri in self._active or uri in self.unconfirmed:
            raise ValueError(f"Write already in flight or awaiting confirmation: {uri}")
        self._active.add(uri)
        try:
            try:
                old = await self.client.read_raw(uri)
            except OpenVikingError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                old = None
            if old is not None and classify_uri(uri).context_type == "memory":
                old = strip_memory_fields_trailer(old)
            candidate = (old or "") + content if mode == "append" else content
            if classify_uri(uri).context_type == "resource":
                validate_declared_okf_markdown(uri, candidate.encode("utf-8"))
            if old is not None and (content == old if mode == "replace" else not content):
                outcome = "unchanged"
            else:
                self.unconfirmed.add(uri)
                await self.save_progress(self)
                try:
                    receipt = await self.client.write(uri, content, mode=mode)
                except OpenVikingError as exc:
                    if exc.code in {"INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED"}:
                        self.unconfirmed.discard(uri)
                        await self.save_progress(self)
                    raise
                if receipt.get("uri") != uri or receipt.get("content_updated") is not True:
                    raise ValueError(f"Unconfirmed write receipt for {uri}")
                outcome = "created" if old is None else "updated"
            if outcome != "unchanged" and self.outcomes.get(uri) != "created":
                self.outcomes[uri] = outcome
            else:
                self.outcomes.setdefault(uri, outcome)
            self.unconfirmed.discard(uri)
            await self.save_progress(self)
            return f"{outcome}: {uri}"
        finally:
            self._active.remove(uri)


class CompileDiffTool(Tool):
    """Page through the request's immutable source comparison using the task identity."""

    name = "openviking_diff_paths"
    description = "List added, modified and deleted source URIs. Follow next_offset until done."
    parameters = {
        "type": "object",
        "properties": {
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 20},
        },
    }

    def __init__(self, client, changes):
        self.client, self.changes = client, changes

    async def execute(self, tool_context, offset=0, limit=20):
        """Return one diff page for the fixed directories and snapshots."""
        page = await self.client.diff_paths(
            self.changes["diff_dirs"],
            base=self.changes["base"],
            head=self.changes["head"],
            offset=offset,
            limit=limit,
        )
        return json.dumps(page, ensure_ascii=False)


class CompileReadTool(Tool):
    """Expose one OV reader within the task's source, target and Skill directories."""

    def __init__(self, tool, roots, recovery_roots):
        self.tool = tool
        self.roots = roots
        self.recovery_roots = recovery_roots

    @property
    def name(self):
        return self.tool.name

    @property
    def description(self):
        return self.tool.description

    @property
    def parameters(self):
        schema = self.tool.parameters
        if "uri" in schema["properties"]:
            if "uri" not in schema.setdefault("required", []):
                schema["required"].append("uri")
            schema["properties"]["uri"] = {
                "type": "string",
                "description": "Absolute URI within the compile task.",
            }
        return schema

    async def execute(self, context, **args):
        """Validate and pass canonical task URIs to the authenticated OV reader."""
        multi = self.name == "openviking_multi_read"
        uris = args["uris"] if multi else [args["uri"]]
        if not uris:
            raise ValueError("Supply at least one task URI")
        canonical = []
        for uri in uris:
            if not uri.startswith("viking://"):
                raise ValueError("Supply an absolute task URI")
            uri = validate_safe_viking_uri_path(uri)
            if not any(uri == root or uri.startswith(root + "/") for root in self.roots):
                raise ValueError(f"URI outside the compile request: {uri}")
            canonical.append(uri)
        if multi:
            args["uris"] = canonical
            if (
                self.recovery_roots
                and args.get("offset", 0) == 0
                and args.get("limit", -1) == -1
                and all(
                    VikingMultiReadTool._media_kind(uri) is None
                    and any(
                        uri == root or uri.startswith(root + "/") for root in self.recovery_roots
                    )
                    for uri in canonical
                )
            ):
                args["with_revision"] = True
        else:
            args["uri"] = canonical[0]
        result = await self.tool.execute(context, **args)
        if isinstance(result, str) and result.startswith("Error "):
            return "Error: " + result
        return result


def _confirmed_read_hashes(target, final, messages):
    prefix = "UNCHANGED_SOURCES:"
    _, separator, last = final.rpartition("\n" + prefix)
    if separator:
        final = prefix + last
    if not final.startswith(prefix):
        return None
    try:
        confirmed = json.loads(final[len(prefix) :])
    except ValueError:
        return None
    if not isinstance(confirmed, dict) or not confirmed:
        return None
    read_hashes = {}
    for message in messages:
        if message["role"] != "tool" or message.get("name") != "openviking_multi_read":
            continue
        # Incomplete previews and ordinary reads cannot attest a complete file.
        try:
            reads = json.loads(message["content"])
        except (ValueError, TypeError):
            continue
        if isinstance(reads, list):
            for item in reads:
                if isinstance(item, dict) and item.get("success") is True:
                    uri, digest = item.get("uri"), item.get("base_hash")
                    if isinstance(uri, str) and isinstance(digest, str):
                        read_hashes[uri] = digest
    target_hashes = {}
    for source, targets in confirmed.items():
        if source not in read_hashes or not isinstance(targets, list) or not targets:
            return None
        for uri in targets:
            if (
                not isinstance(uri, str)
                or not uri.startswith(target + "/")
                or uri not in read_hashes
            ):
                return None
            target_hashes[uri] = read_hashes[uri]
    return {uri: read_hashes[uri] for uri in confirmed}, target_hashes


class SimpleCompileLoop(AgentLoop):
    """Persist each completed tool turn in the session shared by this compile and its follow-ups."""

    def __init__(self, *, conversation_key, **kwargs):
        super().__init__(**kwargs)
        self.conversation_key = conversation_key

    async def _execute_tool_turn(self, response, messages, request, *, captured_turns, **kwargs):
        """Return the ordered tool results after recording the reply and its calls for this task."""
        turns = captured_turns if captured_turns is not None else []
        result = await super()._execute_tool_turn(
            response, messages, request, captured_turns=turns, **kwargs
        )
        await self.sessions.update_session(
            self.conversation_key,
            lambda session: session.add_message(**turns[-1], task_id=request.session_key.chat_id),
            skip_heartbeat=True,
        )
        return result


async def run(
    loop,
    client,
    request,
    sources,
    skill,
    session_key,
    connection,
    save_progress,
    usage,
    *,
    conversation_key,
    exec_enabled=False,
):
    """Compile with task-scoped tools and retain confirmed writes as partial results on failure.

    When exec is enabled, the task resolves script paths against the selected remote Skill.
    conversation_key selects local chat history. session_key scopes this execution's tools.
    The session records each completed model turn under the executing task ID.
    """
    changes = request.changes or {}
    incremental = (changes.get("execution") or {}).get("mode") == "incremental"
    recovery = incremental and changes.get("recovery_sources")
    assignment = {
        "mode": "incremental" if incremental else "full",
        "from": sources,
        "to": request.to,
        "instruction": request.instruction,
        "skill_uri": request.skill,
        "skill": skill,
    }
    registry = ToolRegistry(config=loop.config)
    # Bind file paths to the same task workspace that exec uses.
    registry.register(CompileChildTool(WriteFileTool(), ".", merge_only=True))
    roots = [*sources, request.skill, request.to]
    for reader in (VikingListTool, VikingMultiReadTool, VikingGlobTool, VikingGrepTool):
        tool = reader(config=loop.config, client=client)
        if isinstance(tool, VikingMultiReadTool):
            tool = CompileImageReadTool(tool, {})
        registry.register(CompileReadTool(tool, roots, [*sources, request.to] if recovery else []))
    if changes:
        assignment.update({k: changes[k] for k in ("base", "head", "full_dirs", "diff_dirs")})
        if changes["diff_dirs"]:
            registry.register(CompileDiffTool(client, changes))
    writer = CompileWriteTool(client, request.to, save_progress)
    registry.register(writer)
    confirmed_reads = None
    reminded_recovery = False

    async def confirm_recovery(context):
        nonlocal confirmed_reads, reminded_recovery
        if (
            writer.unconfirmed
            or any(outcome != "unchanged" for outcome in writer.outcomes.values())
            or context.text.startswith("INCOMPLETE:")
        ):
            return None
        reads = _confirmed_read_hashes(request.to, context.text, context.messages)
        if reads and source_set_digest(reads[0]) == recovery:
            confirmed_reads = reads
            return None
        if reminded_recovery or context.iteration >= loop.max_iterations:
            return None
        reminded_recovery = True
        await loop.sessions.update_session(
            conversation_key,
            lambda session: session.add_message(
                "assistant", context.text, tool_calls=[], task_id=session_key.chat_id
            ),
            skip_heartbeat=True,
        )
        messages = loop.context.add_assistant_message(context.messages, context.text, [])
        messages.append(
            {
                "role": "user",
                "content": (
                    "The no-change recovery has no complete confirmation yet. "
                    "Finish any missing reads and use the exact UNCHANGED_SOURCES: JSON format."
                    + _RECOVERY_PROMPT
                ),
            }
        )
        return _PlainTextDelivered(messages=messages, tools_used=[])

    async def remind_write(iteration: int) -> str | None:
        """Return a progress note at a scheduled one-based iteration."""
        max_iterations = loop.max_iterations
        one_third = max_iterations // 3
        two_thirds = 2 * max_iterations // 3
        wrap_up = max_iterations - 30
        if wrap_up <= two_thirds:
            wrap_up = (two_thirds + max_iterations) // 2

        if iteration == one_third:
            return (
                "You are about one-third through the iteration budget. Complete any part of the "
                "task that is ready. Reuse results from earlier steps and focus further work on "
                "what is still needed."
            )
        if iteration == two_thirds:
            return (
                "You are about two-thirds through the iteration budget. Deliver the work that is "
                "ready and verify the result. Prioritize the remaining steps that matter most to "
                "the requested outcome."
            )
        if iteration == wrap_up and wrap_up > two_thirds:
            return (
                f"{max_iterations - iteration} rounds remain after this one. Finish the "
                "highest-priority work in progress, verify what you delivered, and report any "
                "outstanding work accurately."
            )
        return None

    runtime = None
    exec_tool = loop.tools.get("exec") if exec_enabled else None
    try:
        if exec_tool is not None:
            runtime = SkillRuntimeContext(
                config=loop.config,
                sandbox_manager=loop.sandbox_manager,
                session_key=session_key,
                workspace_id=loop.sandbox_manager.to_workspace_id(session_key),
                openviking_connection=connection,
                actor_peer_id=(connection or {}).get("actor_peer_id"),
                reject_unlisted_exec_paths=False,
            )
            active = await runtime.activate_from_read(f"{request.skill}/SKILL.md", skill)
            if active is None:
                raise CompileFailure(
                    "SKILL_INVALID", "Expected a remote Skill definition", stage="agent"
                )
            assignment["skill"] = runtime.render_skill_content(active)
            registry.register(exec_tool)
        assignment_text = json.dumps(assignment, ensure_ascii=False)
        session = loop.sessions.get_or_create(conversation_key, skip_heartbeat=True)
        # Pair each final reply with its request, even when concurrent tasks share a session.
        prior = Session(key=conversation_key)
        requests = {}
        for message in session.messages:
            task_id = message.get("task_id")
            if task_id is None:
                prior.messages.append(message)
            elif message["role"] == "user":
                requests[task_id] = message
            elif "tool_calls" not in message and task_id in requests:
                prior.messages.extend((requests.pop(task_id), message))
        history = prior.get_history(loop.memory_window, loop.config.get_provider_name(loop.model))
        final, reasoning, calls, tokens, _ = await loop._run_agent_loop(
            messages=[
                {"role": "system", "content": _PROMPT + (_RECOVERY_PROMPT if recovery else "")},
                *history,
                {"role": "user", "content": assignment_text},
            ],
            session_key=session_key,
            tool_registry=registry,
            skill_runtime=runtime,
            openviking_connection=connection,
            publish_events=False,
            inject_write_experience=False,
            require_natural_completion=True,
            status_note_provider=remind_write,
            on_plain_text=confirm_recovery if recovery else None,
        )
    except Exception as exc:
        if not any(outcome in {"created", "updated"} for outcome in writer.outcomes.values()):
            raise
        final, calls, tokens = f"INCOMPLETE: {str(exc) or type(exc).__name__}", [], {}
    else:
        if final is not None:
            await loop.sessions.update_session(
                conversation_key,
                lambda session: session.add_message(
                    "assistant",
                    final,
                    task_id=session_key.chat_id,
                    reasoning_content=reasoning,
                    token_usage=tokens,
                ),
                skip_heartbeat=True,
            )
    finally:
        if runtime is not None:
            await runtime.close()
    usage.update(tokens)
    result = CompileResult(from_=request.from_, to=request.to, skill=request.skill)
    for uri, outcome in writer.outcomes.items():
        getattr(result, outcome).append(uri)
    count = len(result.created) + len(result.updated)
    result.page_count = len(writer.outcomes)
    inspected_source = False
    write_error = ""
    for call in calls:
        name = call["tool_name"]
        output = call["result"]
        if not call["execute_success"]:
            warning = str(output)[:300]
            result.warnings.append(warning)
            if name == writer.name:
                write_error = warning
            continue
        if name == CompileDiffTool.name:
            inspected_source = True
        elif name in {"openviking_multi_read", "openviking_list"}:
            args = call["resolved_args"]
            for uri in args.get("uris") or [args["uri"]]:
                uri = validate_safe_viking_uri_path(uri)
                if any(uri == root or uri.startswith(root + "/") for root in sources):
                    if isinstance(output, VikingMultiReadResult) and uri in output.errors:
                        result.warnings.append(f"{uri}: {output.errors[uri]}"[:300])
                    else:
                        inspected_source = True
    if writer.unconfirmed:
        result.warnings.append("Unconfirmed writes: " + ", ".join(sorted(writer.unconfirmed)))
    incomplete = final and final.lstrip().startswith("INCOMPLETE:")
    if incomplete:
        result.warnings.append(final[:600])
    if final is not None and not count and (writer.unconfirmed or incomplete):
        raise CompileFailure("COMPILE_INCOMPLETE", result.warnings[-1], stage="agent")
    if final is None:
        result.warnings.append("Partial compilation: iteration limit reached.")
    elif not writer.outcomes and (
        write_error
        or (
            not (incremental and inspected_source)
            and (runtime is None or not runtime.allows_tool("exec"))
        )
    ):
        raise CompileFailure(
            "COMPILE_INCOMPLETE",
            write_error or "No output or verified incremental no-change result.",
            stage="agent",
        )
    if writer.unconfirmed or incomplete or final is None:
        result.completion_status = "partial"
    elif confirmed_reads and not count:
        result.recovery_proof = await verify_recovery_sources(client, changes, *confirmed_reads)
    return result
