"""Prepare accepted artifacts and revision-bound publication operations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openviking.utils.path_safety import safe_join_viking_uri
from vikingbot.compile import file_ops
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import CompileFailure
from vikingbot.compile.renderer import RenderedBundle, validate_relative_file_path

if TYPE_CHECKING:
    from vikingbot.compile.pipeline import Pipeline


async def run(runtime: Pipeline, references: list[str]) -> RenderedBundle:
    """Validate accepted artifacts and prepare revision-bound publication operations.

    References name accepted task artifacts validated during generation or checkpoint
    recovery. The returned bundle contains write operations for the service to publish,
    without performing target writes here. Invalid artifacts raise before publication.
    Partial recovery retains the same write guards. Skill packages are checked together
    and may use a task-wide budget of three repair submissions before publication.
    """
    if runtime.skill_target:
        artifacts = [await runtime.files.get(ref) for ref in references]
        try:
            file_ops.validate_skill_output({a["path"]: a["content"] for a in artifacts})
        except ValueError as error:
            try:
                references = await file_ops.repair_skill_output(runtime, artifacts, str(error))
            except Exception as exc:
                raise CompileFailure("SKILL_REPAIR_FAILED", str(exc), stage="skill_repair") from exc
    files, owners, revisions, outputs = {}, {}, {}, {}
    for reference in references:
        artifact = await runtime.files.get(reference)
        path = artifact["path"]
        validate_relative_file_path(path)
        if path in owners:
            raise ValueError(f"Multiple output writers claim {path}")
        owners[path] = artifact["owner"]
        payload = file_ops.file_bytes(artifact)
        if content_hash(payload) != artifact["sha256"]:
            raise ValueError(f"Stored file hash mismatch: {path}")
        text = file_ops.encode_content(payload)["content"]
        if not payload or (text is not None and not text.strip()):
            raise ValueError(f"Empty output: {path}")
        if path.lower().endswith((".md", ".json")):
            payload.decode("utf-8")
        if path.endswith(".json"):
            json.loads(payload)
        files[path] = payload
        revisions[path] = artifact["base_hash"]
        outputs[path] = {"source_refs": artifact["source_refs"]}
    if runtime.skill_target and files:
        file_ops.validate_skill_output(
            {path: file_ops.encode_content(raw)["content"] for path, raw in files.items()}
        )
    rendered = RenderedBundle()
    for path, payload in files.items():
        uri = safe_join_viking_uri(runtime.target, path)
        old = runtime.old.get(path)
        revision = revisions.get(path)
        if revision is None and runtime.skill_target:
            # Detect collisions even when a plan deliberately skips history matching.
            if await file_ops.load_old(runtime, path) is not None:
                raise ValueError(f"Create conflicts with an existing target: {uri}")
        elif revision is not None and (old is None or content_hash(old) != revision):
            raise ValueError(f"Stale prepared artifact: {uri}")
        if (
            runtime.skill_target
            and old is not None
            and payload == (old.encode() if isinstance(old, str) else old)
        ):
            rendered.unchanged.append(uri)
            continue
        operation = {
            "uri": uri,
            **file_ops.encode_content(payload),
            "mode": "replace" if revision else "create",
        }
        operation.pop("content_base64" if operation["content"] is not None else "content")
        rendered.operations.append(operation)
        (rendered.updated if revision else rendered.created).append(uri)
        if (
            not runtime.skill_target
            and path.lower().endswith(".md")
            and runtime.output_format == "wiki"
        ):
            rendered.wiki_uris.append(uri)
    for path, output in outputs.items():
        output["sha256"] = content_hash(files[path])
    await runtime.files.put(
        "finalize",
        {
            "owners": owners,
            "prepared_files": len(files),
            "outputs": outputs,
            "excluded": {},
        },
    )
    return rendered
