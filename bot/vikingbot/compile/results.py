"""Runtime-owned source and Markdown identities, independent of model submission schemas."""

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class Source:
    """An immutable prepared input range; source_id also addresses its evidence snapshot."""

    source_id: str
    metadata: dict


@dataclass
class Document:
    """A completed Markdown file in a private workspace with assignment-level provenance.

    path is relative to the publication root; workspace_path identifies its actual bytes.
    source_refs tracks assigned source ranges, not proof of semantic coverage.
    """

    path: str
    workspace_path: str
    source_refs: list[str] = field(default_factory=list)


@dataclass
class Group:
    """Markdown drafts processed together, plus selected existing target files."""

    group_id: str
    documents: list[Document]
    target_uris: list[str] = field(default_factory=list)


class RouteDecision(BaseModel):
    """Candidate links selected for one draft; history stays inside recalled target files."""

    model_config = ConfigDict(extra="forbid")
    document: str
    related: list[str] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)


class RouteResponse(BaseModel):
    """Exactly one routing decision per assigned draft, validated against its candidates."""

    model_config = ConfigDict(extra="forbid")
    decisions: list[RouteDecision]
