"""Finite Map/Shuffle/Reduce/Finalize execution inside an existing Compile task."""

from __future__ import annotations

import ast
import json
import time
from collections import Counter
from dataclasses import asdict
from typing import Any

from loguru import logger

from openviking.core.namespace import classify_uri
from openviking.utils.path_safety import safe_join_viking_uri
from vikingbot.compile import entry
from vikingbot.compile.hashing import content_hash, digest
from vikingbot.compile.models import CompileFailure, utc_now
from vikingbot.compile.ops import finalize as finalize_op
from vikingbot.compile.ops import map as map_op
from vikingbot.compile.ops import reduce as reduce_op
from vikingbot.compile.ops.shuffle import Shuffle
from vikingbot.compile.pipeline_io import JsonModel, TaskFiles, bounded_jobs
from vikingbot.compile.plan import PLANNING_RULES, Contract, PlanProposal, parse_plan
from vikingbot.compile.renderer import RenderedBundle
from vikingbot.compile.results import Record
from vikingbot.compile.review import REVIEW_PROMPT
from vikingbot.compile.review import run as review_stage
from vikingbot.compile.skill_resources import SkillResources

_PLANNER = """## Task Description

You are the planner for a compile task. A compile task transforms source materials
into the outputs required by a Skill and the user's instruction.

Design the simplest sequence of processing steps that fulfills those requirements.

Design processing instructions and record fields so intermediate results
contain the information needed by later steps without unnecessary repetition.
Preserve required details, source references and applicability conditions.

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

""" + PLANNING_RULES.format(
    input_binding="p for calling the operators, sources for the prepared source materials, and target\n"
    "for the output destination request.to. Other variables name results of earlier steps."
)


