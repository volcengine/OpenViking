"""Pre-Map outline: a Skill-provided deterministic page plan that assigns sources to pages.

The decision to use an outline is made by the model from the Skill text alone. When chosen,
a Skill-bundled script produces the outline (pages with fixed output paths and source spans).
The framework then groups seeded source ranges by that outline instead of running Map/Shuffle
grouping; Reduce and Finalize are reused unchanged.

The outline is a task-level plan artifact. It never carries writing instructions: those stay
in the Skill and the planner-produced Contract, exactly as in the legacy flow.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import uuid
from typing import TYPE_CHECKING

from pydantic import Field

from openviking.core.namespace import relative_uri_path
from vikingbot.compile.models import COMPILE_STAGING_ROOT, CompileFailure
from vikingbot.compile.plan import (
    OutlineDecision,
    PlanModel,
    SemanticOutline,
    SemanticOutlineDecision,
)
from vikingbot.compile.renderer import validate_relative_file_path
from vikingbot.compile.results import Group, Record

if TYPE_CHECKING:
    from vikingbot.compile.pipeline import Pipeline


_OUTLINE_DECIDER = """## Task
Read the complete Skill text and decide whether this task mandates a *pre-Map outline*.

A pre-Map outline means that, before processing any content, the task must:
1. produce a fixed list of pages/chapters, and
2. assign source files/line ranges to those pages, and
3. fix each page's output file path in advance,
via a concrete script that the Skill text explicitly names.

Answer use_outline=true ONLY when all three hold AND the Skill names the specific script
that generates the outline. Copy that script's Skill-relative path verbatim into provider.

Answer use_outline=false when:
- pages/sections emerge during synthesis (for example entity/concept pages decided later),
- the Skill groups content dynamically rather than from a fixed upfront manifest,
- or no concrete script path is named in the Skill text. Never invent a path.

