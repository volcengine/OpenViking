"""Typed, collection-level Compile plans. Python syntax is parsed, never executed."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vikingbot.compile.results import RecordDraft

# Shared operator and DSL rules; input_binding describes the collection available to each role.
PLANNING_RULES = """## Available Operators

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
{input_binding}

These are examples of individual calls, not a complete plan or a prescribed sequence:

- Map: mapped = p.map(sources, task=contract.extract)
  mapped names the result; sources is the input; task selects the work configuration.
  A previous records result can replace sources.
- Shuffle: grouped = p.shuffle(records, by=contract.routing)
  records is a previous records result; by selects the grouping rule.
  Add against=target only when target_has_content is true and the task requires
  comparing, updating or integrating existing target content.
  Its consuming Reduce must produce files.
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
Map accepts sources or records; Shuffle accepts records; Reduce accepts groups;
Finalize accepts files. Other input types are unsupported.

Each call describes a processing step over a collection of data.
The runtime assigns files, text ranges or records to individual jobs within
that step; do not write a separate call for each source file or job.

## Access Boundaries

Work with the assigned materials. Avoid scanning all history.

"""


# Common tasks share one collection flow; explicit plans still pass the AST whitelist.
DEFAULT_PLAN = (
    "records = p.map(sources, task=contract.extract)\n"
    "groups = p.shuffle(records, by=contract.routing)\n"
    "changes = p.reduce(groups, task=contract.reduce)\n"
    "p.finalize(changes, into=target)"
)


class PlanModel(BaseModel):
    """Discard undeclared planner fields while validating declared fields and constraints.

    Open dictionaries such as transform fields and scope definitions retain their
    entries; only unknown model attributes are omitted from the parsed plan.
    """

    model_config = ConfigDict(extra="ignore")


class Transform(PlanModel):
    """A bounded transformation; fields declare the permitted intermediate structure."""

    instructions: str = Field(
        min_length=1,
        description="Work on assigned inputs, expected results and any step-specific constraints or checks. "
        "Map/Reduce also receive the full Skill and user instruction; avoid repeating them.",
    )
    output: Literal["records", "files"] = Field(
        default="records",
        description="records carries evidence to later steps; files produces output files.",
    )
    execution: Literal["direct", "agent"] = Field(
        description="Use direct for straightforward tasks with small results. "
        "Prefer agent for detailed knowledge compilation or large results. "
        "Both modes can read assigned materials and Skill references.",
    )
    input_unit: Literal["range", "file"] = Field(
        default="range",
        description="When Map reads source files: use file when processing needs context across "
        "sections of the same document; otherwise use range. file keeps each file's ranges together; "
        "range permits separate or batched ranges. Map over records handles each record separately.",
    )
    fields: dict[str, str] = Field(
        default_factory=lambda: {"text": "Facts extracted from the source."},
        description="Record content field names mapped to instructions for producing their values; "
        'e.g. {"facts": "Facts, conditions and exceptions to retain"}. Omit for files output.',
    )

    @field_validator("fields", mode="before")
    @classmethod
    def field_descriptions(cls, value):
        """Preserve field order, treating name-only lists and null descriptions as undescribed."""
        if isinstance(value, list) and all(isinstance(name, str) for name in value):
            return dict.fromkeys(value, "")
        if isinstance(value, dict):
            return {
                name: "" if description is None else description
                for name, description in value.items()
            }
        return value

    @field_validator("fields")
    @classmethod
    def check_fields(cls, value):
        """Preserve business field descriptions and order while omitting reserved names.

        The record result model defines the reserved envelope fields.
        An empty mapping is valid when no business fields remain after normalization.
        """
        reserved = RecordDraft.model_fields.keys() - {"payload"}
        return {name: description for name, description in value.items() if name not in reserved}


class Routing(PlanModel):
    """Group records by semantic instructions or preserve the entire collection as one group."""

    mode: Literal["semantic", "all"] = Field(
        default="semantic",
        description="semantic groups related records using instructions; all puts every record in one group.",
    )
    instructions: str = Field(
        default="",
        description="Which records belong together and why; required for semantic mode. "
        "Specify the purpose of joint processing and criteria for keeping records separate. "
        "Must stand alone: Shuffle does not receive the Skill or user instruction.",
    )

    @model_validator(mode="after")
    def check_instructions(self) -> Routing:
        """Semantic grouping needs criteria; global grouping needs no model decision."""
        if self.mode == "semantic" and not self.instructions.strip():
            raise ValueError("Semantic routing requires instructions")
        return self


class Contract(PlanModel):
    """Task-local interpretation of the original Skill, which remains authoritative.

    distinguish maps scope field names to their extraction meanings.
    Scope values describe evidence; they are not equality keys for candidate grouping.
    No identifiers in this contract enumerate individual input documents.
    """

    extract: Transform = Field(
        description="Work configuration, typically for processing sources: extract facts or generate files.",
    )
    reduce: Transform | None = Field(
        default=None,
        description="Additional work configuration, typically consolidating related facts into a topic summary.",
    )
    synthesize: Transform | None = Field(
        default=None,
        description="Additional work configuration, typically processing earlier results into final deliverables.",
    )
    routing: str | Routing = Field(
        default="",
        description="Grouping rule referenced by Shuffle's by parameter; omit when not used.",
    )
    final_routing: str | Routing = Field(
        default="",
        description="Another grouping rule for a Shuffle step needing different criteria; same format as routing.",
    )
    distinguish: dict[str, str] = Field(
        default_factory=dict,
        description="Record applicability field names mapped to extraction instructions, not actual values; "
        'e.g. {"version": "Product version these facts apply to"}. These are not exact-match grouping keys.',
    )
    output_format: Literal["wiki", "files"] = Field(
        default="files",
        description="Use files for ordinary file outputs or wiki for Markdown knowledge pages. "
        "The Skill defines page metadata, types and structure.",
    )

    @field_validator("distinguish", mode="before")
    @classmethod
    def scope_descriptions(cls, value):
        """Read saved name lists as fields without descriptions; mappings retain their meanings."""
        return dict.fromkeys(value, "") if isinstance(value, list) else value

    @model_validator(mode="before")
    @classmethod
    def apply_transform_defaults(cls, value):
        """Default Reduce output to files while preserving explicit output choices."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if isinstance(value.get("reduce"), dict):
            value["reduce"] = {"output": "files", **value["reduce"]}
        return value


