"""Compile file editing, revision reads, validation and accepted artifact storage.

These helpers serve record validation, Reduce drafts and Finalize publication preparation.
They share the task runtime without owning pipeline scheduling or publication.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from openviking.core.skill_loader import validate_skill_format
from openviking.utils.path_safety import safe_join_viking_uri
from openviking_cli.exceptions import OpenVikingError
from vikingbot.compile.hashing import content_hash, digest
from vikingbot.compile.renderer import (
    _split_frontmatter,
    validate_relative_file_path,
)
from vikingbot.compile.results import FileDraft, FileResponse, Group, InputReferenceError

if TYPE_CHECKING:
    from vikingbot.compile.pipeline import Pipeline


def apply_file(draft: FileDraft, old: str | None) -> str:
    """Assemble a revision-bound file, rejecting ambiguous, overlapping or stale patches.

    Bytes outside patch anchors are preserved exactly, including whitespace and frontmatter.
    """
    if old is None:
        if draft.base_hash is not None or draft.patches:
            raise ValueError("New files must not include base_hash or patches")
        if draft.content is None:
            raise ValueError(
                f'File "{draft.path}" is missing content. '
                "Provide inline content, or set content_ref to the file you wrote with write_file, "
                "relative to your scratch root."
            )
        return draft.content
    if draft.base_hash != content_hash(old):
        raise ValueError("Old target revision does not match the requested change")
    if draft.content is not None:
        if draft.patches:
            raise ValueError("Full replacement cannot be mixed with patches")
        return draft.content
    if not draft.patches:
        raise ValueError("Existing files require content or patches")
    edits = []
    for patch in draft.patches:
        if old.count(patch.old) != 1:
            raise ValueError("Patch anchor must occur exactly once in its permitted old scope")
        start = old.index(patch.old)
        edits.append((start, start + len(patch.old), patch.new))
    edits.sort()
    if any(a[1] > b[0] for a, b in zip(edits, edits[1:], strict=False)):
        raise ValueError("Patch anchors overlap")
    value = old
    for start, end, replacement in reversed(edits):
        value = value[:start] + replacement + value[end:]
    return value


async def load_old(runtime: Pipeline, path: str) -> str | None:
    """Read a selected target body once in this execution; only NOT_FOUND means absence."""
    path = validate_relative_file_path(path)
    if path not in runtime.old:
        uri = safe_join_viking_uri(runtime.target, path)
        try:
            entry = await runtime.client.stat(uri)
            if entry.get("isDir"):
                raise ValueError("Selected old target must be a file")
            payload = await runtime.client.download_bytes(uri)
            runtime.old[path] = payload.decode("utf-8")
            runtime.metrics["history_body_reads"] += 1
        except OpenVikingError as exc:
            if exc.code != "NOT_FOUND":
                raise
            runtime.old[path] = None
    return runtime.old[path]


def validate_files(runtime: Pipeline, response, group, records, old):
    """Validate supplied lineage, output paths and revisions; unused inputs are allowed."""
    supplied = {record.record_id for record in records}
    paths = [f.path for f in response.files]
    if len(set(paths)) != len(paths):
        raise ValueError("A group cannot submit a path more than once")
    for draft in response.files:
        validate_relative_file_path(draft.path)
        inputs = draft.inputs
        if not inputs or len(inputs) != len(set(inputs)) or not set(inputs) <= supplied:
            raise InputReferenceError(
                "File inputs must be nonempty, unique supporting supplied record IDs: "
                f"path={draft.path}; inputs={inputs}; unknown={sorted(set(inputs) - supplied)}"
            )
        current = old.get(draft.path)
        if draft.content_ref is not None:
            raise ValueError(
                "File references require execution=agent and validated scratch resolution"
            )
        value = apply_file(draft, current)
        if draft.content_sha256 and content_hash(value) != draft.content_sha256:
            raise ValueError("Artifact content hash mismatch")
        if draft.path.endswith(".json"):
            json.loads(value)


async def save_files(runtime: Pipeline, response, group, records, old, *, origin=None):
    """Persist candidates with lineage; only resolved paths enter publication via accept_files."""
    output = []
    for draft in response.files:
        previous = old.get(draft.path)
        value = apply_file(draft, previous)
        inputs = draft.inputs
        artifact = {
            "path": draft.path,
            "content": value,
            "sha256": content_hash(value),
            "base_hash": content_hash(previous) if previous is not None else None,
            "owner": group.group_id,
            "origin": origin or draft.path,
            "source_refs": sorted(
                {ref for r in records if r.record_id in inputs for ref in r.source_refs}
            ),
            "inputs": inputs,
        }
        reference = f"artifacts/{digest([group.group_id, draft.path])}"
        await runtime.files.put(reference, artifact)
        output.append(reference)
    supported = {record_id for draft in response.files for record_id in draft.inputs}
    for record in records:
        if runtime.status[record.record_id] not in {"failed", "prepared"}:
            runtime.status[record.record_id] = (
                "prepared" if record.record_id in supported else "unreferenced"
            )
    return output


async def save_replacements(runtime: Pipeline, response, group, records, *, origin=None):
    """Bind complete generated files to current target revisions without mutating model results."""
    response = response.model_copy(deep=True)
    old = {}
    for draft in response.files:
        previous = await load_old(runtime, draft.path)
        if previous is not None:
            old[draft.path] = previous
            draft.base_hash = content_hash(previous)
    validate_files(runtime, response, group, records, old)
    return await save_files(runtime, response, group, records, old, origin=origin)


async def accept_files(runtime: Pipeline, references):
    """Accept artifacts and optional display metadata; Markdown schemas belong to the Skill."""
    for reference in references:
        artifact = await runtime.files.get(reference)
        metadata = {}
        if artifact["path"].lower().endswith(".md"):
            try:
                metadata, _ = _split_frontmatter(artifact["content"])
            except (ValueError, yaml.YAMLError):
                pass
        owner = runtime.owners.get(artifact["path"])
        previous = f"artifacts/{digest([owner, artifact['path']])}"
        runtime.artifacts = [ref for ref in runtime.artifacts if ref != previous]
        runtime.artifacts.append(reference)
        runtime.owners[artifact["path"]] = artifact["owner"]
        title, description = metadata.get("title"), metadata.get("description")
        runtime.catalog[artifact["path"]] = {
            "path": artifact["path"],
            "title": title if isinstance(title, str) and title.strip() else artifact["path"],
            "description": description if isinstance(description, str) else "",
            "source_refs": artifact["source_refs"],
        }


def validate_skill_output(files: dict[str, str]) -> None:
    """Check one Skill directory and its SKILL.md using the shared format validator.

    Paths are already checked by file validation. Raise ValueError for structural
    errors; content quality and task-specific attachments remain the Skill's concern.
    """
    directories = {path.split("/")[0] for path in files}
    if len(directories) != 1 or any("/" not in path for path in files):
        raise ValueError("Output must contain one <skill-name>/ directory with SKILL.md")
    name = directories.pop()
    main = f"{name}/SKILL.md"
    if main not in files:
        raise ValueError(f"Missing {main}")
    result = validate_skill_format(files[main], strict=True, skill_dir_name=name, source_path=main)
    if not result["valid"]:
        raise ValueError("; ".join(issue["message"] for issue in result["errors"]))


async def repair_skill_output(runtime: Pipeline, artifacts: list[dict], error: str) -> list[str]:
    """Repair rejected files and return saved references, without publishing them.

    The model receives the current files, diagnostic and source handles. Its existing
    validation loop permits three submissions total. Accepted replacements retain
    source lineage and target revision guards; failures propagate to the caller.
    """
    ids = {i for a in artifacts for i in a["inputs"]} or set(runtime.evidence)
    records = [runtime.records[i] for i in sorted(ids)]
    group = Group("skill-repair", records)
    old = {}
    for artifact in artifacts:
        if artifact["base_hash"] is not None:
            previous = runtime.old.get(artifact["path"])
            if previous is None or content_hash(previous) != artifact["base_hash"]:
                raise ValueError(f"Stale prepared artifact: {artifact['path']}")
            old[artifact["path"]] = previous

    def validate(response):
        """Accept complete files only when provenance, revisions and package format agree."""
        validate_files(runtime, response, group, records, old)
        validate_skill_output(
            {draft.path: apply_file(draft, old.get(draft.path)) for draft in response.files}
        )

    response = await runtime.model.ask(
        "skill_repair",
        runtime.system
        + "\nFix the reported errors according to the original Skill and user instruction. "
        "Preserve valid content. Submit all files, including unchanged ones, with supporting "
        "input IDs. Read assigned evidence if needed. Keep existing base_hash values.",
        {
            "error": error,
            "files": [
                {k: a[k] for k in ("path", "content", "inputs", "base_hash")} for a in artifacts
            ],
            "inputs": [
                {"id": r.record_id, "payload": {"source_ranges": r.source_refs}} for r in records
            ],
        },
        FileResponse,
        validate,
    )
    repaired = await save_files(runtime, response, group, records, old)
    runtime.artifacts.clear()
    runtime.owners.clear()
    runtime.catalog.clear()
    await accept_files(runtime, repaired)
    return repaired
