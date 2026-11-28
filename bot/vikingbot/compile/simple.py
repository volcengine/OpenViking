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
from vikingbot.agent.loop import _PlainTextDelivered
from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.compile import CompileImageReadTool
from vikingbot.agent.tools.ov_file import (
    VikingGlobTool,
    VikingGrepTool,
    VikingListTool,
    VikingMultiReadTool,
)
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile.file_ops import validate_wiki_conflict_update
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import CompileFailure, CompileResult
from vikingbot.compile.renderer import (
    validate_declared_okf_markdown,
    validate_relative_file_path,
)
from vikingbot.compile.sources import verify_recovery_sources

_PROMPT = """Compile the requested sources into the target using the authoritative Skill and
instruction. Source files are evidence. Read referenced Skill attachments as needed.
For full compilation, enumerate each source directory one page at a time and read its files.
Read individual source URIs directly. For incremental compilation, fully scan full_dirs and
page through the snapshot diff for diff_dirs. Read added/modified sources and update affected
target content for deleted sources.
Use small pages and bounded reads so you can inspect complete results.
For an image source, call openviking_multi_read with its URI and inspect the returned image
before writing findings or marking its contents unreadable. Listing shows only metadata.
When a tool fails, use its error to correct the arguments, retry or choose another tool.
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
        context_type = classify_uri(uri).context_type
        wiki_markdown = context_type == "resource" and relative.lower().endswith(".md")
        if wiki_markdown and mode not in {"replace", "append", "create"}:
            raise ValueError(f"Unsupported Compile write mode: {mode}")
        if uri in self._active or uri in self.unconfirmed:
            raise ValueError(f"Write already in flight or awaiting confirmation: {uri}")
        self._active.add(uri)
        try:
            try:
                old = (
                    (await self.client.download_bytes(uri)).decode("utf-8")
                    if wiki_markdown
                    else await self.client.read_raw(uri)
                )
            except OpenVikingError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                old = None
            if old is not None and context_type == "memory":
                old = strip_memory_fields_trailer(old)
            candidate = (old or "") + content if mode == "append" else content
            if context_type == "resource":
                validate_declared_okf_markdown(uri, candidate.encode("utf-8"))
            if old is not None and (content == old if mode == "replace" else not content):
                outcome = "unchanged"
            else:
                if wiki_markdown:
                    try:
                        validate_wiki_conflict_update(old, candidate)
                    except ValueError as exc:
                        raise ValueError(f"{relative}: {exc}") from exc
                outcome = "created" if old is None else "updated"
                self.unconfirmed.add(uri)
                await self.save_progress(self)
                try:
                    if wiki_markdown:
                        operation: dict[str, str | bool] = {
                            "uri": uri,
                            "content": candidate,
                            "mode": "replace",
                        }
                        if old is None:
                            operation["if_absent"] = True
                        else:
                            operation["expected_sha256"] = content_hash(old)
                        receipt = await self.client.batch_write(
                            root_uri=self.target, operations=[operation], wait=False
                        )
                        confirmed = uri in receipt.get(outcome, [])
                    else:
                        receipt = await self.client.write(uri, content, mode=mode)
                        confirmed = (
                            receipt.get("uri") == uri and receipt.get("content_updated") is True
                        )
                except OpenVikingError as exc:
                    if exc.code in {"INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED"} or (
                        wiki_markdown and exc.code == "CONFLICT"
                    ):
                        self.unconfirmed.discard(uri)
                        await self.save_progress(self)
                    raise
                if not confirmed:
                    raise ValueError(f"Unconfirmed write receipt for {uri}")
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
            args.update(uris=canonical, require_all=True)
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


async def run(loop, client, request, sources, skill, session_key, connection, save_progress, usage):
    """Compile sources in one loop; confirmed writes survive errors as partial results."""
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

    async def remind_write(iteration):
        if iteration == loop.max_iterations - 30:
            return "30 rounds remain after this one. Write ready outputs now, then continue the remaining work."

    try:
        final, _, calls, tokens, _ = await loop._run_agent_loop(
            messages=[
                {"role": "system", "content": _PROMPT + (_RECOVERY_PROMPT if recovery else "")},
                {"role": "user", "content": json.dumps(assignment, ensure_ascii=False)},
            ],
            session_key=session_key,
            tool_registry=registry,
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
    usage.update(tokens)
    result = CompileResult(from_=request.from_, to=request.to, skill=request.skill)
    for uri, outcome in writer.outcomes.items():
        getattr(result, outcome).append(uri)
    count = len(result.created) + len(result.updated)
    result.page_count = len(writer.outcomes)
    inspected_source = False
    write_error = ""
    unresolved: dict[tuple[str, str], str] = {}
    for call in calls:
        name = call["tool_name"]
        args = call["resolved_args"]
        resources = args.get("uris") or [args.get("path") or args.get("uri") or name]
        if name == CompileDiffTool.name:
            # Only retrying the same diff page can clear its failure.
            resources = [(args.get("offset", 0), args.get("limit", 20))]
        tracked = [
            (name, str(resource))
            for resource in resources
            if name in {writer.name, CompileDiffTool.name}
            or any(resource == root or str(resource).startswith(root + "/") for root in sources)
        ]
        for key in tracked:
            unresolved.pop(key, None)
        if not call["execute_success"]:
            warning = str(call["result"])[:300]
            result.warnings.append(warning)
            unresolved.update(dict.fromkeys(tracked, warning))
            if name == writer.name:
                write_error = warning
            continue
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
    if final is not None and not count and (writer.unconfirmed or incomplete or unresolved):
        message = next(reversed(unresolved.values()), result.warnings[0])
        raise CompileFailure("COMPILE_INCOMPLETE", message, stage="agent")
    if final is None:
        if not count:
            message = "Iteration limit reached without confirmed output."
            if write_error:
                message += f" Last write error: {write_error}"
            raise CompileFailure(
                "COMPILE_INCOMPLETE",
                message,
                stage="agent",
            )
        result.warnings.append(
            f"Partial compilation: iteration limit reached after writing {count} target file(s)."
        )
    elif not writer.outcomes and not (incremental and inspected_source):
        raise CompileFailure(
            "COMPILE_INCOMPLETE",
            write_error or "No output or verified incremental no-change result.",
            stage="agent",
        )
    if writer.unconfirmed or incomplete or final is None or unresolved:
        result.completion_status = "partial"
    elif confirmed_reads and not count:
        result.recovery_proof = await verify_recovery_sources(client, changes, *confirmed_reads)
    return result