Decide only from the workflow shape described in the Skill. Do not decide from keywords
such as "wiki" in the name. Quote the decisive sentences in reason.
"""

# Deterministic outline script execution budget; pure structural planning is fast.
_OUTLINE_EXEC_TIMEOUT_SECONDS = 300


class SourceSpan(PlanModel):
    """An inclusive line span within one source URI, declared by the outline script."""

    uri: str = Field(min_length=1)
    start_line: int = Field(ge=1, strict=True)
    end_line: int = Field(ge=1, strict=True)


class OutlinePage(PlanModel):
    """One planned page: a stable id, a locked output path, and the source spans it covers."""

    id: str = Field(min_length=1, description="Stable unique page identifier used as the group id.")
    title: str = Field(default="", max_length=300)
    path: str = Field(min_length=1, description="Output path relative to the compile target.")
    sources: list[SourceSpan] = Field(min_length=1)


class Outline(PlanModel):
    """The complete pre-Map page plan emitted by the Skill script as JSON."""

    pages: list[OutlinePage] = Field(min_length=1)


async def decide(runtime: Pipeline, source_summary: dict) -> OutlineDecision | None:
    """Ask the model whether the Skill mandates an outline. Any failure safely declines.

    The decision is persisted for audit. A model/timeout/validation error must never drag
    an ordinary task into the outline flow, so it returns None (legacy path).
    """
    try:
        decision = await runtime.model.ask(
            "outline_decide",
            _OUTLINE_DECIDER,
            {"skill": runtime.skill, "source_summary": source_summary},
            OutlineDecision,
        )
    except Exception as exc:  # noqa: BLE001 - decision failure must fall back, never crash planning
        runtime.warnings.append(f"Outline decision failed; using legacy flow: {str(exc)[:300]}")
        return None
    await runtime.files.put("outline-decision", decision.model_dump())
    return decision


async def materialize_provider(runtime: Pipeline, provider: str) -> bool:
    """Validate and snapshot the named script; return False on any boundary/absence problem."""
    try:
        # path() rejects traversal/namespace escape; read() enforces size and materializes it.
        path = runtime.resources.path(provider)
        await runtime.resources.read(path)
    except Exception as exc:  # noqa: BLE001 - an unusable declaration declines, never crashes
        runtime.warnings.append(
            f"Outline requested but provider is unusable ({provider}): {str(exc)[:300]}"
        )
        return False
    return True


def _relative_path(runtime: Pipeline, uri: str) -> str:
    """Repository-relative path of a seeded URI; fall back to the path tail."""
    for root in list(runtime.request.from_ or []):
        rel = relative_uri_path(root, uri)
        if rel:
            return rel
    # No containing from-root: fall back to the path tail rather than failing planning.
    return uri.rstrip("/").rsplit("/", 1)[-1]


def export_manifest(runtime: Pipeline, sources: list[Record], source_summary: dict) -> dict:
    """Expose source structure (URI, repository-relative path, lines, hash) without bodies.

    A bounded file tree (relative paths plus per-file line ranges) is derived purely from
    seeded evidence, so a Skill script can plan from the tree the way DeepWiki does, without
    any extra listing call or file-content access.
    """
    files: dict[str, dict] = {}
    source_items = []
    for record in sources:
        evidence = runtime.evidence[record.record_id]
        uri = evidence["uri"]
        path = _relative_path(runtime, uri)
        source_items.append(
            {
                "id": record.record_id,
                "uri": uri,
                "path": path,
                "start_line": evidence["start_line"],
                "end_line": evidence["end_line"],
                "sha256": evidence["hash"],
            }
        )
        node = files.setdefault(path, {"path": path, "uri": uri, "ranges": []})
        node["ranges"].append([evidence["start_line"], evidence["end_line"]])

    return {
        "target": runtime.target,
        "instruction": runtime.request.instruction,
        "source_summary": source_summary,
        "paths": sorted(files),
        "files": [files[path] for path in sorted(files)],
        "sources": source_items,
    }


def _validate(runtime: Pipeline, outline: Outline, sources: list[Record]) -> None:
    """Reject duplicate ids/paths, illegal paths and spans that match no seeded source."""
    uris = {runtime.evidence[r.record_id]["uri"] for r in sources}
    if len({page.id for page in outline.pages}) != len(outline.pages):
        raise ValueError("Outline page ids must be unique")
    if len({page.path for page in outline.pages}) != len(outline.pages):
        raise ValueError("Outline page paths must be unique")
    for page in outline.pages:
        validate_relative_file_path(page.path)
        for span in page.sources:
            if span.start_line > span.end_line:
                raise ValueError(f"Outline page {page.id}: span start_line exceeds end_line")
            if span.uri not in uris:
                raise ValueError(f"Outline page {page.id}: unknown source uri {span.uri}")


def _members(runtime: Pipeline, sources: list[Record], page: OutlinePage) -> list[Record]:
    """All seeded ranges whose URI and line interval intersect any declared span.

    Intersection membership is intentionally permissive: one range may serve several pages.
    """
    members = []
    for record in sources:
        evidence = runtime.evidence[record.record_id]
        for span in page.sources:
            if (
                span.uri == evidence["uri"]
                and span.start_line <= evidence["end_line"]
                and span.end_line >= evidence["start_line"]
            ):
                members.append(record)
                break
    if not members:
        raise ValueError(f"Outline page {page.id} matched no seeded source ranges")
    return members


def group_sources(
    runtime: Pipeline, sources: list[Record], outline: Outline
) -> tuple[list[Group], list[Record]]:
    """Assign seeded ranges to outline pages; return page groups and uncovered ranges."""
    _validate(runtime, outline, sources)
    groups: list[Group] = []
    covered: set[str] = set()
    for page in outline.pages:
        members = _members(runtime, sources, page)
        groups.append(Group(page.id, members, assigned_path=page.path))
        covered.update(record.record_id for record in members)
    uncovered = [record for record in sources if record.record_id not in covered]
    return groups, uncovered


async def build_outline(
    runtime: Pipeline, provider: str, sources: list[Record], source_summary: dict
) -> Outline:
    """Deterministically execute the Skill script and parse its page plan.

    The framework runs the script itself (no agent exists at this point). A manifest path is
    passed as argv[1]; the script prints the outline JSON to stdout.
    """
    # resources.read() has already snapshotted the script; reuse its sandbox-local path.
    safe_provider = runtime.resources.path(provider)
    script_local = posixpath.join(runtime.resources.local_root, safe_provider)

    manifest = export_manifest(runtime, sources, source_summary)
    manifest_rel = posixpath.join(
        COMPILE_STAGING_ROOT, f"outline-manifest-{uuid.uuid4().hex}.json"
    )
    await runtime.files.sandbox.write_file(manifest_rel, json.dumps(manifest, ensure_ascii=False))
    manifest_abs = posixpath.join(runtime.files.sandbox.sandbox_cwd, manifest_rel)

    # The script writes the outline to a file rather than stdout: the Direct backend
    # truncates shell stdout at 10k characters and merges stderr into it, so a large
    # outline on stdout would be corrupted. Reading the file has no size cap.
    outline_rel = posixpath.join(COMPILE_STAGING_ROOT, f"outline-{uuid.uuid4().hex}.json")
    outline_abs = posixpath.join(runtime.files.sandbox.sandbox_cwd, outline_rel)

    command = (
        f"python3 {shlex.quote(script_local)} {shlex.quote(manifest_abs)} "
        f"{shlex.quote(outline_abs)}"
    )
    await runtime.files.sandbox.execute(command, timeout=_OUTLINE_EXEC_TIMEOUT_SECONDS)
    try:
        raw = await runtime.files.sandbox.read_file(outline_rel)
        outline = Outline.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - convert script/JSON failures to a task failure
        raise CompileFailure(
            "COMPILE_INCOMPLETE",
            f"Outline script {safe_provider} did not return valid outline JSON: {str(exc)[:300]}",
            stage="outline",
        ) from exc
    _validate(runtime, outline, sources)
    await runtime.files.put("outline", outline.model_dump())
    return outline


# --- DeepWiki-style semantic outline (LLM tree + README planning, default-off) --------

_README_RE = re.compile(r"^readme(\.[a-z0-9]+)?$", re.IGNORECASE)
# Guard rails for the planning input. Django-scale trees stay below model context without
# folding; folding is a deterministic last resort and is always recorded as a metric.
_PLANNING_TREE_MAX_FILES = 1200
_PLANNING_BRIEF_MAX_CHARS = 180_000

_SEMANTIC_OUTLINE_DECIDER = """## Task
Read the complete Skill text and decide whether this task mandates a *semantic outline*:
an LLM-produced page plan built from the repository file tree (paths only) plus the
shortest-path README text, before any source content is assigned to pages.

