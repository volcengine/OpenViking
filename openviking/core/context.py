# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unified context class for OpenViking."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openviking.utils.time_utils import format_iso8601
from openviking_cli.session.user_id import UserIdentifier


class ResourceContentType(str, Enum):
    """Resource content type"""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    BINARY = "binary"


class ContextType(str, Enum):
    """Context type"""

    SKILL = "skill"
    MEMORY = "memory"
    RESOURCE = "resource"


class Vectorize:
    text: str = ""
    # image: str = ""
    # video: str = ""
    # audio: str = ""

    def __init__(self, text: str = ""):
        self.text = text


class Context:
    """
    Unified context class for all context types in OpenViking.
    """

    def __init__(
        self,
        uri: str,
        parent_uri: Optional[str] = None,
        is_leaf: bool = False,
        abstract: str = "",
        context_type: Optional[str] = None,
        category: Optional[str] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        active_count: int = 0,
        related_uri: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user: Optional[UserIdentifier] = None,
        id: Optional[str] = None,
    ):
        """
        Initialize a Context object.
        """
        self.id = id or str(uuid4())
        self.uri = uri
        self.parent_uri = parent_uri
        self.is_leaf = is_leaf
        self.abstract = abstract
        self.context_type = context_type or self._derive_context_type()
        self.category = category or self._derive_category()
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or self.created_at
        self.active_count = active_count
        self.related_uri = related_uri or []
        self.meta = meta or {}
        self.session_id = session_id
        self.user = user
        self.vector: Optional[List[float]] = None
        self.vectorize = Vectorize(abstract)

    def _derive_context_type(self) -> str:
        """Derive context type from URI prefix."""
        if self.uri.startswith("viking://agent/skills"):
            return "skill"
        elif "memories" in self.uri:
            return "memory"
        else:
            return "resource"

    def _derive_category(self) -> str:
        """Derive category from URI prefix."""
        if self.uri.startswith("viking://agent/memories"):
            if "patterns" in self.uri:
                return "patterns"
            elif "cases" in self.uri:
                return "cases"
        elif self.uri.startswith("viking://user/memories"):
            if "profile" in self.uri:
                return "profile"
            if "preferences" in self.uri:
                return "preferences"
            if "entities" in self.uri:
                return "entities"
            elif "events" in self.uri:
                return "events"
        return ""

    def get_context_type(self) -> str:
        """Get the type of this context (alias for context_type)."""
        return self.context_type

    def set_vectorize(self, vectorize: Vectorize):
        self.vectorize = vectorize

    def get_vectorization_text(self) -> str:
        """Get text for vectorization."""
        # todo: multi-modal support
        return self.vectorize.text

    def update_activity(self):
        """Update activity statistics."""
        self.active_count += 1
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary format for storage."""
        created_at_str = format_iso8601(self.created_at) if self.created_at else None
        updated_at_str = format_iso8601(self.updated_at) if self.updated_at else None

        data = {
            "id": self.id,
            "uri": self.uri,
            "parent_uri": self.parent_uri,
            "is_leaf": self.is_leaf,
            "abstract": self.abstract,
            "context_type": self.context_type,
            "category": self.category,
            "created_at": created_at_str,
            "updated_at": updated_at_str,
            "active_count": self.active_count,
            "vector": self.vector,
            "meta": self.meta,
            "related_uri": self.related_uri,
            "session_id": self.session_id,
        }

        if self.user:
            data["user"] = self.user.to_dict()

        # Add skill-specific fields from meta
        if self.context_type == "skill":
            data["name"] = self.meta.get("name", "")
            data["description"] = self.meta.get("description", "")

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Context":
        """Create a context object from dictionary."""
        obj = cls(
            uri=data["uri"],
            parent_uri=data.get("parent_uri"),
            is_leaf=data.get("is_leaf", False),
            abstract=data.get("abstract", ""),
            context_type=data.get("context_type"),
            category=data.get("category"),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if isinstance(data.get("created_at"), str)
                else data.get("created_at")
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"])
                if isinstance(data.get("updated_at"), str)
                else data.get("updated_at")
            ),
            active_count=data.get("active_count", 0),
            related_uri=data.get("related_uri", []),
            meta=data.get("meta", {}),
            session_id=data.get("session_id"),
            user=data.get("user"),
        )
        obj.id = data.get("id", obj.id)
        obj.vector = data.get("vector")
        return obj