class PlanProposal(PlanModel):
    """A task contract with an optional custom flow; omission uses the four standard operators."""

    contract: Contract = Field(
        description="Work definitions, grouping rules and output requirements referenced by the plan.",
    )
    plan: str = Field(
        default=DEFAULT_PLAN,
        min_length=1,
        max_length=8000,
        description="Assignments and operator calls as a string, connecting the chosen steps and ending with Finalize.",
    )


class ReviewDecision(PlanModel):
    """A future-plan proposal; acceptance never changes completed outputs or task failures."""

    action: Literal["continue", "revise"]
    reason: str = Field(
        default="", max_length=600, description="Brief observed basis for the decision."
    )
    contract: Contract | None = Field(default=None, description="Complete contract for a revision.")
    plan: str | None = Field(
        default=None,
        min_length=1,
        max_length=8000,
        description="Complete remaining DSL starting from current.handle and ending with Finalize.",
    )

    @model_validator(mode="after")
    def check_revision(self) -> ReviewDecision:
        """Require both revision fields, or neither for continuation; reject ambiguous decisions."""
        if self.action == "revise":
            if self.contract is None or self.plan is None:
                raise ValueError("A revision requires contract and plan")
        elif self.contract is not None or self.plan is not None:
            raise ValueError("Continue must not include contract or plan")
        return self


@dataclass(frozen=True)
class Node:
    """A validated operation over an already-bound dataset; order is topological."""

    name: str
    op: str
    source: str
    task: str = ""
    against_target: bool = False


