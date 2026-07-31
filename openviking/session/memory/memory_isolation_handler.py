# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory isolation helpers for resolving session memory write targets."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from openviking.core.peer_id import safe_peer_id
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.utils.uri import generate_uri, render_template
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_INTERNAL_MEMORY_TYPES = {"session_skills"}
_SELF_PEER_ID = "__self"


@dataclass
class RoleScope:
    """Participant scope inferred from session messages."""

    user_ids: List[str]
    peer_ids: List[str] = field(default_factory=list)


def peer_user_space(user_space: str, peer_id: str) -> str:
    """Return the user-space fragment for memory about a stable peer."""
    if peer_id == _SELF_PEER_ID:
        return user_space
    return f"{user_space}/peers/{peer_id}"


class MemoryIsolationHandler:
    """Memory isolation handler."""

    def __init__(
        self,
        ctx: RequestContext,
        extract_context: Any,
        allowed_memory_types: Optional[Set[str]] = None,
        allow_self: bool = True,
        allowed_peer_ids: Optional[Set[str]] = None,
    ):
        self.ctx = ctx
        self._extract_context = extract_context
        self.allowed_memory_types = (
            {str(item) for item in allowed_memory_types}
            if allowed_memory_types is not None
            else None
        )
        peer_ids = {
            item
            for item in (safe_peer_id(item) for item in allowed_peer_ids or set())
            if item and item != _SELF_PEER_ID
        }
        self.allow_self = bool(allow_self)
        self.allowed_peer_ids = peer_ids
        self.allow_peer = bool(peer_ids)

    def prepare_messages(self) -> None:
        """No-op hook kept for the extraction pipeline."""
        return

    def _messages(self) -> List[Any]:
        messages = getattr(self._extract_context, "messages", None)
        return messages if isinstance(messages, list) else []

    def _message_target_id(self, msg: Any) -> Optional[str]:
        raw_peer_id = getattr(msg, "peer_id", None)
        peer_id = safe_peer_id(raw_peer_id)
        if peer_id and self._is_peer_owner_message(msg) and self._can_write_peer(peer_id):
            return peer_id
        if raw_peer_id in (None, "") and self.allow_self:
            return _SELF_PEER_ID
        return None

    @staticmethod
    def _is_peer_owner_message(msg: Any) -> bool:
        return getattr(msg, "role", None) == "user"

    def get_read_scope(self) -> RoleScope:
        user_ids = set()
        peer_ids = set()

        if self.ctx and self.ctx.user:
            user_id = self.ctx.user.user_id
            if user_id:
                user_ids.add(user_id)

        if self.allow_peer:
            peer_ids.update(self.allowed_peer_ids)

        return RoleScope(
            user_ids=sorted(user_ids),
            peer_ids=sorted(peer_ids),
        )

    def fill_identity_fields(
        self,
        item_dict: Dict[str, Any],
        role_scope: RoleScope,
        memory_type_schema: Optional[MemoryTypeSchema] = None,
    ) -> None:
        del role_scope
        if self.ctx and self.ctx.user and self.ctx.user.user_id:
            item_dict["user_id"] = self.ctx.user.user_id
        item_dict.pop("user_ids", None)

        if memory_type_schema is not None and not memory_type_schema.peer_enabled:
            item_dict.pop("peer_id", None)
            return

        peer_id = safe_peer_id(item_dict.get("peer_id"))
        if peer_id and peer_id != _SELF_PEER_ID:
            item_dict["peer_id"] = peer_id
        else:
            item_dict.pop("peer_id", None)

    def allows_schema(self, memory_type_schema: MemoryTypeSchema) -> bool:
        memory_type = getattr(memory_type_schema, "memory_type", "")
        if memory_type in _INTERNAL_MEMORY_TYPES:
            return True
        if self.allowed_memory_types is not None and memory_type not in self.allowed_memory_types:
            return False
        if not self.allow_self and not getattr(memory_type_schema, "peer_enabled", True):
            return False
        return True

    def _can_write_peer(self, peer_id: str) -> bool:
        return self.allow_peer and peer_id in self.allowed_peer_ids

    def _unique_peer_target_id_in_messages(self) -> Optional[str]:
        targets = [
            peer_id
            for msg in self._messages()
            if (peer_id := safe_peer_id(getattr(msg, "peer_id", None)))
            and self._is_peer_owner_message(msg)
            and self._can_write_peer(peer_id)
        ]
        peer_ids = list(dict.fromkeys(targets))
        return peer_ids[0] if len(peer_ids) == 1 else None

    def render_schema_directories(self, memory_type_schema: MemoryTypeSchema) -> List[str]:
        user_id = self.ctx.user.user_id if self.ctx and self.ctx.user else "default"
        user_space = user_id
        user_spaces: List[str] = []
        if self.allow_self:
            user_spaces.append(user_space)
        if self.allow_peer and getattr(memory_type_schema, "peer_enabled", True):
            for peer_id in sorted(self.allowed_peer_ids):
                user_spaces.append(peer_user_space(user_space, peer_id))

        directories = []
        for target_user_space in dict.fromkeys(user_spaces):
            directories.append(
                render_template(
                    memory_type_schema.directory,
                    {"user_space": target_user_space},
                    self._extract_context,
                )
            )
        return directories

    def _range_targets(self, ranges: Any) -> List[str]:
        if not ranges or not self._extract_context:
            return []
        try:
            msg_range = self._extract_context.read_message_ranges(str(ranges))
        except Exception:
            logger.warning("Failed to parse memory ranges for peer memory: %s", ranges)
            return []

        target_ids = []
        for msg_group in getattr(msg_range, "elements", []) or []:
            for msg in msg_group:
                target_id = self._message_target_id(msg)
                if target_id:
                    target_ids.append(target_id)
        return list(dict.fromkeys(target_ids))

    def _resolve_operation_target_id(self, raw_peer_id: Any) -> Optional[str]:
        peer_id = safe_peer_id(raw_peer_id)
        if peer_id == _SELF_PEER_ID and self.allow_self:
            return _SELF_PEER_ID
        if peer_id and self._can_write_peer(peer_id):
            return peer_id
        if raw_peer_id not in (None, ""):
            return None
        if self.allow_self:
            return _SELF_PEER_ID
        return self._unique_peer_target_id_in_messages()

    def calculate_memory_uris(
        self,
        memory_type_schema: MemoryTypeSchema,
        operation: ResolvedOperation,
        extract_context: ExtractContext,
    ):
        if not self.allows_schema(memory_type_schema):
            return []

        if not self.ctx or not self.ctx.user:
            return []

        user_id = self.ctx.user.user_id
        operation.memory_fields["user_id"] = user_id

        target_ids: List[str] = []
        has_ranges = operation.memory_fields.get("ranges") is not None
        if not getattr(memory_type_schema, "peer_enabled", True):
            operation.memory_fields.pop("peer_id", None)
            target_ids = [_SELF_PEER_ID] if self.allow_self else []
        elif operation.memory_fields.get("ranges") is not None:
            target_ids = self._range_targets(
                operation.memory_fields.get("ranges"),
            )
            operation.memory_fields.pop("peer_id", None)
        else:
            target_id = self._resolve_operation_target_id(
                operation.memory_fields.get("peer_id"),
            )
            if target_id:
                target_ids = [target_id]
            if target_id == _SELF_PEER_ID:
                operation.memory_fields.pop("peer_id", None)
            elif target_id:
                operation.memory_fields["peer_id"] = target_id
            else:
                operation.memory_fields.pop("peer_id", None)

        if not target_ids:
            return []

        # 文件
        uris = set()
        user_space = user_id
        base_fields = dict(operation.memory_fields)
        for target_id in target_ids:
            fields = dict(base_fields)
            if target_id == _SELF_PEER_ID:
                target_user_space = user_space
                fields.pop("peer_id", None)
            else:
                target_user_space = peer_user_space(user_space, target_id)
                fields["peer_id"] = target_id
            uris.add(
                generate_uri(
                    memory_type=memory_type_schema,
                    fields=fields,
                    user_space=target_user_space,
                    extract_context=extract_context,
                )
            )

        if has_ranges:
            operation.memory_fields.pop("peer_id", None)
        return list(uris)
