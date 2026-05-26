# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory isolation helpers for resolving session memory write targets."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from openviking.core.namespace import to_user_space
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.utils.uri import generate_uri, render_template
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass
class RoleScope:
    """Participant scope inferred from session messages."""

    user_ids: List[str]
    peer_ids: List[str] = field(default_factory=list)


PEER_MEMORY_TYPES = {"profile", "preferences", "entities", "events"}


def peer_user_space(user_space: str, peer_id: str) -> str:
    """Return the user-space fragment for memory about a stable peer."""
    return f"{user_space}/peers/{peer_id}"


def is_peer_memory_type(memory_type: str) -> bool:
    return memory_type in PEER_MEMORY_TYPES


def safe_peer_id(peer_id: Optional[str]) -> Optional[str]:
    if not peer_id:
        return None
    if "/" in peer_id or "\\" in peer_id:
        return None
    return peer_id


class MemoryIsolationHandler:
    """Memory isolation handler."""

    def __init__(
        self,
        ctx: RequestContext,
        extract_context: Any,
        target_peer_id: Optional[str] = None,
        allowed_memory_types: Optional[Set[str]] = None,
    ):
        self.ctx = ctx
        self._extract_context = extract_context
        self.target_peer_id = safe_peer_id(target_peer_id)
        self.allowed_memory_types = (
            {str(item) for item in allowed_memory_types}
            if allowed_memory_types is not None
            else None
        )
        config = get_openviking_config()
        self.role_id_memory_isolation_enabled = (
            config.memory.role_id_memory_isolation_enabled if config.memory else False
        )

    def prepare_messages(self) -> None:
        """Normalize missing role_id values when role_id memory isolation is disabled."""
        if self.role_id_memory_isolation_enabled:
            return
        messages = self._extract_context.messages if self._extract_context else []
        for msg in messages:
            msg.role_id = self.ctx.resolve_role_id(msg.role, msg.role_id) if self.ctx else None

    def get_read_scope(self) -> RoleScope:
        user_ids = set()
        peer_ids = set()

        if self.ctx and self.ctx.user:
            user_id = self.ctx.user.user_id
            if user_id:
                user_ids.add(user_id)

        messages = self._extract_context.messages if self._extract_context else []
        for msg in messages:
            peer_id = safe_peer_id(getattr(msg, "peer_id", None))
            if peer_id:
                peer_ids.add(peer_id)
        if self.target_peer_id:
            peer_ids.add(self.target_peer_id)

        return RoleScope(
            user_ids=list(user_ids),
            peer_ids=list(peer_ids),
        )

    def fill_role_ids(self, item_dict: Dict[str, Any], role_scope: RoleScope) -> None:
        user_ids = set()
        peer_ids = set()

        def add_role_id(role_ids, role_id, scope_ids):
            if role_id is None:
                return
            if role_id not in scope_ids:
                return
            role_ids.add(role_id)

        def add_user_id(user_id):
            add_role_id(user_ids, user_id, role_scope.user_ids)

        def check_set_default():
            if not user_ids and role_scope.user_ids:
                user_ids.add(role_scope.user_ids[0])

        if item_dict.get("ranges") is None:
            add_user_id(item_dict.get("user_id"))
            if self.target_peer_id:
                peer_ids.add(self.target_peer_id)
            check_set_default()
            if user_ids:
                item_dict["user_id"] = list(user_ids)[0]
            item_dict.pop("agent_id", None)
            if len(peer_ids) == 1:
                item_dict["peer_id"] = list(peer_ids)[0]

        else:
            # 使用 ExtractContext 的方法解析 ranges
            msg_range = self._extract_context.read_message_ranges(item_dict.get("ranges"))
            # elements 是 List[List[Message]] - 遍历所有消息组
            for msg_group in msg_range.elements:
                for msg in msg_group:
                    if msg.role == "user":
                        add_user_id(msg.role_id)
            if self.target_peer_id:
                peer_ids.add(self.target_peer_id)
            check_set_default()
            item_dict["user_ids"] = list(user_ids)
            item_dict.pop("agent_ids", None)
            if len(peer_ids) == 1:
                item_dict["peer_id"] = list(peer_ids)[0]

    def allows_schema(self, memory_type_schema: MemoryTypeSchema) -> bool:
        memory_type = getattr(memory_type_schema, "memory_type", "")
        if self.allowed_memory_types is not None and memory_type not in self.allowed_memory_types:
            return False
        if self.target_peer_id and not is_peer_memory_type(memory_type):
            return False
        return True

    def _template_vars(self, user_id: str, memory_type: str) -> Dict[str, str]:
        policy = self.ctx.namespace_policy
        user_space = to_user_space(policy, user_id)
        if self.target_peer_id and is_peer_memory_type(memory_type):
            user_space = peer_user_space(user_space, self.target_peer_id)
        return {
            "user_space": user_space,
            "agent_space": user_space,
        }

    def render_schema_directory(self, memory_type_schema: MemoryTypeSchema) -> str:
        user_id = self.ctx.user.user_id if self.ctx and self.ctx.user else "default"
        return render_template(
            memory_type_schema.directory,
            self._template_vars(
                user_id,
                getattr(memory_type_schema, "memory_type", ""),
            ),
            self._extract_context,
        )

    def calculate_memory_uris(
        self,
        memory_type_schema: MemoryTypeSchema,
        operation: ResolvedOperation,
        extract_context: ExtractContext,
    ):
        if not self.allows_schema(memory_type_schema):
            return []
        policy = self.ctx.namespace_policy

        if not self.ctx or not self.ctx.user:
            return []

        user_id = self.ctx.user.user_id
        operation.memory_fields["user_id"] = user_id
        operation.memory_fields.pop("agent_id", None)
        operation.memory_fields.pop("agent_ids", None)

        # 文件
        uris = set()
        user_space = to_user_space(policy, user_id)
        if self.target_peer_id and is_peer_memory_type(memory_type_schema.memory_type):
            operation.memory_fields["peer_id"] = self.target_peer_id
            user_space = peer_user_space(user_space, self.target_peer_id)
        uri = generate_uri(
            memory_type=memory_type_schema,
            fields=operation.memory_fields,
            user_space=user_space,
            agent_space=user_space,
            extract_context=extract_context,
        )
        uris.add(uri)

        return list(uris)
