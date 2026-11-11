"""Pre-seed source filtering: a Skill-provided whitelist that shrinks inputs before any read.

Mirroring the pre-Map outline flow, the model decides from the Skill text alone whether the
task mandates a deterministic, pre-seed source filter and copies the named Skill-bundled
script verbatim. The framework executes that script once on a body-less manifest (stable ids,
absolute URIs and repository-relative paths only) and the script returns a whitelist of ids
to keep. Filtering happens before source bodies are read, split or seeded, so dropped files
cost no model calls, no batches and no media read failures; every downstream stage
(manifest, coverage, outline) operates on the trimmed collection, which keeps the
"all sources must be covered" contract consistent.

Every failure mode fails open: a disabled gate, decision error, unusable script, execution
error, malformed result or empty/unknown whitelist keeps the original file list unchanged and
records a warning. A filter must never turn an ordinary compile into a failure.
"""

from __future__ import annotations

import json
import posixpath
import shlex
import uuid
from typing import TYPE_CHECKING

from openviking.core.namespace import relative_uri_path
from vikingbot.compile.models import COMPILE_STAGING_ROOT
from vikingbot.compile.ops import outline as outline_op
from vikingbot.compile.plan import FilterDecision

if TYPE_CHECKING:
    from vikingbot.compile.pipeline import Pipeline

# Deterministic structural filtering is a pure stdlib script; keep the same budget as outline.
_FILTER_EXEC_TIMEOUT_SECONDS = 300

_FILTER_DECIDER = """## Task
Read the complete Skill text and decide whether this task mandates a *pre-seed source filter*.

A pre-seed source filter means that, before any source content is read, split or processed,
the task must run a concrete, Skill-bundled script that receives the framework-provided file
manifest (stable ids, absolute URIs and repository-relative paths, no file contents) and
returns the whitelist of file ids to keep, deterministically dropping noise files such as
translation catalogs or static/vendor assets.

Answer use_filter=true ONLY when the Skill explicitly names the specific script that performs
this pre-seed filtering and states that the framework runs it before reading sources. Copy
that script's Skill-relative path verbatim into provider.

Answer use_filter=false when:
- filtering is only suggested as guidance for the model rather than a named framework script,
- the script runs inside an agent job after sources are already read (that is not pre-seed),
- files are excluded only implicitly by an outline or by source-selection scoring,
- or no concrete script path is named in the Skill text. Never invent a path.

Decide only from the workflow shape described in the Skill. Do not decide from keywords such
as "wiki" in the name. Quote the decisive sentences in reason.
"""


async def decide(runtime: "Pipeline", source_summary: dict) -> FilterDecision | None:
    """Ask the model whether the Skill mandates a pre-seed filter. Any failure declines."""
    try:
        decision = await runtime.model.ask(
            "source_filter_decide",
            _FILTER_DECIDER,
            {"skill": runtime.skill, "source_summary": source_summary},
            FilterDecision,
        )
    except Exception as exc:  # noqa: BLE001 - decision failure must fall back, never crash planning
        runtime.warnings.append(
            f"Source filter decision failed; keeping all files: {str(exc)[:300]}"
        )
        return None
    if decision is None:
        runtime.warnings.append(
            "Source filter decision returned no result; keeping all files."
        )
        return None
    await runtime.files.put("filter-decision", decision.model_dump())
    return decision


def _relative_path(uri: str, roots: list[str]) -> str:
    for root in roots:
        rel = relative_uri_path(root, uri)
        if rel:
            return rel
    # No containing from-root: fall back to the path tail rather than failing planning.
    return uri.rstrip("/").rsplit("/", 1)[-1]


def _build_manifest(runtime: "Pipeline", ordered: list[str], roots: list[str]) -> dict:
    """Expose identity and repository-relative paths only; never source bodies."""
    return {
        "target": runtime.target,
        "files": [
            {"id": str(index), "uri": uri, "path": _relative_path(uri, roots)}
            for index, uri in enumerate(ordered)
        ],
    }


