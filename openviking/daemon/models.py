"""
Data models for OpenViking Active Daemon.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FileCursor:
    """Tracks file read position for incremental processing."""
    file_path: str
    last_position: int = 0
    last_read_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "last_position": self.last_position,
            "last_read_time": self.last_read_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileCursor":
        return cls(
            file_path=data["file_path"],
            last_position=data.get("last_position", 0),
            last_read_time=data.get("last_read_time", 0.0),
        )


@dataclass
class BatchBuffer:
    """Buffer for accumulating events before batch processing."""
    lines: List[Dict[str, Any]] = field(default_factory=list)
    byte_count: int = 0
    created_at: float = 0.0

    def add_line(self, line: Dict[str, Any], byte_size: int):
        self.lines.append(line)
        self.byte_count += byte_size

    def is_empty(self) -> bool:
        return len(self.lines) == 0

    def clear(self):
        self.lines.clear()
        self.byte_count = 0
        self.created_at = 0.0


@dataclass
class ConversationTurn:
    """A complete user-assistant conversation turn."""
    user_prompt: str
    assistant_response: str
    session_id: Optional[str] = None
    project_name: Optional[str] = None
    timestamp: Optional[str] = None
    source_tool: Optional[str] = None


@dataclass
class ExtractedKnowledge:
    """Structured knowledge extracted from a conversation."""
    status: str  # "EXTRACTED" | "IGNORED"
    category: str  # "skills" | "memories" | "resources"
    title: str
    content: str
    confidence: float = 0.0
    project_name: Optional[str] = None
    entity_links: List[str] = field(default_factory=list)
    actionable_steps: List[str] = field(default_factory=list)
    timestamp: Optional[str] = None
    source_tool: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "category": self.category,
            "title": self.title,
            "content": self.content,
            "confidence": self.confidence,
            "project_name": self.project_name,
            "entity_links": self.entity_links,
            "actionable_steps": self.actionable_steps,
            "timestamp": self.timestamp,
            "source_tool": self.source_tool,
        }
