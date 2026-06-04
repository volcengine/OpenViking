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


@dataclass
class RoleScope:
    """Participant scope inferred from session messages."""

    user_ids: List[str]
    peer_ids: List[str] = field(default_factory=list)


def peer_user_space(user_space: str, peer_id: str) -> str:
    """Return the user-space fragment for memory about a stable peer."""
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
        peer_ids = {safe_peer_id(item) for item in allowed_peer_ids or set()}
        peer_ids = {item for item in peer_ids if item}
        self.allow_self = bool(allow_self)
        self.allowed_peer_ids = peer_ids
        self.allow_peer = bool(peer_ids)

    def prepare_messages(self) -> None:
        """No-op hook kept for the extraction pipeline."""
        return

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

    def fill_identity_fields(self, item_dict: Dict[str, Any], role_scope: RoleScope) -> None:
        del role_scope
        if self.ctx and self.ctx.user and self.ctx.user.user_id:
            item_dict["user_id"] = self.ctx.user.user_id
        item_dict.pop("user_ids", None)

        peer_id = safe_peer_id(item_dict.get("peer_id"))
        if peer_id:
            item_dict["peer_id"] = peer_id
        else:
            item_dict.pop("peer_id", None)

    def allows_schema(self, memory_type_schema: MemoryTypeSchema) -> bool:
        memory_type = getattr(memory_type_schema, "memory_type", "")
        if self.allowed_memory_types is not None and memory_type not in self.allowed_memory_types:
            return False
        return True

    def _can_write_peer(self, peer_id: str) -> bool:
        return self.allow_peer and peer_id in self.allowed_peer_ids

    def render_schema_directories(self, memory_type_schema: MemoryTypeSchema) -> List[str]:
        user_id = self.ctx.user.user_id if self.ctx and self.ctx.user else "default"
        user_space = user_id
        user_spaces: List[str] = []
        if self.allow_self:
            user_spaces.append(user_space)
        if self.allow_peer:
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

    def _range_targets(self, ranges: Any) -> tuple[bool, List[str]]:
        if not ranges or not self._extract_context:
            return False, []
        try:
            msg_range = self._extract_context.read_message_ranges(str(ranges))
        except Exception:
            logger.warning("Failed to parse memory ranges for peer routing: %s", ranges)
            return False, []

        include_self = False
        peer_ids = set()
        for msg_group in getattr(msg_range, "elements", []) or []:
            for msg in msg_group:
                raw_peer_id = getattr(msg, "peer_id", None)
                peer_id = safe_peer_id(raw_peer_id)
                if peer_id:
                    if self._can_write_peer(peer_id):
                        peer_ids.add(peer_id)
                elif self.allow_self:
                    include_self = True
        return include_self, sorted(peer_ids)

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

        peer_ids_to_write: List[str] = []
        include_self = False

        if operation.memory_fields.get("ranges") is not None:
            include_self, peer_ids_to_write = self._range_targets(
                operation.memory_fields.get("ranges"),
            )
            operation.memory_fields.pop("peer_id", None)
        else:
            raw_peer_id = operation.memory_fields.get("peer_id")
            peer_id = safe_peer_id(raw_peer_id)
            if raw_peer_id and not peer_id:
                return []
            if peer_id:
                if not self._can_write_peer(peer_id):
                    return []
                peer_ids_to_write = [peer_id]
                operation.memory_fields["peer_id"] = peer_id
            elif self.allow_self:
                include_self = True
                operation.memory_fields.pop("peer_id", None)

        if not include_self and not peer_ids_to_write:
            return []

        # 文件
        uris = set()
        user_space = user_id
        if include_self:
            uris.add(
                generate_uri(
                    memory_type=memory_type_schema,
                    fields=operation.memory_fields,
                    user_space=user_space,
                    extract_context=extract_context,
                )
            )
        for peer_id in peer_ids_to_write:
            target_user_space = peer_user_space(user_space, peer_id)
            uris.add(
                generate_uri(
                    memory_type=memory_type_schema,
                    fields=operation.memory_fields,
                    user_space=target_user_space,
                    extract_context=extract_context,
                )
            )

        return list(uris)
