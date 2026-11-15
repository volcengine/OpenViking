"""Validated stage selection for a Markdown file pipeline; DSL text is never executed."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PLANNING_RULES = """## Available stages and plan syntax
{input_binding}
Map reads assigned source ranges and writes Markdown drafts following the Skill.
Use input_unit=file when a document's ranges must stay together; otherwise use range.
Shuffle groups Markdown drafts by their contents for joint processing by Reduce.
Reduce merges each group into one or more Markdown files, preserving unique facts,
source citations, conditions, conflicting claims and applicability differences.
Finalize publishes the selected files and must end the plan.

Choose only necessary stages. Independent outputs need Map then Finalize.
Cross-source consolidation needs Map, Shuffle, Reduce, then Finalize.
Each operator can appear at most once. Each intermediate result has a new name
and is consumed exactly once. Do not use imports, loops or other Python calls.

Examples:
files = p.map(sources, task=contract.extract)
p.finalize(files, into=target)

or:
drafts = p.map(sources, task=contract.extract)
groups = p.shuffle(drafts, by=contract.routing)
files = p.reduce(groups, task=contract.reduce)
p.finalize(files, into=target)

Shuffle may add against=target when the target has content and relevant existing
files must be integrated. Similarity requests joint processing, not mandatory merging.
Map and Reduce always use file tools and produce Markdown.
Define reduce and routing only when used. Stage instructions supplement the Skill.
"""


class PlanModel(BaseModel):
    """Reject undeclared planning options instead of silently ignoring stage mistakes."""

    model_config = ConfigDict(extra="forbid")


class Transform(PlanModel):
    """Markdown work instructions; input_unit controls source assignment granularity."""

    instructions: str = Field(min_length=1)
    input_unit: Literal["range", "file"] = "range"


class Routing(PlanModel):
    """Semantic grouping criteria, or one collection for a task requiring global synthesis."""

    mode: Literal["semantic", "all"] = "semantic"
    instructions: str = ""

    @model_validator(mode="after")
    def check_instructions(self):
        """Require explicit grouping criteria for semantic routing."""
        if self.mode == "semantic" and not self.instructions.strip():
            raise ValueError("Semantic routing requires instructions")
        return self


class Contract(PlanModel):
    """Stage-specific instructions; the original Skill remains authoritative."""

    extract: Transform
    reduce: Transform | None = None
    routing: Routing | None = None
    output_format: Literal["wiki", "files"] = "files"


class PlanProposal(PlanModel):
    """Explicit stage sequence and the work definitions referenced by it."""

    contract: Contract
    plan: str = Field(min_length=1, max_length=8000)


class ReviewDecision(PlanModel):
    """An advisory replacement of only the remaining stages and their instructions."""

    action: Literal["continue", "revise"]
    reason: str = Field(default="", max_length=600)
    contract: Contract | None = None
    plan: str | None = Field(default=None, min_length=1, max_length=8000)

    @model_validator(mode="after")
    def check_revision(self):
        """Require a complete replacement for revise and no replacement for continue."""
        if (self.action == "revise") != (self.contract is not None and self.plan is not None):
            raise ValueError("Revise requires contract and plan")
        if self.action == "continue" and (self.contract is not None or self.plan is not None):
            raise ValueError("Continue must not include a replacement")
        return self


@dataclass(frozen=True)
class Node:
    """A validated operation consuming a single bound collection."""

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
    completed_ops: set[str] | None = None,
) -> list[Node]:
    """Compile a small AST whitelist into typed nodes; no Python objects are evaluated.

    Plan text is limited to 8,000 characters before parsing. Plans have a single terminal
    finalize, no unused datasets, rebinding, implicit fan-out or literals.
    input_handle/type bind the sole available collection; completed_names cannot be
    rebound. completed_ops prevents reuse of executed operators. Reduce produces files
    consumed by Finalize. Each Markdown draft belongs to one work set. Invalid plans
    raise ValueError.
    """
    if len(program) > 8000:
        raise ValueError("Plan exceeds 8000 characters")
    try:
        tree = ast.parse(program)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid plan syntax") from exc
    if not tree.body:
        raise ValueError("Plan must contain at least one operation")
    if input_type not in {"sources", "groups", "files"}:
        raise ValueError(f"Invalid input type: {input_type}")
    handles = {input_handle: input_type}
    used: set[str] = set()
    used_ops = set(completed_ops or ())
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
        if op in used_ops:
            raise ValueError(f"Operator may appear at most once: {op}")
        used_ops.add(op)
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
            task = reference(kwargs["task"], "contract", {"extract", "reduce"}, "task")
            transform = getattr(contract, task)
            if transform is None:
                raise ValueError(f"Missing transform: {task}")
            if op == "reduce":
                valid = handles[source] == "groups"
            else:
                valid = handles[source] == "sources"
            output_type = "files"
        elif op == "shuffle":
            if set(kwargs) not in ({"by"}, {"by", "against"}):
                raise ValueError("shuffle requires by and optional against=target")
            task = reference(kwargs["by"], "contract", {"routing"}, "by")
            if not getattr(contract, task):
                raise ValueError(f"Missing routing requirements: {task}")
            if "against" in kwargs:
                value = kwargs["against"]
                if not isinstance(value, ast.Name) or value.id != "target":
                    raise ValueError("Only the bound target can be searched")
                against = True
            valid, output_type = handles[source] == "files", "groups"
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
        nodes.append(Node(name, op, source, task, against))
    if nodes[-1].op != "finalize" or set(handles) - used != {nodes[-1].name}:
        raise ValueError("Every dataset must reach finalize")
    return nodes
