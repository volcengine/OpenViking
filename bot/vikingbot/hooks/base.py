from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from vikingbot.config.schema import SessionKey


@dataclass
class HookContext:
    event_type: str
    session_id: Optional[str] = None
    # 沙箱唯一主键
    workspace_id: Optional[str] = None
    session_key: SessionKey = None
    metadata: Dict[str, Any] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.timestamp is None:
            self.timestamp = datetime.now()


class Hook(ABC):
    name: str
    is_sync: bool = False

    @abstractmethod
    async def execute(self, context: HookContext, **kwargs) -> Any:
        pass
