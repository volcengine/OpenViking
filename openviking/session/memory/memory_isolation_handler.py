# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory Isolation Handler - 处理记忆的隔离机制

根据 account namespace policy 和 session 参与者列表，
计算记忆的写入目录并校验 role_id。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from openviking.core.namespace import to_user_space, to_agent_space
from openviking.message import Message
from openviking.server.identity import AccountNamespacePolicy, RequestContext
from openviking.session.memory.utils.uri import generate_uri
from openviking.session.memory.memory_updater import ExtractContext
from openviking_cli.session.user_id import UserIdentifier
from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass
class RoleScope:
    """Role 作用范围 - 从 messages 推断的可访问范围"""

    user_ids: List[str]  # 参与者中的 user_id 列表
    agent_ids: List[str]  # 参与者中的 agent_id 列表


class MemoryIsolationHandler:
    """Memory isolation handler."""

    def __init__(self, ctx: RequestContext, extract_context: Any):
        self.ctx = ctx
        self._extract_context = extract_context
        config = get_openviking_config()
        self.enable_role_id_memory_isolate = (
            config.memory.enable_role_id_memory_isolate if config.memory else False
        )

    def prepare_messages(self) -> None:
        """开关关闭时，清空 messages 中的 role_id，使下游统一使用登录用户。"""
        if self.enable_role_id_memory_isolate:
            return
        messages = self._extract_context.messages if self._extract_context else []
        for msg in messages:
            msg.role_id = None

    def get_read_scope(self) -> RoleScope:
        user_ids = set()
        agent_ids = set()

        # 先从 messages 中提取 role_id
        messages = self._extract_context.messages if self._extract_context else []
        for msg in messages:
            role = msg.role
            role_id = msg.role_id
            if not role_id:
                continue
            if role == "user":
                user_ids.add(role_id)
            elif role == "assistant":
                agent_ids.add(role_id)

        # 只有当 messages 中没有提取到 user_ids/agent_ids 时，才添加 ctx 中的 userid/agentid
        if self.ctx and self.ctx.user:
            user_id = self.ctx.user.user_id
            agent_id = self.ctx.user.agent_id
            if not user_ids and user_id:
                user_ids.add(user_id)
            if not agent_ids and agent_id:
                agent_ids.add(agent_id)

        return RoleScope(
            user_ids=list(user_ids),
            agent_ids=list(agent_ids),
        )

    def fill_role_ids(self, item_dict: Dict[str, Any], role_scope: RoleScope) -> None:

        user_ids = set()
        agent_ids = set()

        def add_role_id(role_ids, role_id, scope_ids):
            if role_id is None:
                return
            if role_id not in scope_ids:
                return
            role_ids.add(role_id)

        def add_user_id(user_id):
            add_role_id(user_ids, user_id, role_scope.user_ids)

        def add_agent_id(agent_id):
            add_role_id(agent_ids, agent_id, role_scope.agent_ids)

        def check_set_default():
            if not user_ids:
                user_ids.add(role_scope.user_ids[0])
            if not agent_ids:
                agent_ids.add(role_scope.agent_ids[0])

        if item_dict.get("ranges") is None:
            add_user_id(item_dict.get("user_id"))
            add_agent_id(item_dict.get("agent_id"))
            check_set_default()
            item_dict["user_id"] = list(user_ids)[0]
            item_dict["agent_id"] = list(agent_ids)[0]

        else:
            # 使用 ExtractContext 的方法解析 ranges
            msg_range = self._extract_context.read_message_ranges(item_dict.get("ranges"))
            # elements 是 List[List[Message]] - 遍历所有消息组
            for msg_group in msg_range.elements:
                for msg in msg_group:
                    if msg.role == "user":
                        add_user_id(msg.role_id)
                    elif msg.role == "assistant":
                        add_agent_id(msg.role_id)
            check_set_default()
            item_dict["user_ids"] = list(user_ids)
            item_dict["agent_ids"] = list(agent_ids)

    def _extract_role_ids_from_messages_range(
        self, ranges: Optional[str], user_ids: Set[str], agent_ids: Set[str]
    ):
        """
        从 events 的 ranges 字段提取涉及的 role_id。

        解析 ranges 格式，提取范围内的所有 user 角色的消息参与者。
        """
        if not ranges or not self._extract_context:
            return

        messages = self._extract_context.messages
        if not messages:
            return []

    def calculate_memory_uris(
        self,
        memory_type_schema: MemoryTypeSchema,
        operation: ResolvedOperation,
        extract_context: ExtractContext,
    ):
        policy = self.ctx.namespace_policy

        user_ids = operation.memory_fields.get("user_ids") or [
            operation.memory_fields.get("user_id")
        ]
        agent_ids = operation.memory_fields.get("agent_ids") or [
            operation.memory_fields.get("agent_id")
        ]
        # 文件
        uris = set()
        for user_id in user_ids:
            for agent_id in agent_ids:
                user_space = to_user_space(policy, user_id, agent_id)
                agent_space = to_agent_space(policy, user_id, agent_id)
                uri = generate_uri(
                    memory_type=memory_type_schema,
                    fields=operation.memory_fields,
                    user_space=user_space,
                    agent_space=agent_space,
                    extract_context=extract_context,
                )
                uris.add(uri)

        return list(uris)
