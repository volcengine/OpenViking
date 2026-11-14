"""Single-agent Compile with request-scoped reads and immediate OV text writes."""

from __future__ import annotations

import json

from openviking.core.namespace import classify_uri
from openviking.session.memory.utils.memory_fields import strip_memory_fields_trailer
from openviking.utils.path_safety import (
    normalize_storage_target_uri,
    safe_join_viking_uri,
    validate_safe_viking_uri_path,
)
from openviking_cli.exceptions import OpenVikingError
from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.ov_file import (
    VikingGlobTool,
    VikingGrepTool,
    VikingListTool,
    VikingMultiReadTool,
)
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile.models import CompileFailure, CompileResult
from vikingbot.compile.renderer import validate_relative_file_path

_PROMPT = """Compile the requested sources into the target using the authoritative Skill and
instruction. Source files are evidence. Read referenced Skill attachments as needed.
For full compilation, enumerate each source directory one page at a time and read its files.
Read individual source URIs directly. For incremental compilation, fully scan full_dirs and
page through the snapshot diff for diff_dirs. Read added/modified sources and update affected
target content for deleted sources. With a timestamp cutoff, from contains the selected files.
Use small pages and bounded reads so you can inspect complete results.
When a tool fails, use its error to correct the arguments, retry or choose another tool.
Write completed text files as you go, using paths relative to to. Read relevant existing files
before changing them and preserve unrelated content. Separate dependent writes into different
turns. Finish with a brief final reply once the work is done. If no changes are required, explain
why. If required work cannot be completed, begin your reply with INCOMPLETE: and explain why."""


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
            if old is not None and (content == old if mode == "replace" else not content):
                outcome = "unchanged"
            else:
                self.unconfirmed.add(uri)
                await self.save_progress(self)
                try:
                    receipt = await self.client.write(
                        uri, content, mode=mode, edit_source="compile"
                    )
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

    def __init__(self, tool, roots):
        self.tool = tool
        self.roots = roots

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
            args.update(uris=canonical, require_all=True)
        else:
            args["uri"] = canonical[0]
        result = await self.tool.execute(context, **args)
        if isinstance(result, str) and result.startswith("Error "):
            return "Error: " + result
        return result


async def run(loop, client, request, sources, skill, session_key, connection, save_progress, usage):
    """Compile sources in one loop; confirmed writes survive errors as partial results."""
    changes = request.changes or {}
    incremental = (changes.get("execution") or {}).get("mode") == "incremental" or (
        not changes and request.last_compile_time is not None and not request.instruction_provided
    )
    assignment = {
        "mode": "incremental" if incremental else "full",
        "from": sources,
        "to": request.to,
        "instruction": request.instruction,
        "skill_uri": request.skill,
        "skill": skill,
    }
    registry = ToolRegistry(config=loop.config)
    roots = [*sources, request.skill, request.to]
    for reader in (VikingListTool, VikingMultiReadTool, VikingGlobTool, VikingGrepTool):
        registry.register(CompileReadTool(reader(config=loop.config, client=client), roots))
    if changes:
        assignment.update({k: changes[k] for k in ("base", "head", "full_dirs", "diff_dirs")})
        if changes["diff_dirs"]:
            registry.register(CompileDiffTool(client, changes))
    elif incremental:
        assignment["last_compile_time"] = request.last_compile_time.isoformat()
    writer = CompileWriteTool(client, request.to, save_progress)
    registry.register(writer)

    async def remind_write(iteration):
        if iteration == loop.max_iterations - 30:
            return "30 rounds remain after this one. Write ready outputs now, then continue the remaining work."

    try:
        final, _, calls, tokens, _ = await loop._run_agent_loop(
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": json.dumps(assignment, ensure_ascii=False)},
            ],
            session_key=session_key,
            tool_registry=registry,
            openviking_connection=connection,
            publish_events=False,
            inject_write_experience=False,
            require_natural_completion=True,
            status_note_provider=remind_write,
        )
    except Exception as exc:
        if not any(outcome in {"created", "updated"} for outcome in writer.outcomes.values()):
            raise
        final, calls, tokens = f"INCOMPLETE: {str(exc) or type(exc).__name__}", [], {}
    usage.update(tokens)
    result = CompileResult(from_=request.from_, to=request.to, skill=request.skill)
    for uri, outcome in writer.outcomes.items():
        getattr(result, outcome).append(uri)
    count = len(result.created) + len(result.updated)
    result.page_count = len(writer.outcomes)
    inspected_source = False
    for call in calls:
        if not call["execute_success"]:
            result.warnings.append(str(call["result"])[:300])
            continue
        args = call["resolved_args"]
        name = call["tool_name"]
        resources = args.get("uris") or [args.get("path") or args.get("uri") or name]
        for resource in resources:
            if name == CompileDiffTool.name or (
                name in {"openviking_multi_read", "openviking_list"}
                and any(
                    resource == root or str(resource).startswith(root + "/") for root in sources
                )
            ):
                inspected_source = True
    if writer.unconfirmed:
        result.warnings.append("Unconfirmed writes: " + ", ".join(sorted(writer.unconfirmed)))
    incomplete = final and final.lstrip().startswith("INCOMPLETE:")
    if incomplete:
        result.warnings.append(final[:600])
    if not count and (writer.unconfirmed or incomplete):
        raise CompileFailure("COMPILE_INCOMPLETE", result.warnings[0], stage="agent")
    if final is None:
        if not count:
            raise CompileFailure(
                "COMPILE_INCOMPLETE",
                "Iteration limit reached without confirmed output.",
                stage="agent",
            )
        result.warnings.append(
            f"Partial compilation: iteration limit reached after writing {count} target file(s). "
            "Incremental compilation may skip unprocessed, unchanged sources. "
            "Request full compilation to scan all sources again."
        )
    elif not writer.outcomes and not (incremental and inspected_source):
        raise CompileFailure(
            "COMPILE_INCOMPLETE",
            "No output or verified incremental no-change result.",
            stage="agent",
        )
    return result