Answer use_semantic_outline=true ONLY when the Skill explicitly instructs the runtime to
plan the pages itself from the file tree and README and to assign whole files (never line
numbers) to a fixed small set of pages, stated in the Skill's own words.

Answer false for deterministic outline scripts, dynamic Map/Shuffle grouping, or any Skill
that never names semantic outline planning. Never decide from keywords such as "wiki".
Quote the decisive sentences in reason.
"""

_SEMANTIC_OUTLINE_PLANNER = """## Task
You plan a small documentation wiki for one repository. Produce a fixed outline of
8 to 12 pages, including exactly one index page whose path is "index.md".

## Inputs
- paths: every repository-relative file path available to this task. Paths only; no content.
- readme_path/readme: the shortest-path README, supplied in full. It is the only file whose
  content you may read while planning.
- instruction: the user's task instruction.
- skill: the complete Skill text governing this build. When it contains a "Required
  coverage" section, treat every topic there as mandatory: assign the files implementing
  each topic to at least one page. Required coverage adds constraints; it never overrides
  the 8-12 page limit.

## Rules
1. Plan 8 to 12 pages total, index included. Each page needs a stable id, a title, an output
   path ending in .md, a page_kind (index/landing/deep/mechanism) and a nonempty file_paths.
2. Copy file_paths verbatim from paths. Never invent paths and never include line numbers.
3. Assign whole files; one file may appear on several pages. Unassigned files are excluded on
   purpose, so prefer the files carrying the architecture the README describes.
4. A deep or mechanism page groups the files that explain one mechanism end to end; do not
   create one page per file.
5. related lists ids of pages a reader should follow next.
6. When the skill input contains a "Required coverage" section, every topic listed there must
   be carried by at least one page whose file_paths include the modules implementing it;
   before returning, map each required topic to its carrying page and reject any unmapped topic.
