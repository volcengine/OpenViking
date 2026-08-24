# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticMsg: Semantic extraction queue message dataclass."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openviking.utils.ingest_options import IngestOptions


def build_semantic_coalesce_key(
    *,
    context_type: str,
    uri: str,
    account_id: str = "default",
    user_id: str = "default",
    peer_id: str = "default",
) -> str:
    return "|".join([context_type, account_id, user_id, peer_id, uri.rstrip("/")])


@dataclass
class SemanticMsg:
    """Semantic extraction queue message.

    Attributes:
        id: Unique identifier (UUID)
        uri: Directory URI to process
        context_type: Type of context (resource, memory, skill, session)
        status: Processing status (pending/processing/completed)
        timestamp: Creation timestamp
        recursive: Whether to recursively process subdirectories.
                   When True, the processor will collect all subdirectory info and
                   enqueue them for processing (bottom-up order).
                   When False, only the specified directory will be processed.
        use_hierarchical_aggregation: Route memory directories through the
                   shared directory DAG instead of the specialized flat memory
                   update path.
        propagate_to_parent: Whether a completed directory refresh may enqueue
                   a freshness refresh for its parent.
    """

    id: str  # UUID
    uri: str  # Directory URI
    context_type: str  # resource, memory, skill, session
    status: str = "pending"  # pending/processing/completed
    timestamp: int = int(datetime.now().timestamp())
    recursive: bool = True  # Whether to recursively process subdirectories
    account_id: str = "default"
    user_id: str = "default"
    peer_id: str = "default"
    role: str = "root"
    # Additional flags
    skip_vectorization: bool = False
    telemetry_id: str = ""
    target_uri: str = ""
    lock_handoff: Optional[Dict[str, Any]] = None
    is_code_repo: bool = False
    target_preexisting: Optional[bool] = None
    ingest_options: IngestOptions = field(default_factory=IngestOptions)
    coalesce_key: str = ""
    coalesce_version: int = 0
    changes: Optional[Dict[str, List[str]]] = (
        None  # {"added": [...], "modified": [...], "deleted": [...]}
    )
    source: Optional[Dict[str, str]] = None
    generation_trigger: str = "semantic_refresh"
    aggregate_directory: bool = True
    use_hierarchical_aggregation: bool = False
    propagate_to_parent: bool = True

    def __init__(
        self,
        uri: str,
        context_type: str,
        recursive: bool = True,
        account_id: str = "default",
        user_id: str = "default",
        peer_id: str = "default",
        role: str = "root",
        skip_vectorization: bool = False,
        telemetry_id: str = "",
        target_uri: str = "",
        lock_handoff: Optional[Dict[str, Any]] = None,
        is_code_repo: bool = False,
        target_preexisting: Optional[bool] = None,
        ingest_options: IngestOptions | Dict[str, Any] | None = None,
        coalesce_key: str = "",
        coalesce_version: int = 0,
        changes: Optional[Dict[str, List[str]]] = None,
        source: Optional[Dict[str, str]] = None,
        generation_trigger: str = "semantic_refresh",
        aggregate_directory: bool = True,
        use_hierarchical_aggregation: bool = False,
        propagate_to_parent: bool = True,
    ):
        self.id = str(uuid4())
        self.uri = uri
        self.context_type = context_type
        self.recursive = recursive
        self.account_id = account_id
        self.user_id = user_id
        self.peer_id = peer_id
        self.role = role
        self.skip_vectorization = skip_vectorization
        self.telemetry_id = telemetry_id
        self.target_uri = target_uri
        self.lock_handoff = lock_handoff
        self.is_code_repo = is_code_repo
        self.target_preexisting = target_preexisting
        self.ingest_options = IngestOptions.from_value(ingest_options)
        self.coalesce_key = coalesce_key
        self.coalesce_version = coalesce_version
        self.changes = changes
        self.source = dict(source) if source else None
        self.generation_trigger = generation_trigger
        self.aggregate_directory = bool(aggregate_directory)
        self.use_hierarchical_aggregation = bool(use_hierarchical_aggregation)
        self.propagate_to_parent = bool(propagate_to_parent)

    def to_dict(self) -> Dict[str, Any]:
        """Convert object to dictionary."""
        data = asdict(self)
        data["ingest_options"] = self.ingest_options.to_dict()
        return data

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
            account_id=data.get("account_id", "default"),
            user_id=data.get("user_id", "default"),
            peer_id=data.get("peer_id", "default"),
            role=data.get("role", "root"),
            skip_vectorization=data.get("skip_vectorization", False),
            telemetry_id=data.get("telemetry_id", ""),
            target_uri=data.get("target_uri", ""),
            lock_handoff=data.get("lock_handoff"),
            is_code_repo=data.get("is_code_repo", False),
            target_preexisting=data.get("target_preexisting"),
            ingest_options=(
                data.get("ingest_options")
                or {
                    "search_tags": data.get("search_tags"),
                    "search_tag_mode": data.get("search_tag_mode", "replace"),
                }
            ),
            coalesce_key=data.get("coalesce_key", ""),
            coalesce_version=data.get("coalesce_version", 0),
            changes=data.get("changes"),
            source=data.get("source"),
            generation_trigger=data.get("generation_trigger", "semantic_refresh"),
            aggregate_directory=data.get("aggregate_directory", True),
            use_hierarchical_aggregation=data.get("use_hierarchical_aggregation", False),
            propagate_to_parent=data.get("propagate_to_parent", True),
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