_SKILL_OUTPUT = """The target is a Skill namespace. The whole task must deliver one complete Skill package.

Keep all package files under one <skill-name>/ directory and include
<skill-name>/SKILL.md in the final output.

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
        self.files = TaskFiles(sandbox)
        self.metrics: Counter = Counter()
        self.model = JsonModel(
            provider, model, temperature, self.files, limits, usage, self.metrics
        )
        self.resources = SkillResources(client, request.skill, self.files)
        self.model.resources = self.resources
        self.contract: Contract
        self.direct_output_format: str | None = None
        self.system = ""
        self.records: dict[str, Record] = {}
        self.evidence: dict[str, dict] = {}
        self.status: dict[str, str] = {}
        self.old: dict[str, str | bytes | None] = {}
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.artifacts: list[str] = []  # Only fully accepted final-file submissions.
        self.catalog: dict[str, dict] = {}  # Accepted task outputs, never historical enumeration.
        # Each output path belongs to the group whose file is accepted last.
        self.owners: dict[str, str] = {}

    @property
    def output_format(self) -> str:
        """Return the direct submission's format or the current planner contract's format."""
        return self.direct_output_format or self.contract.output_format

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
        self.direct_output_format = None
        failed = False
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
                if self.skill_target and proposal.contract.output_format != "files":
                    raise ValueError("Skill output requires output_format=files")

            planner_prompt = _PLANNER + self.output_instructions
            if self.skill_target:
                planner_prompt += "\nSet contract.output_format=files.\n"
            planning_context = {
                "skill": self.skill,
                "runtime": {"time": runtime["time"]},
                "request": {"to": self.target, "instruction": self.request.instruction},
                "source_summary": source_summary,
                "target_has_content": target_has_content,
            }
            direct = await entry.run(self, sources, planning_context)
            if direct is not None:
                result = await finalize_op.run(self, direct)
                complete = not self.failures
                self.metrics["direct_completed"] = 1
                return result
            proposal = await self.model.ask(
                "plan",
                planner_prompt,
                planning_context,
                PlanProposal,
                validate_plan,
            )
            self.contract = proposal.contract
            nodes = parse_plan(proposal.plan, self.contract)
            self.system = (
                "# Original Skill (authoritative)\n"
                + self.skill
                + "\n\n# User instruction\n"
                + self.request.instruction
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
            program, position, revisions = proposal.plan, 0, 0
            completed_names = set()
            while position < len(nodes):
                node = nodes[position]
                active_stage = node.name
                stage_start = time.monotonic()
                configuration = getattr(self.contract, node.task) if node.task else None
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
                    result = await finalize_op.run(self, inputs)
                    complete = not self.failures
                self.metrics[f"{node.name}_milliseconds"] = round(
                    (time.monotonic() - stage_start) * 1000
                )
                position += 1
                completed_names.add(node.name)
                if node.op != "finalize":
                    outputs = data[node.name]
                    await self.files.put(
                        f"stages/{node.name}",
                        {
                            "node": asdict(node),
                            "configuration": configuration.model_dump()
                            if hasattr(configuration, "model_dump")
                            else configuration,
                            "outputs": [
                                item.record_id if node.op != "shuffle" else item.group_id
                                for item in outputs
                            ]
                            if node.op == "shuffle" or configuration.output == "records"
                            else outputs,
                            "revisions": revisions,
                        },
                    )
                    statements = ast.parse(program).body
                    remaining_plan = "\n".join(ast.unparse(s) for s in statements[position:])
                    active_stage, stage_start = f"review-{node.name}", time.monotonic()
                    accepted = await review_stage(
                        self,
                        node,
                        outputs,
                        remaining_plan,
                        completed_names,
                        revisions,
                        REVIEW_PROMPT + self.output_instructions,
                        planning_context,
                    )
                    if accepted:
                        decision, suffix = accepted
                        nodes = nodes[:position] + suffix
                        program = "\n".join(ast.unparse(s) for s in statements[:position])
                        program += "\n" + decision.plan
                        await self.files.put(
                            "plan",
                            {
                                "program": program,
                                "nodes": [asdict(n) for n in nodes],
                                "contract": decision.contract.model_dump(),
                                "revisions": revisions + 1,
                            },
                        )
                        self.contract = decision.contract
                        revisions += 1
                        self.metrics["plan_revisions"] = revisions
                    self.metrics[f"review-{node.name}_milliseconds"] = round(
                        (time.monotonic() - stage_start) * 1000
                    )
                active_stage = None
            return result
        except BaseException as exc:
            failed = True
            self.failures.append(str(exc)[:800] or type(exc).__name__)
            if isinstance(exc, Exception) and not isinstance(exc, CompileFailure):
                raise CompileFailure(
                    "COMPILE_INCOMPLETE", self.failures[-1], stage="pipeline"
                ) from exc
            raise
        finally:
            if active_stage is not None:
                self.metrics[f"{active_stage}_milliseconds"] = round(
                    (time.monotonic() - stage_start) * 1000
                )
            try:
                await self.write_coverage()
                self.metrics["total_milliseconds"] = round((time.monotonic() - started) * 1000)
                self.metrics["pending"] = sum(s == "pending" for s in self.status.values())
                self.metrics["unreferenced"] = sum(
                    s == "unreferenced" for s in self.status.values()
                )
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
            except Exception:
                if not failed:
                    raise
                logger.exception(
                    "Compile state finalization failed; preserving the execution error"
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
        """Write unique source ranges with bounded concurrency, retaining their input order."""
        result = []
        pending = []
        seen = set(self.records)

        async def write_source(item):
            """Atomically write one (ID, metadata, range); return its record and evidence."""
            record_id, metadata, part = item
            await self.files.put(
                f"sources/{record_id}", {**metadata, "text": part.content, "context": part.context}
            )
            return Record(
                record_id, f"sources/{record_id}", [record_id], part.uri, {}, []
            ), metadata

        async def flush():
            """Register successful writes in source order; report any failed writes."""
            errors = []
            for record, metadata in await bounded_jobs(
                pending,
                write_source,
                concurrency=self.limits.source_concurrency,
                metrics=self.metrics,
                failures=errors,
            ):
                self.evidence[record.record_id] = metadata
                self.register(record)
                result.append(record)
            pending.clear()
            if errors:
                raise ValueError(errors[0])

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
                if record_id in seen:
                    continue
                seen.add(record_id)
                pending.append((record_id, metadata, part))
                if len(pending) >= self.limits.source_concurrency:
                    await flush()
        if pending:
            await flush()
        return result

    def register(self, record: Record) -> None:
        """Track every record's lineage and disposition without truncating the collection."""
        self.records[record.record_id] = record
        self.status.setdefault(record.record_id, "pending")