Return only the structured object.
"""


def _fold_paths(paths: list[str], cap: int) -> list[str]:
    """Deterministically shrink an oversized tree, round-robin per top-level segment."""
    if len(paths) <= cap:
        return list(paths)
    groups: dict[str, list[str]] = {}
    for path in paths:
        groups.setdefault(path.split("/", 1)[0], []).append(path)
    queues = [iter(group) for group in groups.values()]
    folded: list[str] = []
    while len(folded) < cap and queues:
        remaining = []
        for queue in queues:
            item = next(queue, None)
            if item is None:
                continue
            folded.append(item)
            if len(folded) >= cap:
                break
            remaining.append(queue)
        queues = remaining
    return sorted(folded)


async def semantic_decide(
    runtime: Pipeline, source_summary: dict
) -> SemanticOutlineDecision | None:
    """Decide from Skill text whether semantic outline planning is mandated. Fail open."""
    try:
        decision = await runtime.model.ask(
            "semantic_outline_decide",
            _SEMANTIC_OUTLINE_DECIDER,
            {"skill": runtime.skill, "source_summary": source_summary},
            SemanticOutlineDecision,
        )
    except Exception as exc:  # noqa: BLE001 - decision failure must never crash planning
        runtime.warnings.append(
            f"Semantic outline decision failed; using deterministic outline: {str(exc)[:300]}"
        )
        return None
    await runtime.files.put("semantic-outline-decision", decision.model_dump())
    return decision


async def export_planning_brief(
    runtime: Pipeline, sources: list[Record], source_summary: dict
) -> dict:
    """Build the DeepWiki-style planning input: a body-less file tree plus one README body.

    Only the shortest-path README contributes content; every other file stays a path. When
    the tree exceeds the guard rails it folds deterministically and the fold is metered.
    """
    manifest = export_manifest(runtime, sources, source_summary)
    paths = manifest["paths"]
    readmes = [p for p in paths if _README_RE.match(p.rsplit("/", 1)[-1])]
    readme_path = min(readmes, key=len) if readmes else None
    readme_text = ""
    if readme_path is not None:
        chunks: list[tuple[int, str]] = []
        for record in sources:
            evidence = runtime.evidence[record.record_id]
            if _relative_path(runtime, evidence["uri"]) != readme_path:
                continue
            shard = await runtime.files.get(f"sources/{record.record_id}")
            text = shard.get("text") if shard else None
            if text:
                chunks.append((evidence["start_line"], text))
        readme_text = "\n".join(text for _, text in sorted(chunks))

    tree_chars = sum(len(path) + 1 for path in paths)
    folded = len(paths) > _PLANNING_TREE_MAX_FILES or tree_chars > _PLANNING_BRIEF_MAX_CHARS
    tree_paths = _fold_paths(paths, _PLANNING_TREE_MAX_FILES) if folded else paths
    runtime.metrics["planning_brief_files"] = len(paths)
    runtime.metrics["planning_brief_tree_files"] = len(tree_paths)
    runtime.metrics["planning_brief_folded"] = int(folded)
    return {
        "target": runtime.target,
        "instruction": runtime.request.instruction,
        "file_count": len(paths),
        "paths": tree_paths,
        "readme_path": readme_path,
        "readme": readme_text,
        "tree_folded": folded,
    }


async def plan_semantic_outline(runtime: Pipeline, brief: dict) -> SemanticOutline:
    """One model call turns the tree plus README into an 8-12 page file-level plan."""
    return await runtime.model.ask(
        "semantic_outline_plan",
        _SEMANTIC_OUTLINE_PLANNER,
        {**brief, "skill": runtime.skill},
        SemanticOutline,
    )


async def expand_semantic_outline(
    runtime: Pipeline,
    sources: list[Record],
    source_summary: dict,
    planned: SemanticOutline,
) -> Outline:
    """Deterministically join LLM file-level assignments onto seeded source spans.

    Unknown paths are dropped with a warning; a page with no surviving span fails the whole
    semantic attempt so the caller can fail over to the deterministic outline. Line numbers
    are never trusted from the model: spans come exclusively from the seeded manifest.
    """
    manifest = export_manifest(runtime, sources, source_summary)
    by_path = {node["path"]: node for node in manifest["files"]}
    pages: list[OutlinePage] = []
    assigned: set[str] = set()
    for page in planned.pages:
        spans: list[SourceSpan] = []
        for rel in dict.fromkeys(page.file_paths):
            node = by_path.get(rel)
            if node is None:
                runtime.warnings.append(f"Semantic outline dropped unknown path: {rel}")
                continue
            assigned.add(rel)
            spans.extend(
                SourceSpan(uri=node["uri"], start_line=start, end_line=end)
                for start, end in node["ranges"]
            )
        if not spans:
            raise ValueError(f"Semantic outline page {page.id} has no valid assigned file")
        pages.append(
            OutlinePage(id=page.id, title=page.title or page.id, path=page.path, sources=spans)
        )
    outline = Outline(pages=pages)
    _validate(runtime, outline, sources)
    runtime.metrics["semantic_outline_pages"] = len(pages)
    runtime.metrics["semantic_outline_files_assigned"] = len(assigned)
    runtime.metrics["semantic_outline_files_dropped"] = len(by_path) - len(assigned)
    await runtime.files.put("semantic-outline", outline.model_dump())
    return outline
