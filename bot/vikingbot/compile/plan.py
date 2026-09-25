"""Typed, collection-level Compile plans. Python syntax is parsed, never executed."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

PROCESSING_VERSION = "compile-pipeline-33"
# Explicit output-token fallback when the configured VLM provides no value.
DEFAULT_MAX_TOKENS = 32_000

# Common tasks share one collection flow; explicit plans still pass the AST whitelist.
DEFAULT_PLAN = (
    "records = p.map(sources, task=contract.extract)\n"
    "groups = p.shuffle(records, by=contract.routing)\n"
    "changes = p.reduce(groups, task=contract.reduce)\n"
    "p.finalize(changes, into=target)"
)


def digest(value: Any) -> str:
    """Hash JSON-compatible task inputs deterministically, including their versions."""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def content_hash(value: str | bytes) -> str:
    """Return the byte hash used by conditional content writes."""
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
        default="direct",
        description="direct: model calls reading assigned evidence and Skill attachments. "
        "agent: also uses scratch files and Skill scripts.",
    )
    input_unit: Literal["range", "file"] = Field(
        default="range",
        description="When Map reads source files: file keeps each file's text ranges together; "
        "range permits separate or batched ranges. Map over records handles each record separately.",
    )
    fields: dict[str, str] = Field(
        default_factory=lambda: {"text": "Facts extracted from the source."},
        description='Record content field names mapped to instructions for producing their values; '
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
        description='Record applicability field names mapped to extraction instructions, not actual values; '
        'e.g. {"version": "Product version these facts apply to"}. These are not exact-match grouping keys.',
    )
    preserve: list[str] = Field(
        default_factory=list,
        description="Extra requirements shared with Map/Reduce, e.g. details to preserve. "
        "Omit if covered by the Skill or user instruction.",
    )
    output_format: Literal["wiki", "files"] = Field(
        default="files",
        description="Use files for ordinary text or Markdown outputs. Choose wiki when the task "
        "requires OpenViking Knowledge Format (OKF) wiki pages with its page metadata and link rules.",
    )
    required_paths: list[str] = Field(
        default_factory=list,
        description="Prescribed output paths relative to request.to; no wildcards or guessed filenames. "
        "Omit if none are required.",
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


@dataclass(frozen=True)
class Node:
    """A validated operation over an already-bound dataset; order is topological."""

    name: str
    op: str
    source: str
    task: str = ""
    against_target: bool = False


def parse_plan(program: str, contract: Contract) -> list[Node]:
    """Compile a small AST whitelist into typed nodes; no Python objects are evaluated.

    Plan text is limited to 8,000 characters before parsing. Plans have a single terminal
    finalize, no unused datasets, rebinding, implicit fan-out or literals.
    Each Shuffle record belongs to one work set. Invalid plans raise ValueError.
    """
    if len(program) > 8000:
        raise ValueError("Plan exceeds 8000 characters")
    try:
        tree = ast.parse(program)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid plan syntax") from exc
    if not tree.body:
        raise ValueError("Plan must contain at least one operation")
    handles = {"sources": "sources"}
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
        if name in handles or name in {"p", "contract", "target", "sources"}:
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
        nodes.append(Node(name, op, source, task, against))
    if nodes[-1].op != "finalize" or set(handles) - used != {nodes[-1].name}:
        raise ValueError("Every dataset must reach finalize")
    return nodes


class EvidenceSpan(StrictModel):
    """Original evidence location; line numbers are one-based, inclusive and shard-local."""

    source_range: str
    start_line: int = Field(ge=1, strict=True)
    end_line: int = Field(ge=1, strict=True)


class RecordDraft(StrictModel):
    """Model payload with local input references; runtime assigns identity and evidence."""

    inputs: list[str] = Field(min_length=1)
    # Optional reading hints; omitted ranges retain full-shard evidence access.
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    # JSON preserves nested facts and relations without prescribing their business shape.
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    routing_text: str = Field(
        default="",
        max_length=600,
        description="Short semantic routing description.",
    )
    scope: dict[str, str] = Field(default_factory=dict)
    # A path without content is only a hint, never a finished or publishable file.
    ready_content: str | None = None
    ready_path: str | None = None
    ready_content_ref: str | None = Field(
        default=None, description="Agent scratch file alternative to inline ready_content."
    )
    # An explicit URI present in supplied evidence, used as a candidate before search.
    target_uri: str | None = None

    @model_validator(mode="before")
    @classmethod
    def move_business_fields(cls, value):
        """Move extra record fields into payload without mutating the supplied record.

        Equal duplicates collapse; conflicting values require model repair. Invalid
        payload types and missing structural fields remain subject to normal validation.
        """
        if not isinstance(value, dict) or not isinstance(value.get("payload", {}), dict):
            return value
        extra = value.keys() - cls.model_fields.keys()
        if not extra:
            return value
        record, payload = dict(value), dict(value.get("payload", {}))
        for name in extra:
            if name in payload and payload[name] != record[name]:
                raise ValueError(f"Conflicting values for payload field: {name}")
            payload[name] = record.pop(name)
        return {**record, "payload": payload}

    @field_validator("payload", mode="before")
    @classmethod
    def strip_reserved_fields(cls, value):
        """Drop top-level payload keys owned by the record without changing outer values.

        Nested business data is preserved; invalid payload types still fail validation.
        """
        if isinstance(value, dict):
            reserved = cls.model_fields.keys() - {"payload"}
            return {key: item for key, item in value.items() if key not in reserved}
        return value


class InputReferenceError(ValueError):
    """Input references or dispositions do not match the assigned evidence."""


class MissingReadyPathError(ValueError):
    """Finished content lacks the relative path required for publication."""


class RecordResponse(StrictModel):
    records: list[RecordDraft] = Field(default_factory=list, max_length=64)


class CombinedContent(StrictModel):
    """Intermediate evidence for Reduce; inputs identify its supporting batch materials."""

    inputs: list[str] = Field(min_length=1)
    content: str = Field(min_length=1)


class CombineResponse(StrictModel):
    """Consolidated batch contents, with provenance maintained by the runtime."""

    records: list[CombinedContent] = Field(min_length=1, max_length=64)


def result_schema(schema, data):
    """Expose assignment fields and routing identities in direct and child tool schemas."""
    # The model receives strict routing instructions; malformed individual decisions
    # remain available to Shuffle so valid neighbours survive a partial response.
    result = (RouteResponse if schema is RouteBatchResponse else schema).model_json_schema()
    if schema is PlanProposal:
        # Planner-facing fields carry their meaning; class docstrings and generated titles do not.
        for definition in [result, *result["$defs"].values()]:
            definition.pop("title", None)
            definition.pop("description", None)
            for property_schema in definition.get("properties", {}).values():
                property_schema.pop("title", None)
        # Explicit plans are required from the model; stored contracts retain parsing defaults.
        result["properties"]["plan"].pop("default")
        result["required"] = ["contract", "plan"]
        transform = result["$defs"]["Transform"]
        transform["properties"]["output"].pop("default")
        transform["required"] = ["instructions", "output"]
        properties = result["$defs"]["Contract"]["properties"]
        # Shared requirements remain readable from saved contracts; planners use stage instructions.
        properties.pop("preserve")
        # Model output uses objects for referenced configurations, omitting unused optional entries.
        for name, definition in (
            ("reduce", "Transform"),
            ("synthesize", "Transform"),
            ("routing", "Routing"),
            ("final_routing", "Routing"),
        ):
            properties[name] = {
                "$ref": f"#/$defs/{definition}",
                "description": properties[name]["description"],
            }
    if schema in (RouteResponse, RouteBatchResponse):
        ids = [item["record"] for item in data["records"]]
        result["properties"]["decisions"].update(minItems=len(ids), maxItems=len(ids))
        result["$defs"]["RouteDecision"]["properties"]["record"]["enum"] = ids
    if schema is FileResponse and data.get("inputs"):
        # Limit file lineage to supplied inputs without requiring exhaustive coverage.
        ids = [item["id"] for item in data["inputs"]]
        inputs = result["$defs"]["FileDraft"]["properties"]["inputs"]
        inputs["items"]["enum"] = ids
        inputs["uniqueItems"] = True
    if schema is FileResponse and "indexes" in data:
        paths = [item["group"]["path"] for item in data["indexes"]]
        result["properties"]["files"].update(minItems=len(paths), maxItems=len(paths))
        result["$defs"]["FileDraft"]["properties"]["path"]["enum"] = paths
    if schema is RecordResponse and "record_fields" in data:
        record = result["$defs"]["RecordDraft"]
        properties = record["properties"]
        record["required"] = ["inputs", "scope"]
        for name, fields in (("payload", data["record_fields"]), ("scope", data["scope_fields"])):
            value = properties[name]["additionalProperties"]
            if name == "scope":
                value = {"type": "string"}
            properties[name] = {
                "type": "object",
                "properties": {
                    field: {**value, "description": fields[field]}
                    if isinstance(fields, dict)
                    else dict(value)
                    for field in fields
                },
                "additionalProperties": value,
            }
    return result


class RouteDecision(StrictModel):
    """Candidate links for joint processing, without asserting identity or output paths.

    related names only this primary record's candidates for joint consideration.
    history names only this primary record's recalled URIs, not mandatory updates.
    Empty lists retain the record as an independent work set.
    """

    record: str
    related: list[str] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)


class RouteResponse(StrictModel):
    decisions: list[RouteDecision] = Field(
        description="Exactly one decision per supplied record ID, with no duplicates. "
        "Links request joint processing; Reduce decides the number and paths of output files.",
    )


class RouteBatchResponse(StrictModel):
    """Unvalidated routing entries; Shuffle validates and retries each primary independently.

    Only the envelope is parsed here. Individual entries may be malformed and must
    never enter the accepted-route store or the model's validated-response cache.
    """

    decisions: list[Any]


class Patch(StrictModel):
    """Exact, unique old-text anchor and its replacement, bound to an old-file hash."""

    old: str = Field(
        min_length=1,
        description="Exact text occurring once in the supplied old content; anchors must not overlap.",
    )
    new: str = Field(description="Replacement text for this anchor; empty text deletes it.")


class FileDraft(StrictModel):
    """An inline edit or a child-local file reference resolved before semantic validation.

    content_ref is relative to the submitting child's scratch directory. Runtime
    records and verifies content_sha256; callers may supply it to assert a version.
    """

    path: str = Field(description="Destination path relative to the compile target.")
    content: str | None = Field(
        default=None,
        description="Complete file content. Omit when using content_ref or patches.",
    )
    content_ref: str | None = Field(
        default=None,
        description="Existing file to read, relative to your scratch root. "
        "Use this after write_file; omit inline content.",
    )
    content_sha256: str | None = None
    patches: list[Patch] = Field(
        default_factory=list,
        description="Edits to supplied historical content; do not combine with content or content_ref.",
    )
    base_hash: str | None = Field(
        default=None,
        description="Copy the supplied historical file hash when updating; omit for new files.",
    )
    inputs: list[str] = Field(
        min_length=1,
        description="Unique supplied input IDs actually used by this file; multiple inputs may support "
        "one file, and one input may support multiple files.",
    )


class FileResponse(StrictModel):
    """Final files with explicit lineage; unreferenced inputs have unconfirmed coverage."""

    files: list[FileDraft] = Field(default_factory=list)


@dataclass
class Record:
    """A dataset item carrying small routing metadata and references to exact evidence.

    source_refs are runtime-assigned source-range IDs, deduplicated across levels.
    payload_ref addresses one task shard; neither payloads nor vectors enter plans.
    parents records immediate lineage for final-state accounting.
    """

    record_id: str
    payload_ref: str
    source_refs: list[str]
    routing_text: str
    scope: dict[str, str]
    parents: list[str]
    ready_ref: str | None = None
    schema: str = "source-range"
    target_uri: str | None = None


@dataclass
class Group:
    """Evidence to process together; one work set may produce several independent files."""

    group_id: str
    records: list[Record]
    # Authorized recalled files available for comparison and version-checked updates.
    target_uris: list[str] = field(default_factory=list)
