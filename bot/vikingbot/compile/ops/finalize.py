"""Prepare accepted artifacts, Wiki navigation and revision-bound publication operations."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from openviking.core.namespace import relative_uri_path
from openviking.utils.path_safety import safe_join_viking_uri
from openviking_cli.exceptions import OpenVikingError
from vikingbot.compile import file_ops
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import CompileFailure
from vikingbot.compile.renderer import (
    RenderedBundle,
    _link_uri,
    finalize_resource_output,
    relocate_wiki_links,
)

if TYPE_CHECKING:
    from vikingbot.compile.pipeline import Pipeline


async def run(runtime: Pipeline, references: list[str]) -> RenderedBundle:
    """Validate one writer per path and prepare revision-bound publication operations.

    References name accepted task artifacts validated during generation or checkpoint
    recovery. The returned bundle contains write operations for the service to publish,
    without performing target writes here.
    With wiki_links enabled, directory listings and submitted Markdown paths define
    complete runtime-owned indexes. Only retained index bodies are read or rewritten;
    retained knowledge pages supply paths without body reads. Otherwise contents stay unchanged.
    Partial recovery retains the same write guards. Skill packages are checked together
    and may use a task-wide budget of three repair submissions before publication.
    Navigation does not invoke models or Skill scripts, including during recovery.
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
    files, owners, revisions, source_uris, origins, outputs = {}, {}, {}, {}, {}, {}
    relocations = {}
    for reference in references:
        artifact = await runtime.files.get(reference)
        path = artifact["path"]
        if path in owners:
            raise ValueError(f"Multiple output writers claim {path}")
        owners[path] = artifact["owner"]
        files[path] = file_ops.file_bytes(artifact)
        if content_hash(files[path]) != artifact["sha256"]:
            raise ValueError(f"Stored file hash mismatch: {path}")
        revisions[path] = artifact["base_hash"]
        origin = artifact.get("origin", path)
        origins[path] = origin
        relocations[origin] = path if origin not in relocations else None
        source_uris[path] = sorted(
            {runtime.evidence[ref]["uri"] for ref in artifact["source_refs"]}
        )
        outputs[path] = {"source_refs": artifact["source_refs"]}
    existing = {}
    page_files = {
        path: payload
        for path, payload in files.items()
        if path.lower().endswith(".md")
        and not any(part.startswith(".") for part in path.split("/"))
    }
    if runtime.request.wiki_links and not runtime.skill_target and page_files:
        known_paths = set(files)
        pending = [runtime.target]
        seen = {runtime.target}
        while pending:
            directory = pending.pop()
            offset = 0
            while True:
                try:
                    entries = await runtime.client.list_resources(
                        directory, node_limit=500, offset=offset
                    )
                except OpenVikingError as exc:
                    if exc.code != "NOT_FOUND" or directory != runtime.target:
                        raise
                    break
                for entry in entries:
                    uri = str(entry.get("uri") or "").rstrip("/")
                    path = relative_uri_path(runtime.target, uri)
                    if not path:
                        raise ValueError(
                            f"Navigation inventory returned an out-of-scope URI: {uri}"
                        )
                    if any(part.startswith(".") for part in path.split("/")):
                        continue
                    if entry.get("isDir", entry.get("is_dir", False)):
                        if uri not in seen:
                            seen.add(uri)
                            pending.append(uri)
                    else:
                        known_paths.add(path)
                offset += len(entries)
                if len(entries) < 500:
                    break
        for path, payload in page_files.items():
            page_files[path] = relocate_wiki_links(
                payload.decode(),
                origin=origins[path],
                path=path,
                target_uri=runtime.target,
                relocations=relocations,
                known_paths=known_paths,
            ).encode()
        indexes: set[str] = set()
        for path in known_paths:
            if not path.lower().endswith(".md") or any(
                part.startswith(".") for part in path.split("/")
            ):
                continue
            parts = path.split("/")[:-1]
            indexes.update("/".join([*parts[:i], "index.md"]) for i in range(len(parts) + 1))
        for path in sorted(indexes):
            text = await file_ops.load_old(runtime, path)
            if text is not None:
                existing[path] = text.encode()
            revisions[path] = content_hash(text) if text is not None else None
            owners[path] = "runtime:navigation"
        finalized = finalize_resource_output(
            page_files,
            target_uri=runtime.target,
            source_roots={key: info["uri"] for key, info in runtime.evidence.items()},
            existing_files=existing,
            known_paths=known_paths | set(existing),
            partial_catalog=True,
            source_uris_by_path=source_uris,
        )
        missing = set()
        for entry in finalized.link_report.get("unresolved", []):
            source = safe_join_viking_uri(runtime.target, entry["source_path"])
            uri = _link_uri(re.split(r"[#?]", entry["target"], maxsplit=1)[0], source)
            path = relative_uri_path(runtime.target, uri)
            if not path or path in missing:
                continue
            try:
                await runtime.client.stat(safe_join_viking_uri(runtime.target, path))
            except OpenVikingError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                missing.add(path)
        if missing:
            # A missing exact target and unique matching title allow a local repair;
            # unrecalled historical pages never get redirected by basename alone.
            finalized = finalize_resource_output(
                page_files,
                target_uri=runtime.target,
                source_roots={key: info["uri"] for key, info in runtime.evidence.items()},
                existing_files=existing,
                known_paths=known_paths | set(existing),
                partial_catalog=True,
                source_uris_by_path=source_uris,
                verified_missing_paths=missing,
            )
            broken = [
                entry
                for entry in finalized.link_report.get("unresolved", [])
                if relative_uri_path(
                    runtime.target,
                    _link_uri(
                        re.split(r"[#?]", entry["target"], maxsplit=1)[0],
                        safe_join_viking_uri(runtime.target, entry["source_path"]),
                    ),
                )
                in missing
            ]
            if broken:
                runtime.warnings.append(
                    f"Unresolved links to {len(broken)} verified missing targets."
                )
        files.update(finalized.files)
        if finalized.link_report.get("invalid_metadata"):
            runtime.warnings.append(
                f"Invalid YAML in {len(finalized.link_report['invalid_metadata'])} pages; "
                "navigation uses filenames and preserves page content."
            )
    else:
        finalized = None
    rendered = RenderedBundle()
    for path, payload in files.items():
        owners.setdefault(path, "runtime:navigation")
        uri = safe_join_viking_uri(runtime.target, path)
        old = runtime.old.get(path)
        revision = revisions.get(path)
        if revision is None and runtime.skill_target:
            # Detect collisions even when a plan deliberately skips history matching.
            if await file_ops.load_old(runtime, path) is not None:
                raise ValueError(f"Create conflicts with an existing target: {uri}")
        elif revision is not None and (old is None or content_hash(old) != revision):
            raise ValueError(f"Stale prepared artifact: {uri}")
        # Resource writes recheck even identical cached bytes under server locks;
        # concurrent changes become per-file conflicts in the publication result.
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
        if revision:
            operation["expected_sha256"] = revision
        operation.pop("content_base64" if operation["content"] is not None else "content")
        rendered.operations.append(operation)
        (rendered.updated if revision else rendered.created).append(uri)
        if (
            not runtime.skill_target
            and path.lower().endswith(".md")
            and (runtime.output_format == "wiki" or runtime.request.wiki_links)
        ):
            rendered.wiki_uris.append(uri)
    if finalized:
        rendered.link_count, rendered.link_report = finalized.link_count, finalized.link_report
        unresolved = rendered.link_report.get("unresolved", [])
        rendered.link_report["unresolved_count"] = len(unresolved)
        rendered.link_report["unresolved"] = unresolved[:20]
    for path, output in outputs.items():
        output["sha256"] = content_hash(files[path])
    await runtime.files.put(
        "finalize", {"owners": owners, "prepared_files": len(files), "outputs": outputs}
    )
    return rendered