def parse_plan(
    program: str,
    contract: Contract,
    *,
    input_handle: str = "sources",
    input_type: str = "sources",
    completed_names: set[str] | None = None,
    input_against_target: bool = False,
) -> list[Node]:
    """Compile a small AST whitelist into typed nodes; no Python objects are evaluated.

    Plan text is limited to 8,000 characters before parsing. Plans have a single terminal
    finalize, no unused datasets, rebinding, implicit fan-out or literals.
    input_handle/type bind the sole available collection; completed_names cannot be
    rebound. input_against_target retains historical comparison requirements for a
    groups input. Each Shuffle record belongs to one work set. Invalid plans raise ValueError.
    """
    if len(program) > 8000:
        raise ValueError("Plan exceeds 8000 characters")
    try:
        tree = ast.parse(program)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid plan syntax") from exc
    if not tree.body:
        raise ValueError("Plan must contain at least one operation")
    if input_type not in {"sources", "records", "groups", "files"}:
        raise ValueError(f"Invalid input type: {input_type}")
    handles = {input_handle: input_type}
    historical = {input_handle} if input_against_target else set()
    used: set[str] = set()
    nodes = []

    def reference(value: ast.AST, owner: str, allowed: set[str], field: str) -> str:
        """Resolve a direct reference or report its source line, field and allowed expressions."""
        if not (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == owner
            and value.attr in allowed
        ):
            expected = ", ".join(f"{owner}.{name}" for name in sorted(allowed))
            raise ValueError(
                f"plan line {value.lineno}, {field}: received {ast.unparse(value)}. "
                f"Expected one of: {expected}."
            )
        return value.attr

    for index, statement in enumerate(tree.body):
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            lhs = statement.targets[0]
            if not isinstance(lhs, ast.Name) or lhs.id.startswith("_"):
                raise ValueError("Dataset names must be plain identifiers")
            name = lhs.id
            call = statement.value
        elif isinstance(statement, ast.Expr):
            name, call = "result", statement.value
        else:
            raise ValueError("Only dataset assignments and a final p.finalize are allowed")
        if (
            name in handles
            or name in {"p", "contract", "target", "sources"}
            or name in (completed_names or ())
        ):
            raise ValueError(f"Rebinding forbidden: {name}")
        if not isinstance(call, ast.Call):
            raise ValueError("Expected pipeline call")
        op = reference(call.func, "p", {"map", "shuffle", "reduce", "finalize"}, "operator")
        if len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
            raise ValueError("An operator takes one dataset handle")
        source = call.args[0].id
        if source not in handles or source in used:
            raise ValueError(f"Unbound or multiply consumed dataset: {source}")
        kwargs = {k.arg: k.value for k in call.keywords}
        if len(kwargs) != len(call.keywords) or None in kwargs:
            raise ValueError("Duplicate or expanded keywords forbidden")
        task, against = "", False
        if op in {"map", "reduce"}:
            required = {"task"}
            if set(kwargs) != required:
                raise ValueError(
                    f"plan line {call.lineno}, node {name}: p.{op} accepts only the task keyword; "
                    f"missing={sorted(required - set(kwargs))}; unexpected={sorted(set(kwargs) - required)}. "
                    f"Received {ast.unparse(call)}."
                )
            task = reference(
                kwargs["task"], "contract", {"extract", "reduce", "synthesize"}, "task"
            )
            transform = getattr(contract, task)
            if transform is None:
                raise ValueError(f"Missing transform: {task}")
            if op == "reduce":
                valid = handles[source] == "groups"
                if source in historical and transform.output != "files":
                    raise ValueError("against=target requires a Reduce with output=files")
            else:
                valid = handles[source] in {"sources", "records"}
            output_type = transform.output
        elif op == "shuffle":
            if set(kwargs) not in ({"by"}, {"by", "against"}):
                raise ValueError("shuffle requires by and optional against=target")
            task = reference(kwargs["by"], "contract", {"routing", "final_routing"}, "by")
            if not getattr(contract, task):
                raise ValueError(f"Missing routing requirements: {task}")
            if "against" in kwargs:
                value = kwargs["against"]
                if not isinstance(value, ast.Name) or value.id != "target":
                    raise ValueError("Only the bound target can be searched")
                against = True
            valid, output_type = handles[source] == "records", "groups"
        else:
            into = kwargs.get("into")
            if set(kwargs) != {"into"} or not isinstance(into, ast.Name) or into.id != "target":
                raise ValueError("finalize requires into=target")
            valid, output_type = handles[source] == "files", "result"
            if index != len(tree.body) - 1:
                raise ValueError("finalize must be the final operation")
        if not valid or (isinstance(statement, ast.Expr) and op != "finalize"):
            raise ValueError(f"Invalid dataset type for {op}: {handles[source]}")
        used.add(source)
        handles[name] = output_type
        if against:
            historical.add(name)
        nodes.append(Node(name, op, source, task, against))
    if nodes[-1].op != "finalize" or set(handles) - used != {nodes[-1].name}:
        raise ValueError("Every dataset must reach finalize")
    return nodes
