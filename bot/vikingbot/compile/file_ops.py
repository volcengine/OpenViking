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
from vikingbot.compile.plan import FileDraft, InputReferenceError, content_hash, digest
from vikingbot.compile.renderer import (
    _split_frontmatter,
    validate_relative_file_path,
    validate_resource_file,
)

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
            if entry.get("isDir") or int(entry.get("size") or 0) > 8 * 1024 * 1024:
                raise ValueError("Selected old target must be a file of at most 8 MiB")
            payload = await runtime.client.download_bytes(uri)
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError("Selected old file exceeds 8 MiB")
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
        if runtime.skill_target:
            if not draft.path.startswith(runtime.skill_name + "/"):
                raise ValueError(f"Skill files must be under {runtime.skill_name}/")
            if draft.path == f"{runtime.skill_name}/SKILL.md":
                validation = validate_skill_format(
                    value, strict=True, skill_dir_name=runtime.skill_name, source_path=draft.path
                )
                if not validation["valid"]:
                    raise ValueError("; ".join(issue["message"] for issue in validation["errors"]))
        if draft.content_sha256 and content_hash(value) != draft.content_sha256:
            raise ValueError("Artifact content hash mismatch")
        if len(value.encode()) > 8 * 1024 * 1024:
            raise ValueError("Assembled output exceeds 8 MiB")
        wiki = is_wiki(runtime, draft.path, value.encode())
        if draft.path.endswith(".json"):
            json.loads(value)
        if (
            runtime.contract.output_format == "wiki"
            and draft.path.lower().endswith(".md")
            and not wiki
        ):
            raise ValueError("Declared Wiki output requires valid OKF frontmatter")


def is_wiki(runtime: Pipeline, path, payload):
    """Apply OKF validation only to declared Wiki output or recognized OKF page types.

    Generic Markdown may use its own frontmatter, including a different type.
    Such files and Skill package files do not acquire Wiki navigation or citations.
    """
    if runtime.skill_target:
        return False
    if runtime.contract.output_format == "files" and path.lower().endswith(".md"):
        try:
            metadata, _ = _split_frontmatter(payload.decode())
        except (ValueError, UnicodeError, yaml.YAMLError):
            return False
        if metadata.get("type") not in {
            "entity",
            "concept",
            "method",
            "comparison",
            "analysis",
            "index",
        }:
            return False
    return validate_resource_file(path, payload)


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
    """Select saved references for publication and update their catalog entries together."""
    for reference in references:
        artifact = await runtime.files.get(reference)
        metadata = (
            _split_frontmatter(artifact["content"])[0]
            if is_wiki(runtime, artifact["path"], artifact["content"].encode())
            else {}
        )
        owner = runtime.owners.get(artifact["path"])
        previous = f"artifacts/{digest([owner, artifact['path']])}"
        runtime.artifacts = [ref for ref in runtime.artifacts if ref != previous]
        runtime.artifacts.append(reference)
        runtime.owners[artifact["path"]] = artifact["owner"]
        runtime.catalog[artifact["path"]] = {
            "path": artifact["path"],
            "title": metadata.get("title", artifact["path"]),
            "description": metadata.get("description", ""),
            "source_refs": artifact["source_refs"],
        }
