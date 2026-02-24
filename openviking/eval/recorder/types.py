# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
IO Recorder types for OpenViking evaluation.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class IOType(Enum):
    """IO operation type."""

    FS = "fs"
    VIKINGDB = "vikingdb"


class FSOperation(Enum):
    """File system operations."""

    READ = "read"
    WRITE = "write"
    LS = "ls"
    STAT = "stat"
    MKDIR = "mkdir"
    RM = "rm"
    MV = "mv"
    GREP = "grep"
    TREE = "tree"
    GLOB = "glob"


class VikingDBOperation(Enum):
    """VikingDB operations."""

    INSERT = "insert"
    UPDATE = "update"
    UPSERT = "upsert"
    DELETE = "delete"
    GET = "get"
    EXISTS = "exists"
    SEARCH = "search"
    FILTER = "filter"
    CREATE_COLLECTION = "create_collection"
    DROP_COLLECTION = "drop_collection"
    COLLECTION_EXISTS = "collection_exists"
    LIST_COLLECTIONS = "list_collections"


@dataclass
class AGFSCallRecord:
    """
    Record of a single AGFS client call.

    Used when recording VikingFS operations that may involve multiple AGFS calls.
    """

    operation: str
    request: Dict[str, Any]
    response: Optional[Any] = None
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


@dataclass
class IORecord:
    """
    Single IO operation record.

    Attributes:
        timestamp: ISO format timestamp
        io_type: IO type (fs or vikingdb)
        operation: Operation name
        request: Request parameters
        response: Response data (serialized)
        latency_ms: Latency in milliseconds
        success: Whether operation succeeded
        error: Error message if failed
        agfs_calls: List of AGFS calls made during this operation (for VikingFS operations)
    """

    timestamp: str
    io_type: str
    operation: str
    request: Dict[str, Any]
    response: Optional[Any] = None
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None
    agfs_calls: List[AGFSCallRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""

        def serialize_any(obj: Any) -> Any:
            """Recursively serialize any object."""
            if obj is None:
                return None
            if isinstance(obj, bytes):
                return {"__bytes__": obj.decode("utf-8", errors="replace")}
            if isinstance(obj, dict):
                return {k: serialize_any(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [serialize_any(item) for item in obj]
            if isinstance(obj, (str, int, float, bool)):
                return obj
            if hasattr(obj, "__dict__"):
                return serialize_any(obj.__dict__)
            return str(obj)

        data = asdict(self)
        data["response"] = serialize_any(data["response"])

        serialized_agfs_calls = []
        for call in data["agfs_calls"]:
            serialized_call = call.copy()
            serialized_call["request"] = serialize_any(serialized_call["request"])
            serialized_call["response"] = serialize_any(serialized_call["response"])
            serialized_agfs_calls.append(serialized_call)
        data["agfs_calls"] = serialized_agfs_calls

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IORecord":
        """Create from dictionary."""
        data = data.copy()
        if "agfs_calls" in data and data["agfs_calls"]:
            agfs_calls = []
            for call_data in data["agfs_calls"]:
                if isinstance(call_data, dict):
                    agfs_calls.append(AGFSCallRecord(**call_data))
                else:
                    agfs_calls.append(call_data)
            data["agfs_calls"] = agfs_calls
        return cls(**data)


__all__ = [
    "IOType",
    "FSOperation",
    "VikingDBOperation",
    "AGFSCallRecord",
    "IORecord",
]
