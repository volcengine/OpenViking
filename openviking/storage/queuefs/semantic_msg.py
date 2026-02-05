# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""SemanticMsg: Semantic extraction queue message dataclass."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict
from uuid import uuid4


@dataclass
class SemanticMsg:
    """Semantic extraction queue message.

    Attributes:
        id: Unique identifier (UUID)
        uri: Directory URI to process
        context_type: Type of context (resource, memory, skill)
        status: Processing status (pending/processing/completed)
        timestamp: Creation timestamp
        recursive: Whether to recursively process subdirectories.
                   When True, the processor will collect all subdirectory info and
                   enqueue them for processing (bottom-up order).
                   When False, only the specified directory will be processed.
    """

    id: str  # UUID
    uri: str  # Directory URI
    context_type: str  # resource, memory, skill
    status: str = "pending"  # pending/processing/completed
    timestamp: int = int(datetime.now().timestamp())
    recursive: bool = True  # Whether to recursively process subdirectories

    def __init__(
        self,
        uri: str,
        context_type: str,
        recursive: bool = True,
    ):
        self.id = str(uuid4())
        self.uri = uri
        self.context_type = context_type
        self.recursive = recursive

    def to_dict(self) -> Dict[str, Any]:
        """Convert object to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert object to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticMsg":
        """Safely create object from dictionary, filtering extra fields and handling missing fields."""
        if not data:
            raise ValueError("Data dictionary is empty")

        uri = data.get("uri")
        context_type = data.get("context_type")

        if not uri or not context_type:
            missing = []
            if not uri:
                missing.append("uri")
            if not context_type:
                missing.append("context_type")
            raise ValueError(f"Missing required fields: {missing}")

        obj = cls(
            uri=uri,
            context_type=context_type,
            recursive=data.get("recursive", True),
        )
        if "id" in data and data["id"]:
            obj.id = data["id"]
        if "status" in data:
            obj.status = data["status"]
        if "timestamp" in data:
            obj.timestamp = data["timestamp"]
        return obj

    @classmethod
    def from_json(cls, json_str: str) -> "SemanticMsg":
        """Create object from JSON string."""
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")
