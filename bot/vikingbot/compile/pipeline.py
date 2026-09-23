"""Finite Map/Shuffle/Reduce/Finalize execution inside an existing Compile task."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict
from typing import Any

from openviking.core.namespace import classify_uri
from openviking.utils.path_safety import safe_join_viking_uri
from openviking.utils.skill_processor import validate_skill_name
from openviking_cli.exceptions import OpenVikingError
from vikingbot.compile.models import CompileFailure, utc_now
from vikingbot.compile.ops import finalize as finalize_op
from vikingbot.compile.ops import map as map_op
from vikingbot.compile.ops import reduce as reduce_op
from vikingbot.compile.ops.shuffle import Shuffle
from vikingbot.compile.pipeline_io import JsonModel, TaskFiles
from vikingbot.compile.plan import (
    Contract,
    PlanProposal,
    Record,
    content_hash,
    digest,
    parse_plan,
)
from vikingbot.compile.renderer import RenderedBundle
from vikingbot.compile.skill_resources import SkillResources

_PLANNER = """Plan the input collection using the original Skill, its attachments, and request.instruction.
Choose the simplest plan that satisfies the requested result. Determine the deliverables and
which inputs each depends on before choosing operators. Do not assume one output per input.
Use source_summary for file sizes and fragmentation.
Samples come from files selected by length; they are excerpts,
not complete documents or representative coverage of all topics.

Operators

- Map processes independent units into records or files. Use input_unit=file to keep each
  source file together, or range to allow source-range batching. Intermediate records stay separate.
- Shuffle groups records. Use a routing string for semantic grouping, or {"mode":"all"}
  to put all records into one group. Make routing criteria self-contained: Shuffle receives
  record evidence, but not the original Skill or instruction.
- Reduce synthesizes each group into records or files. One group may produce several files.
  Resolve shared identities and references before generating files that depend on them.
- Finalize publishes files.

Stage instructions

The Skill and request.instruction define the whole task. Each transform's instructions must
explain its assigned work, input scope, expected output, and what later stages do with that output.

For work requiring later synthesis, ask for the evidence needed downstream and identify
which later stage produces the final result. If assigned inputs are only part of the collection,
say so explicitly; do not let the transform treat its local assignment as the whole task.
For independently complete work, ask for the finished deliverable.

Reference Skill rules without copying them. Preserve required facts, conditions, exceptions
and uncertainty.

Records

Define payload fields with short, plain descriptions. File outputs need no custom fields.
The runtime supplies source IDs, ranges, hashes and counts.

Keep evidence for joint synthesis in payload. Independently complete files may use
ready_content/ref with ready_path; keep only useful identity and relationship information
in payload instead of duplicating the finished content.

Use distinguish to describe scope fields, for example:
{"subject": "The entity and where the facts apply", "version": "The effective version"}.
Scope describes applicability, not exact-match grouping keys. Paths alone do not determine groups.

Plan syntax

Declare only the transforms and routing rules used. Omit plan to select this default:

records = p.map(sources, task=contract.extract)
groups = p.shuffle(records, by=contract.routing, against=target)
changes = p.reduce(groups, task=contract.reduce)
p.finalize(changes, into=target)

For independent file output, set extract.output=files and provide a Map -> Finalize plan.
Use against=target when existing target content matters; omit it for intermediate grouping.

Custom plans use this assignment syntax with sources, target, contract and p.
Map accepts sources or records; Shuffle accepts records; Reduce accepts groups;
Finalize accepts files. Consume each dataset once and finish with one Finalize.
Do not use imports, loops, arbitrary calls, or enumerate individual inputs and jobs.

Execution and contract settings

Transforms default to execution=direct. extract defaults to records; reduce defaults to files.
Choose execution=agent for iterative work, Skill scripts, or scratch-file processing.
Oversized direct calls also use agents with scratch assignments and scoped reads.

All settings are direct fields of contract:
- preserve and validation: additional requirements beyond the Skill.
- required_paths: prescribed exact relative output paths, without wildcards or guessed filenames.
- output_format: files for ordinary or mixed text files; wiki for OKF pages.
- unsupported: missing capabilities that prevent completing the task. Report these explicitly.

For additional synthesis stages, define contract.synthesize and contract.final_routing.
Use output=records for intermediate Reduce stages and output=files for final deliverables.

Set contract.overflow to direct or structured.
direct sends the whole group to Reduce. structured uses contract.combine to produce
intermediate records when the group exceeds the input budget.
Define contract.combine with output=records when selecting structured.

Reduce reads the overflow policy from contract automatically.
Its plan syntax is p.reduce(groups, task=contract.reduce).
The reduce call accepts only the task keyword.

Available capabilities