def _validate_keep(keep: object, valid_ids: set[str]) -> list[str] | None:
    """Return the whitelist only when it is a non-empty subset of manifest ids."""
    if not isinstance(keep, list) or not keep or not all(isinstance(item, str) for item in keep):
        return None
    keep_set = set(keep)
    if not keep_set or not keep_set <= valid_ids:
        return None
    # Preserve the manifest's original ordering; ignore duplicate ids defensively.
    return [item for item in keep if item in keep_set]


async def apply(runtime: "Pipeline", ordered: list[str], roots: list[str]) -> list[str]:
    """Shrink the pre-seed file list via the Skill whitelist; all problems fail open.

    Returns a new list without mutating ``ordered``. On every unusable signal the original
    list is returned untouched.
    """
    if not ordered:
        return ordered

    source_summary = {
        "file_count": len(ordered),
        "sample_paths": [_relative_path(uri, roots) for uri in ordered[:20]],
    }
    decision = await decide(runtime, source_summary)
    if decision is None or not decision.use_filter or not decision.provider:
        return ordered
    provider = decision.provider

    if not runtime.compile_exec_enabled:
        runtime.warnings.append(
            "Source filter requested but compile exec is disabled; keeping all files."
        )
        return ordered
    # Reuse the outline provider checks: traversal/namespace rejection plus the 256 KiB gate.
    if not await outline_op.materialize_provider(runtime, provider):
        return ordered

    safe_provider = runtime.resources.path(provider)
    script_local = posixpath.join(runtime.resources.local_root, safe_provider)
    manifest = _build_manifest(runtime, ordered, roots)
    manifest_rel = posixpath.join(
        COMPILE_STAGING_ROOT, f"filter-manifest-{uuid.uuid4().hex}.json"
    )
    await runtime.files.sandbox.write_file(
        manifest_rel, json.dumps(manifest, ensure_ascii=False)
    )
    manifest_abs = posixpath.join(runtime.files.sandbox.sandbox_cwd, manifest_rel)
    result_rel = posixpath.join(
        COMPILE_STAGING_ROOT, f"filter-result-{uuid.uuid4().hex}.json"
    )
    result_abs = posixpath.join(runtime.files.sandbox.sandbox_cwd, result_rel)

    command = (
        f"python3 {shlex.quote(script_local)} {shlex.quote(manifest_abs)} "
        f"{shlex.quote(result_abs)}"
    )
    try:
        await runtime.files.sandbox.execute(
            command, timeout=_FILTER_EXEC_TIMEOUT_SECONDS
        )
        raw = await runtime.files.sandbox.read_file(result_rel)
        payload = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - script/JSON failures keep the full input set
        runtime.warnings.append(
            f"Source filter script {safe_provider} failed; keeping all files: {str(exc)[:300]}"
        )
        return ordered

    valid_ids = {str(index) for index in range(len(ordered))}
    keep = _validate_keep(payload.get("keep") if isinstance(payload, dict) else None, valid_ids)
    if keep is None:
        runtime.warnings.append(
            f"Source filter {safe_provider} returned an empty, malformed or out-of-scope "
            "whitelist; keeping all files."
        )
        return ordered

    keep_set = set(keep)
    filtered = [uri for index, uri in enumerate(ordered) if str(index) in keep_set]
    if not filtered:
        runtime.warnings.append(
            f"Source filter {safe_provider} dropped every file; keeping all files."
        )
        return ordered

    runtime.metrics["source_filter_calls"] += 1
    runtime.metrics["source_files_total"] = len(ordered)
    runtime.metrics["source_files_kept"] = len(filtered)
    runtime.metrics["source_files_dropped"] = len(ordered) - len(filtered)
    await runtime.files.put(
        "filter-result",
        {
            "provider": safe_provider,
            "total": len(ordered),
            "kept": len(filtered),
            "dropped": len(ordered) - len(filtered),
            "keep": keep,
        },
    )
    runtime.warnings.append(
        f"Source filter {safe_provider} kept {len(filtered)}/{len(ordered)} files "
        f"(dropped {len(ordered) - len(filtered)})."
    )
    return filtered
