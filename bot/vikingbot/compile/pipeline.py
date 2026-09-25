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

_PLANNER = """## Task Description

You are the planner for a compile task. A compile task transforms source materials
into the outputs required by a Skill and the user's instruction.

Design the simplest sequence of processing steps that fulfills those requirements.
The runtime executes your plan to read the materials, process them and publish the outputs.
Return contract, which describes the work for each step, and plan, which specifies
which steps run and how their results pass between them. The emit tool defines the
fields you must fill in.

## Inputs

The user message contains a JSON object with the following fields:

- skill: the complete SKILL.md text describing the task requirements.
- request.instruction: the user's requirements for this compile task.
- request.to: the destination for the generated outputs.
- target_has_content: whether request.to contains any visible files or subdirectories.
- source_summary: file counts, sizes and splitting statistics; sizes are Unicode characters.
- runtime.time: the task timestamp for interpreting relative dates in the request.

Only the main Skill text is included. Source document contents and other files
referenced by the Skill are not included. Plan from the supplied requirements and
statistics; the execution steps inspect those contents.
Before execution, the runtime divides large source files into text segments called ranges.

## Available Operators

Operators are the processing steps supported by the runtime. Select and combine
those needed for the task. You may use an operator more than once; the four operators
below are not a required sequence.

### Map

Processes assigned source text. It can produce final files directly or return
structured intermediate items called records. Each record contains information
needed by later steps, such as facts extracted from a document.
Map can also process records produced by an earlier step.

### Shuffle

Collects records into groups according to a grouping rule.
Each group is a collection of records that Reduce will process together.

### Reduce

Processes all records in a group together, combining their information.
It can produce final files or new records for further processing.
One group may produce multiple output files.

### Finalize

Publishes the generated files to the requested destination and ends the flow.

## Plan Syntax

Write plan as a string of assignments and operator calls. The runtime provides
p for calling the operators, sources for the prepared source materials, and target
for the output destination request.to. Other variables name results of earlier steps.

These are examples of individual calls, not a complete plan or a prescribed sequence:

- Map: mapped = p.map(sources, task=contract.extract)
  mapped names the result; sources is the input; task selects the work configuration.
  A previous records result can replace sources.
- Shuffle: grouped = p.shuffle(records, by=contract.routing)
  records is a previous records result; by selects the grouping rule.
  Add against=target only when target_has_content is true and the task requires
  comparing, updating or integrating existing target content.
  Otherwise grouping uses only the current inputs.
- Reduce: combined = p.reduce(groups, task=contract.reduce)
  groups is a previous Shuffle result; task selects the work configuration.
- Finalize: p.finalize(files, into=target)
  files is a previous Map or Reduce result containing output files.

The task argument can reference contract.extract, contract.reduce or contract.synthesize.
Each contains instructions and settings for the work to perform. Any of them can be used
by Map or Reduce; their names suggest common uses rather than operator types or positions.
The by argument can reference contract.routing or contract.final_routing.
Use these configuration names; reuse a configuration when multiple steps need the same work.
Define optional work configurations and grouping rules only when referenced by the plan.

## Plan Rules

Write a single sequence of the operator calls shown above. Do not include
Python control flow, imports or other function calls.
Give each intermediate result a new variable name and pass it to exactly
one subsequent call. End the sequence with one Finalize call.

Each call describes a processing step over a collection of data.
The runtime assigns files, text ranges or records to individual jobs within
that step; do not write a separate call for each source file or job.

## Execution Capabilities

Map/Reduce can read their assigned evidence and files referenced by the Skill.
A work configuration with execution=agent also permits private scratch files and
Skill-supplied Python scripts. Execution steps cannot scan all history, access external
networks or run arbitrary scratch scripts.

"""


_SKILL_OUTPUT = """The target is a Skill namespace. The whole task must deliver one complete Skill package.

Keep all package files under one <skill-name>/ directory.
Set contract.output_format=files and include <skill-name>/SKILL.md in
contract.required_paths, using the same package directory for every output file.

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
            source_summary = self.summarize_sources(sources)
            target_has_content = bool(await self.client.list_resources(self.target, node_limit=1))

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

            proposal = await self.model.ask(
                "plan",
                _PLANNER + self.output_instructions,
                {
                    "skill": self.skill,
                    "runtime": {"time": runtime["time"]},
                    "request": {
                        "to": self.target,
                        "instruction": self.request.instruction,
                    },
                    "source_summary": source_summary,
                    "target_has_content": target_has_content,
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
                    include={"preserve", "required_paths"}
                )
                + "\n\n# Output rules\n"
                + self.output_instructions
                + "\n# Runtime\n"
                + json.dumps(prompt_runtime)
                + "\n"
            )
            await self.files.put(
                "contract",
                {
                    "contract": self.contract.model_dump(),
                    "skill": self.skill,
                    "dependencies": dict(self.resources.hashes),
                },
            )
            await self.files.put(
                "plan",
                {
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

    def summarize_sources(self, sources: list[Record]) -> dict[str, Any]:
        """Return file-size and fragmentation statistics for seeded source records.

        Counts use non-overlapping Unicode character ranges from evidence metadata,
        excluding repeated reading context. Percentiles use nearest ranks. Empty
        input yields zero counts. Source bodies are neither read nor modified.
        """
        chars: Counter[str] = Counter()
        ranges: Counter[str] = Counter()
        for record in sources:
            evidence = self.evidence[record.record_id]
            uri = evidence["uri"]
            chars[uri] += evidence["end_char"] - evidence["start_char"]
            ranges[uri] += 1

        lengths = sorted(chars.values())
        count = len(lengths)

        def percentile(percent: int) -> int:
            """Return a nearest-rank file length for a percentile in 1..100, or zero if empty."""
            return lengths[(count * percent + 99) // 100 - 1] if count else 0

        return {
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