The runtime handles source rereads, scoped history recall, provenance and conditional writes.
Read missing Skill resources with read_skill_resource.
Agents can read, write and edit private scratch files and run Skill-supplied Python scripts
through run_skill_script. They cannot scan all history, access external networks, or execute
arbitrary scratch scripts.
"""


_SKILL_OUTPUT = """The target is a Skill namespace. The whole task must deliver one complete Skill package.
Each transform follows its assigned instructions and contributes intermediate evidence
or complete files to that package.

Keep all package files under one <skill-name>/ directory.
Choose the package name once in the contract.
Set contract.output_format=files and include <skill-name>/SKILL.md in
contract.required_paths.

The final SKILL.md must have YAML frontmatter with name matching the package directory
and a nonempty description. Preserve attachments in their native formats.
"""


class Pipeline:
    """A task-owned finite executor, reusing provider slots, sandbox, client and commit API.

    Payloads/evidence stay in task files; batching targets guide model assignments.
    The lifecycle is owned by BotCompileService; this object provides same-workspace
    retries, not automatic service-crash recovery. Input collections have no fixed
    aggregate size or elapsed-time cutoff; individual calls retain execution timeouts.
    """

    def __init__(
        self, *, client, sandbox, provider, model, temperature, limits, request, skill, usage
    ):
        self.client, self.limits, self.request = client, limits, request
        self.target, self.skill = request.to, skill
        self.skill_target = classify_uri(self.target).context_type == "skill"
        self.skill_name = ""  # One package directory, selected by the validated contract.
        self.files = TaskFiles(sandbox)
        self.metrics: Counter = Counter()
        self.model = JsonModel(
            provider, model, temperature, self.files, limits, usage, self.metrics
        )
        self.resources = SkillResources(client, request.skill, self.files)
        self.model.resources = self.resources
        self.contract: Contract
        self.system = ""
        self.records: dict[str, Record] = {}
        self.evidence: dict[str, dict] = {}
        self.status: dict[str, str] = {}
        self.old: dict[str, str | None] = {}
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.artifacts: list[str] = []  # Only fully accepted final-file submissions.
        self.catalog: dict[str, dict] = {}  # Accepted task outputs, never historical enumeration.
        # Each output path belongs to the group whose file is accepted last.
        self.owners: dict[str, str] = {}

    @property
    def output_instructions(self) -> str:
        """Return shared output-root semantics and target-specific output constraints."""
        instructions = (
            '"to" in the instruction refers to the compile output root; return paths '
            'relative to it without adding a literal "to/" prefix.\n'
        )
        if self.skill_target:
            return instructions + _SKILL_OUTPUT
        return instructions

    async def run(self, batches) -> RenderedBundle:
        """Plan, expand and execute collections; errors preserve honest pending/failed states."""
        started = time.monotonic()
        complete = False
        active_stage, stage_start = None, started
        self.records.clear()
        self.evidence.clear()
        self.status.clear()
        self.old.clear()
        self.failures.clear()
        self.warnings.clear()
        self.artifacts.clear()
        self.catalog.clear()
        self.owners.clear()
        try:
            sources = await self.seed(batches)
            runtime = await self.files.get("runtime")
            if runtime is None:
                runtime = {
                    "model": self.model.model,
                    "model_settings": self.model.identity,
                    "time": utc_now(),
                    "skill": self.request.skill,
                    "wiki_links": self.request.wiki_links,
                }
                await self.files.put("runtime", runtime)
            if runtime.get("wiki_links", False) != self.request.wiki_links:
                raise ValueError("Wiki link setting differs from the task runtime")
            prompt_runtime = {
                key: value for key, value in runtime.items() if key in {"time", "skill"}
            }
            self.metrics["input_ranges"] = len(sources)
            self.metrics["input_files"] = len({x["uri"] for x in self.evidence.values()})
            source_summary, samples = await self.summarize_sources(sources)
            references = self.resources.references(self.skill)
            for reference in references:
                await self.resources.read(reference)

            def validate_plan(proposal):
                parse_plan(proposal.plan, proposal.contract)
                if self.skill_target:
                    paths = [
                        p
                        for p in proposal.contract.required_paths
                        if p.count("/") == 1 and p.endswith("/SKILL.md")
                    ]
                    if len(paths) != 1 or proposal.contract.output_format != "files":
                        raise ValueError(
                            "Skill output requires files and one required <skill-name>/SKILL.md"
                        )
                    try:
                        name = validate_skill_name(paths[0].split("/")[0])
                    except OpenVikingError as exc:
                        raise ValueError(str(exc)) from exc
                    if any(not p.startswith(name + "/") for p in proposal.contract.required_paths):
                        raise ValueError("Required Skill outputs must share the package directory")
                missing = set(references) - set(self.resources.hashes)
                if missing:
                    raise ValueError(
                        f"Read the referenced Skill resources before planning: {sorted(missing)}"
                    )

            proposal = await self.model.ask(
                "plan",
                _PLANNER + self.output_instructions,
                {
                    "skill": self.skill,
                    "skill_resources": references,
                    "runtime": prompt_runtime,
                    "request": {
                        "to": self.target,
                        "instruction": self.request.instruction,
                        "source_root_count": len(self.request.from_),
                    },
                    "source_counts": dict(self.metrics),
                    "source_summary": source_summary,
                    "samples": samples,
                },
                PlanProposal,
                validate_plan,
            )
            self.contract = proposal.contract
            if self.skill_target:
                self.skill_name = next(
                    p.split("/")[0]
                    for p in self.contract.required_paths
                    if p.count("/") == 1 and p.endswith("/SKILL.md")
                )
            nodes = parse_plan(proposal.plan, self.contract)
            self.system = (
                "# Original Skill (authoritative)\n"
                + self.skill
                + "\n\n# User instruction\n"
                + self.request.instruction
                + "\n\n# Shared requirements\n"
                + self.contract.model_dump_json(
                    include={"preserve", "validation", "required_paths"}
                )
                + "\n\n# Output rules\n"
                + self.output_instructions
                + "\n# Runtime\n"
                + json.dumps(prompt_runtime)
                + "\n\n# Skill attachments (read necessary rules before use)\n"
                + json.dumps(self.resources.hashes)
                + "\n"
            )
            contract_hash = digest(
                [self.skill, self.contract.model_dump(), self.model.identity, self.resources.hashes]
            )
            await self.files.put(
                "contract",
                {
                    "hash": contract_hash,
                    "contract": self.contract.model_dump(),
                    "skill": self.skill,
                    "dependencies": dict(self.resources.hashes),
                },
            )
            await self.files.put(
                "plan",
                {
                    "contract_hash": contract_hash,
                    "program": proposal.plan,
                    "nodes": [asdict(n) for n in nodes],
                },
            )
            data: dict[str, Any] = {"sources": sources}
            shuffler = Shuffle(self)
            for node in nodes:
                active_stage = node.name
                stage_start = time.monotonic()
                inputs = data.pop(node.source)
                if node.op == "map":
                    data[node.name] = await map_op.run(self, node, inputs)
                elif node.op == "shuffle":
                    data[node.name] = await shuffler.run(node, inputs)
                elif node.op == "reduce":
                    data[node.name] = await reduce_op.run(self, node, inputs)
                else:
                    if self.failures:
                        if not inputs:
                            raise ValueError("No accepted final artifacts remain after failed jobs")
                        self.warnings.append(
                            "Partial output; unfinished jobs: " + self.failures[0][:500]
                        )
                    result = await finalize_op.run(self, inputs, partial=bool(self.failures))
                    complete = not self.failures
                self.metrics[f"{node.name}_milliseconds"] = round(
                    (time.monotonic() - stage_start) * 1000
                )
                active_stage = None
            return result
        except BaseException as exc:
            self.failures.append(str(exc)[:800] or type(exc).__name__)
            if isinstance(exc, Exception):
                raise CompileFailure(
                    "COMPILE_INCOMPLETE", self.failures[-1], stage="pipeline"
                ) from exc
            raise
        finally:
            if active_stage is not None:
                self.metrics[f"{active_stage}_milliseconds"] = round(
                    (time.monotonic() - stage_start) * 1000
                )
            await self.write_coverage()
            self.metrics["total_milliseconds"] = round((time.monotonic() - started) * 1000)
            self.metrics["pending"] = sum(s == "pending" for s in self.status.values())
            self.metrics["unreferenced"] = sum(s == "unreferenced" for s in self.status.values())
            self.metrics["failed"] = sum(s == "failed" for s in self.status.values())
            await self.files.put(
                "summary",
                {
                    "prepared": complete,
                    "committed": False,
                    "metrics": dict(self.metrics),
                    "states": self.status,
                    "errors": self.failures,
                    "warnings": self.warnings,
                },
            )

    async def write_coverage(self, published=()):
        """Record every input's lineage disposition and acknowledged final files with hashes.

        Published URIs come from file/package acknowledgements or verified unchanged
        bytes. A source is delivered only when all its branches finish and all prepared
        supporting files are acknowledged. Unreferenced branches have unconfirmed coverage,
        not an approved exclusion. References alone do not prove semantic quality.
        """
        children = {}
        for record in self.records.values():
            for parent in record.parents:
                children.setdefault(parent, []).append(record.record_id)

        def disposition(states):
            for state in ("failed", "pending", "unreferenced"):
                if state in states:
                    return state
            return "prepared"

        for record in reversed(list(self.records.values())):
            if record.record_id in children and self.status[record.record_id] != "failed":
                self.status[record.record_id] = disposition(
                    [self.status[child] for child in children[record.record_id]]
                )
        manifest = await self.files.get("inputs") or {}
        inputs = {
            uri: {"ranges": {}, "outputs": [], "status": state} for uri, state in manifest.items()
        }
        for reference, evidence in self.evidence.items():
            inputs[evidence["uri"]]["ranges"][reference] = {
                **evidence,
                "status": self.status[reference],
            }
        finalized = await self.files.get("finalize") or {}
        published = set(published)
        for path, output in finalized.get("outputs", {}).items():
            uri = safe_join_viking_uri(self.target, path)
            for source in {self.evidence[ref]["uri"] for ref in output["source_refs"]}:
                inputs[source]["outputs"].append(
                    {
                        "uri": uri,
                        "sha256": output["sha256"],
                        "delivered": uri in published,
                        "source_ranges": [
                            ref
                            for ref in output["source_refs"]
                            if self.evidence[ref]["uri"] == source
                        ],
                    }
                )
        for uri, item in inputs.items():
            if item["ranges"]:
                item["status"] = disposition([r["status"] for r in item["ranges"].values()])
            if (
                item["status"] == "prepared"
                and item["outputs"]
                and all(o["delivered"] for o in item["outputs"])
            ):
                item["status"] = "delivered"
            manifest[uri] = item["status"]
        await self.files.put("inputs", manifest)
        await self.files.put(
            "coverage", {"counts": dict(Counter(manifest.values())), "inputs": inputs}
        )

    async def summarize_sources(
        self, sources: list[Record]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Summarize seeded sources and sample files by length for the planner.

        Sources retain seed order and non-overlapping Unicode character ranges;
        counts exclude repeated reading context. Percentiles use nearest ranks.
        Samples contain at most 500 characters from the first range of the smallest,
        median and largest files, with duplicate selections removed. They represent
        file lengths, not semantic categories. Empty input yields zero counts and
        no samples. Only selected source shards are read; stored sources are unchanged.
        """
        chars: Counter[str] = Counter()
        ranges: Counter[str] = Counter()
        first: dict[str, Record] = {}
        for record in sources:
            evidence = self.evidence[record.record_id]
            uri = evidence["uri"]
            chars[uri] += evidence["end_char"] - evidence["start_char"]
            ranges[uri] += 1
            first.setdefault(uri, record)

        ordered = sorted(chars, key=lambda uri: (chars[uri], uri))
        lengths = [chars[uri] for uri in ordered]
        count = len(lengths)

        def percentile(percent: int) -> int:
            """Return a nearest-rank file length for a percentile in 1..100, or zero if empty."""
            return lengths[(count * percent + 99) // 100 - 1] if count else 0

        summary = {
            "file_count": count,
            "range_count": len(sources),
            "total_chars": sum(lengths),
            "file_chars": {
                "min": min(lengths, default=0),
                "p50": percentile(50),
                "p90": percentile(90),
                "max": max(lengths, default=0),
            },
            "files_with_multiple_ranges": sum(n > 1 for n in ranges.values()),
            "max_ranges_per_file": max(ranges.values(), default=0),
        }
        samples = []
        if ordered:
            for index in sorted({0, (count - 1) // 2, count - 1}):
                uri = ordered[index]
                source = await self.files.get(first[uri].payload_ref)
                excerpt = source["text"][:500]
                samples.append(
                    {
                        "uri": uri,
                        "file_chars": chars[uri],
                        "range_count": ranges[uri],
                        "excerpt": excerpt,
                        "excerpt_is_complete_file": len(excerpt) == chars[uri],
                    }
                )
        return summary, samples

    async def seed(self, batches) -> list[Record]:
        """Retain exact ranges with content hashes and offsets before any transformation."""
        result = []
        async for batch in batches:
            for part in batch:
                metadata = {
                    "uri": part.uri,
                    "hash": content_hash(part.content),
                    "start_char": part.start_char,
                    "end_char": part.end_char,
                    "start_line": part.start_line,
                    "end_line": part.end_line,
                    "continues_before": part.continues_before,
                    "continues_after": part.continues_after,
                }
                record_id = digest(metadata)[:24]
                if record_id in self.records:
                    continue
                self.evidence[record_id] = metadata
                await self.files.put(
                    f"sources/{record_id}",
                    {**metadata, "text": part.content, "context": part.context},
                )
                record = Record(record_id, f"sources/{record_id}", [record_id], part.uri, {}, [])
                self.register(record)
                result.append(record)
        return result

    def register(self, record: Record) -> None:
        """Track every record's lineage and disposition without truncating the collection."""
        self.records[record.record_id] = record
        self.status.setdefault(record.record_id, "pending")
