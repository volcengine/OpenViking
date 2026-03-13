# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Union
from uuid import uuid4


@dataclass
class EmbeddingMsg:
    message: Union[str, List[Dict[str, Any]]]
    context_data: Dict[str, Any]

    def __init__(self, message: Union[str, List[Dict[str, Any]]], context_data: Dict[str, Any]):
        self.id = str(uuid4())
        self.message = message
        self.context_data = context_data

    def to_dict(self) -> Dict[str, Any]:
        """Convert embedding message to dictionary format."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert embedding message to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmbeddingMsg":
        """Create an embedding message object from dictionary."""
        obj = EmbeddingMsg(
            message=data["message"],
            context_data=data["context_data"],
        )
        obj.id = data.get("id", obj.id)
        return obj

    @classmethod
    def from_json(cls, json_str: str) -> "EmbeddingMsg":
        """Safely create object from JSON string."""
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")
