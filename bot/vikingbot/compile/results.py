"""Execution results, evidence references and runtime datasets for Compile operators."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(StrictModel):
    """A supporting passage, with inclusive line numbers within one source text segment."""

    source_range: str = Field(
        description="ID of the supporting source text segment. For source inputs, use the input's "
        "id; for earlier results, use an ID from their payload.source_ranges."
    )
    start_line: int = Field(
        ge=1,
        strict=True,
        description="First included line, numbered from 1 within the referenced text segment.",
    )
    end_line: int = Field(
        ge=1,
        strict=True,
        description="Last included line in the same text segment; must be at least start_line.",
    )


class RecordDraft(StrictModel):
    """One structured result carrying content and references to its supporting inputs."""

    inputs: list[str] = Field(
        min_length=1,
        description="IDs from inputs[].id supporting this record's content or finished file. "
        "Include only IDs supplied in this assignment.",
    )
    # Optional reading hints; omitted ranges retain full-shard evidence access.
    evidence_spans: list[EvidenceSpan] = Field(
        default_factory=list,
        description="Optional locations of supporting passages in the original text. Include relevant conditions, exceptions, "
        "headings and table headers/notes; omit uncertain locations.",
    )
    # JSON preserves nested facts and relations without prescribing their business shape.
    payload: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Content requested by record_fields; values may be text or structured JSON. "
        "Record-level inputs, scope, routing_text and ready_* fields are siblings of payload.",
    )
    routing_text: str = Field(
        default="",
        max_length=600,
        description="Short description of subject, content and applicability for later grouping; "
        "do not rely only on a title.",
    )
    scope: dict[str, str] = Field(
        default_factory=dict,
        description="Evidence-supported applicability conditions, such as version or region, "
        "guided by scope_fields. Leave empty when none are established. Source filenames, "
        "coverage notes and observations belong in payload.",
    )
    # A path without content is only a hint, never a finished or publishable file.
    ready_content: str | None = Field(
        default=None,
        description="Full text of a file completed from the assigned inputs. Provide ready_path "
        "beside this field. Omit if more inputs are needed or ready_content_ref is used.",
    )
    # Runtime byte snapshots survive JSON caching without entering model tool schemas.
    ready_content_base64: SkipJsonSchema[str | None] = None
    ready_path: str | None = Field(
        default=None,
        description="Nonempty destination file path relative to the requested output directory. "
        "Do not add a literal to/ prefix. Required with ready_content or ready_content_ref; "
        "a path alone does not submit a finished file.",
    )
    ready_content_ref: str | None = Field(
        default=None,
        description="Alternative to ready_content when file tools are available: the relative "
        "path of the completed file, or a supplied ready_file.content_ref. Provide ready_path for its final "
        "destination and omit ready_content.",
    )
    # An explicit URI present in supplied evidence, used as a candidate before search.
    target_uri: str | None = Field(
        default=None,
        description="URI of an existing file inside the output directory, explicitly named "
        "in the supporting inputs. Omit if no such file is identified.",
    )

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
    """Structured results passed to later processing steps."""

    records: list[RecordDraft] = Field(default_factory=list, max_length=64)


class CombinedContent(StrictModel):
    """Intermediate evidence for Reduce; inputs identify its supporting batch materials."""

    inputs: list[str] = Field(min_length=1)
    content: str = Field(min_length=1)


class CombineResponse(StrictModel):
    """Consolidated batch contents, with provenance maintained by the runtime."""

    records: list[CombinedContent] = Field(min_length=1, max_length=64)


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
    """One output file, submitted as full text, a file reference or edits to existing text.

    A content_ref names a private scratch file or a supplied immutable snapshot.
    content_sha256 optionally asserts the submitted file's complete byte hash.
    """

    path: str = Field(
        description="Nonempty destination file path relative to the requested output directory, "
        "without a literal to/ prefix."
    )
    content: str | None = Field(
        default=None,
        description="Complete file content. Omit when using content_ref or patches.",
    )
    # Binary bytes are populated from validated references, not generated by the model.
    content_base64: SkipJsonSchema[str | None] = None
    content_ref: str | None = Field(
        default=None,
        description="Alternative to content when file tools are available: the relative path "
        "of the completed file, or reuse a supplied content_ref unchanged. Set path to its final destination; "
        "omit content and patches.",
    )
    content_sha256: str | None = Field(
        default=None,
        description="Optional SHA-256 hash of the complete file content; omit if unknown.",
    )
    patches: list[Patch] = Field(
        default_factory=list,
        description="Edits to supplied historical content; do not combine with content or content_ref.",
    )
    base_hash: str | None = Field(
        default=None,
        description="When editing a historical_files entry, copy its base_hash. Omit for new files.",
    )
    inputs: list[str] = Field(
        min_length=1,
        description="Unique supplied input IDs actually used by this file; multiple inputs may support "
        "one file, and one input may support multiple files.",
    )


class FileResponse(StrictModel):
    """Output files with references to the inputs supporting their content."""

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
