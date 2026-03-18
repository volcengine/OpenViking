# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory relation store for OpenViking.

Provides typed relation tracking between memories (supersedes, contradicts,
related_to, derived_from) stored as lightweight documents in VikingDB.
Unlike resource-level relations managed by VikingFS, memory relations carry
a semantic type and are designed for conflict detection and retrieval filtering.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class RelationType(str, Enum):
    """Semantic relation type between two memories."""

    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    RELATED_TO = "related_to"
    DERIVED_FROM = "derived_from"


@dataclass
class MemoryRelation:
    """A typed edge between two memory URIs."""

    source_uri: str
    target_uri: str
    relation_type: RelationType
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    account_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "source_uri": self.source_uri,
            "target_uri": self.target_uri,
            "relation_type": self.relation_type.value,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }
        if self.account_id is not None:
            d["account_id"] = self.account_id
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryRelation":
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif not isinstance(created_at, datetime):
            created_at = datetime.now(timezone.utc)

        return cls(
            id=data.get("id", uuid4().hex),
            source_uri=data["source_uri"],
            target_uri=data["target_uri"],
            relation_type=RelationType(data["relation_type"]),
            created_at=created_at,
            metadata=data.get("metadata", {}),
            account_id=data.get("account_id"),
        )


class MemoryRelationStore:
    """
    In-memory relation store backed by a simple list.

    Relations are stored as MemoryRelation objects and can be queried by
    source URI, target URI, or relation type. This store is designed to
    be lightweight - a future iteration can persist to VikingDB's collection
    mechanism once the schema is validated.
    """

    def __init__(self) -> None:
        self._relations: List[MemoryRelation] = []

    async def create(self, relation: MemoryRelation) -> str:
        """Store a new relation. Returns the relation ID."""
        # Prevent exact duplicates (same source, target, type).
        for existing in self._relations:
            if (
                existing.source_uri == relation.source_uri
                and existing.target_uri == relation.target_uri
                and existing.relation_type == relation.relation_type
            ):
                logger.debug(
                    "Duplicate relation skipped: %s --%s--> %s",
                    relation.source_uri,
                    relation.relation_type.value,
                    relation.target_uri,
                )
                return existing.id

        self._relations.append(relation)
        logger.debug(
            "Created relation %s: %s --%s--> %s",
            relation.id,
            relation.source_uri,
            relation.relation_type.value,
            relation.target_uri,
        )
        return relation.id

    async def query(
        self,
        uri: str,
        relation_type: Optional[RelationType] = None,
        direction: str = "outgoing",
    ) -> List[MemoryRelation]:
        """Query relations for a memory URI.

        Args:
            uri: The memory URI to query.
            relation_type: Filter by relation type (optional).
            direction: "outgoing" (uri is source), "incoming" (uri is target),
                       or "both".

        Returns:
            List of matching MemoryRelation objects.
        """
        results: List[MemoryRelation] = []
        for rel in self._relations:
            match = False
            if direction in ("outgoing", "both") and rel.source_uri == uri:
                match = True
            if direction in ("incoming", "both") and rel.target_uri == uri:
                match = True
            if match and relation_type is not None and rel.relation_type != relation_type:
                match = False
            if match:
                results.append(rel)
        return results

    async def delete(self, relation_id: str) -> bool:
        """Delete a relation by ID. Returns True if found and deleted."""
        for i, rel in enumerate(self._relations):
            if rel.id == relation_id:
                self._relations.pop(i)
                logger.debug("Deleted relation %s", relation_id)
                return True
        return False

    async def delete_by_uri(self, uri: str) -> int:
        """Delete all relations involving a URI (as source or target).

        Returns the number of relations deleted.
        """
        before = len(self._relations)
        self._relations = [
            r for r in self._relations if r.source_uri != uri and r.target_uri != uri
        ]
        deleted = before - len(self._relations)
        if deleted:
            logger.debug("Deleted %d relations for URI %s", deleted, uri)
        return deleted

    async def get_superseded_uris(self, uri: str) -> List[str]:
        """Get URIs that the given URI supersedes (outgoing supersedes edges).

        Useful during retrieval to filter out stale memories.
        """
        return [
            r.target_uri
            for r in self._relations
            if r.source_uri == uri and r.relation_type == RelationType.SUPERSEDES
        ]

    async def is_superseded(self, uri: str) -> bool:
        """Check if a URI has been superseded by a newer memory."""
        return any(
            r.target_uri == uri and r.relation_type == RelationType.SUPERSEDES
            for r in self._relations
        )

    def count(self) -> int:
        """Return total number of stored relations."""
        return len(self._relations)
