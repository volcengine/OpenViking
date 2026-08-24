# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory isolation helpers for resolving session memory write targets."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from openviking.core.peer_id import safe_peer_id
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import (
    MemoryOperationSkip,
    MemoryOperationSkipCode,
    MemoryTypeSchema,
    ResolvedOperation,
)
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.utils.uri import generate_uri, render_template
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_INTERNAL_MEMORY_TYPES = {"session_skills"}
_SELF_PEER_ID = "__self"

_SKIP_REASON_MESSAGES = {
    MemoryOperationSkipCode.MEMORY_TYPE_FILTERED: (
        "Memory type is outside the allowed extraction scope"
    ),
    MemoryOperationSkipCode.SELF_MEMORY_DISABLED: "Self memory writes are disabled",
    MemoryOperationSkipCode.PEER_MEMORY_DISABLED: "Peer memory writes are disabled",
    MemoryOperationSkipCode.INVALID_PEER_ID: "Target peer ID is invalid",
    MemoryOperationSkipCode.PEER_NOT_ALLOWED: ("Target peer is outside the allowed memory scope"),
    MemoryOperationSkipCode.INVALID_RANGES: "Message ranges are malformed or out of bounds",
    MemoryOperationSkipCode.AMBIGUOUS_TARGET: "Memory ownership cannot be resolved uniquely",
    MemoryOperationSkipCode.NO_WRITABLE_TARGET: "No writable memory target could be resolved",
}

_SKIP_REASON_PRIORITY = {
    MemoryOperationSkipCode.INVALID_PEER_ID: 0,
    MemoryOperationSkipCode.INVALID_RANGES: 1,
    MemoryOperationSkipCode.PEER_MEMORY_DISABLED: 2,
    MemoryOperationSkipCode.PEER_NOT_ALLOWED: 3,
    MemoryOperationSkipCode.SELF_MEMORY_DISABLED: 4,
    MemoryOperationSkipCode.AMBIGUOUS_TARGET: 5,
    MemoryOperationSkipCode.NO_WRITABLE_TARGET: 6,
}


@dataclass
class RoleScope:
    """Participant scope inferred from session messages."""

    user_ids: List[str]
    peer_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class _TargetResolution:
    target_ids: List[str] = field(default_factory=list)
    skip_code: Optional[MemoryOperationSkipCode] = None


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
        peer_memory_enabled: Optional[bool] = None,
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
        self.peer_memory_enabled = (
            bool(peer_memory_enabled) if peer_memory_enabled is not None else True
        )
        self.allow_peer = bool(peer_ids)

    def prepare_messages(self) -> None:
        """No-op hook kept for the extraction pipeline."""
        return

    def _messages(self) -> List[Any]:
        messages = getattr(self._extract_context, "messages", None)
        return messages if isinstance(messages, list) else []

    def _message_target_id(self, msg: Any) -> Optional[str]:
        resolution = self._message_target_resolution(msg)
        return resolution.target_ids[0] if resolution.target_ids else None

    def _message_target_resolution(self, msg: Any) -> _TargetResolution:
        if not self._is_peer_owner_message(msg):
            return _TargetResolution()
        raw_peer_id = getattr(msg, "peer_id", None)
        if raw_peer_id in (None, ""):
            if self.allow_self:
                return _TargetResolution([_SELF_PEER_ID])
            return _TargetResolution(skip_code=MemoryOperationSkipCode.SELF_MEMORY_DISABLED)
        peer_id = safe_peer_id(raw_peer_id)
        if not peer_id:
            return _TargetResolution(skip_code=MemoryOperationSkipCode.INVALID_PEER_ID)
        if peer_id == _SELF_PEER_ID:
            # ``__self`` is an internal operation-target sentinel, not a valid
            # message peer. Preserve the legacy behavior: an explicit message
            # peer with this value does not resolve to a writable target.
            return _TargetResolution(skip_code=MemoryOperationSkipCode.NO_WRITABLE_TARGET)
        if not self.peer_memory_enabled:
            return _TargetResolution(skip_code=MemoryOperationSkipCode.PEER_MEMORY_DISABLED)
        if not self._can_write_peer(peer_id):
            return _TargetResolution(skip_code=MemoryOperationSkipCode.PEER_NOT_ALLOWED)
        return _TargetResolution([peer_id])

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

    @staticmethod
    def _classify_identity_fields(
        item_dict: Dict[str, Any],
        memory_type_schema: Optional[MemoryTypeSchema] = None,
    ) -> Optional[MemoryOperationSkip]:
        """Capture a diagnostic hint without changing identity-field normalization."""
        if memory_type_schema is not None and not memory_type_schema.peer_enabled:
            return None
        raw_peer_id = item_dict.get("peer_id")
        if raw_peer_id not in (None, "", _SELF_PEER_ID):
            if safe_peer_id(raw_peer_id):
                return None
            return MemoryOperationSkip(
                reason_code=MemoryOperationSkipCode.INVALID_PEER_ID,
                reason=_SKIP_REASON_MESSAGES[MemoryOperationSkipCode.INVALID_PEER_ID],
            )
        return None

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

    def _target_ids_in_messages(self) -> List[str]:
        targets = [
            target_id for msg in self._messages() if (target_id := self._message_target_id(msg))
        ]
        return list(dict.fromkeys(targets))

    def _range_is_fully_in_bounds(self, ranges: Any) -> bool:
        parts = str(ranges).split(",")
        if not parts or any(not part.strip() for part in parts):
            return False

        message_count = len(self._messages())
        if message_count == 0:
            return False

        try:
            for part in parts:
                bounds = part.strip().split("-")
                if len(bounds) == 1:
                    start = end = int(bounds[0])
                elif len(bounds) == 2 and all(bound.strip() for bound in bounds):
                    start, end = (int(bound) for bound in bounds)
                else:
                    return False
                if start < 0 or start > end or end >= message_count:
                    return False
        except ValueError:
            return False
        return True

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

    @staticmethod
    def _preferred_skip_code(
        skip_codes: List[MemoryOperationSkipCode],
    ) -> MemoryOperationSkipCode:
        if not skip_codes:
            return MemoryOperationSkipCode.NO_WRITABLE_TARGET
        return min(skip_codes, key=lambda code: _SKIP_REASON_PRIORITY.get(code, 999))

    def _range_targets(self, ranges: Any) -> _TargetResolution:
        if not ranges or not self._extract_context:
            return _TargetResolution(skip_code=MemoryOperationSkipCode.INVALID_RANGES)
        range_is_fully_in_bounds = self._range_is_fully_in_bounds(ranges)
        try:
            msg_range = self._extract_context.read_message_ranges(str(ranges))
        except Exception:
            logger.warning("Failed to parse memory ranges for peer memory: %s", ranges)
            return _TargetResolution(skip_code=MemoryOperationSkipCode.INVALID_RANGES)

        target_ids = []
        has_message = False
        has_user_message = False
        skip_codes: List[MemoryOperationSkipCode] = []
        for msg_group in getattr(msg_range, "elements", []) or []:
            for msg in msg_group:
                has_message = True
                if self._is_peer_owner_message(msg):
                    has_user_message = True
                resolution = self._message_target_resolution(msg)
                target_ids.extend(resolution.target_ids)
                if resolution.skip_code is not None:
                    skip_codes.append(resolution.skip_code)
        target_ids = list(dict.fromkeys(target_ids))
        if target_ids:
            return _TargetResolution(target_ids)
        can_fallback = range_is_fully_in_bounds and has_message and not has_user_message
        if can_fallback:
            fallback_targets = self._target_ids_in_messages()
            if len(fallback_targets) == 1:
                return _TargetResolution(fallback_targets)
            if len(fallback_targets) > 1:
                return _TargetResolution(skip_code=MemoryOperationSkipCode.AMBIGUOUS_TARGET)
        if not range_is_fully_in_bounds:
            return _TargetResolution(skip_code=MemoryOperationSkipCode.INVALID_RANGES)
        if has_user_message:
            return _TargetResolution(skip_code=self._preferred_skip_code(skip_codes))
        return _TargetResolution(skip_code=MemoryOperationSkipCode.NO_WRITABLE_TARGET)

    def _resolve_operation_target(self, raw_peer_id: Any) -> _TargetResolution:
        if raw_peer_id not in (None, ""):
            peer_id = safe_peer_id(raw_peer_id)
            if not peer_id:
                return _TargetResolution(skip_code=MemoryOperationSkipCode.INVALID_PEER_ID)
            if peer_id == _SELF_PEER_ID:
                if self.allow_self:
                    return _TargetResolution([_SELF_PEER_ID])
                return _TargetResolution(skip_code=MemoryOperationSkipCode.SELF_MEMORY_DISABLED)
            if not self.peer_memory_enabled:
                return _TargetResolution(skip_code=MemoryOperationSkipCode.PEER_MEMORY_DISABLED)
            if not self._can_write_peer(peer_id):
                return _TargetResolution(skip_code=MemoryOperationSkipCode.PEER_NOT_ALLOWED)
            return _TargetResolution([peer_id])
        if self.allow_self:
            return _TargetResolution([_SELF_PEER_ID])
        peer_targets = list(
            dict.fromkeys(
                target
                for msg in self._messages()
                for target in self._message_target_resolution(msg).target_ids
                if target != _SELF_PEER_ID
            )
        )
        if len(peer_targets) == 1:
            return _TargetResolution(peer_targets)
        if len(peer_targets) > 1:
            return _TargetResolution(skip_code=MemoryOperationSkipCode.AMBIGUOUS_TARGET)
        return _TargetResolution(skip_code=MemoryOperationSkipCode.NO_WRITABLE_TARGET)

    @staticmethod
    def _skip_operation(
        operation: ResolvedOperation,
        reason_code: MemoryOperationSkipCode,
    ) -> List[str]:
        operation.resolution_skip = MemoryOperationSkip(
            reason_code=reason_code,
            reason=_SKIP_REASON_MESSAGES[reason_code],
        )
        return []

    def calculate_memory_uris(
        self,
        memory_type_schema: MemoryTypeSchema,
        operation: ResolvedOperation,
        extract_context: ExtractContext,
    ):
        identity_resolution_skip = operation.resolution_skip
        operation.resolution_skip = None
        if not self.allows_schema(memory_type_schema):
            reason_code = (
                MemoryOperationSkipCode.SELF_MEMORY_DISABLED
                if not self.allow_self and not getattr(memory_type_schema, "peer_enabled", True)
                else MemoryOperationSkipCode.MEMORY_TYPE_FILTERED
            )
            return self._skip_operation(operation, reason_code)

        if not self.ctx or not self.ctx.user:
            return []

        user_id = self.ctx.user.user_id
        operation.memory_fields["user_id"] = user_id

        target_ids: List[str] = []
        skip_code: Optional[MemoryOperationSkipCode] = None
        has_ranges = operation.memory_fields.get("ranges") is not None
        if not getattr(memory_type_schema, "peer_enabled", True):
            operation.memory_fields.pop("peer_id", None)
            if self.allow_self:
                target_ids = [_SELF_PEER_ID]
            else:
                skip_code = MemoryOperationSkipCode.SELF_MEMORY_DISABLED
        elif operation.memory_fields.get("ranges") is not None:
            resolution = self._range_targets(
                operation.memory_fields.get("ranges"),
            )
            target_ids = resolution.target_ids
            skip_code = resolution.skip_code
            operation.memory_fields.pop("peer_id", None)
        else:
            resolution = self._resolve_operation_target(operation.memory_fields.get("peer_id"))
            target_ids = resolution.target_ids
            skip_code = resolution.skip_code
            if not target_ids and identity_resolution_skip is not None:
                skip_code = identity_resolution_skip.reason_code
            target_id = target_ids[0] if len(target_ids) == 1 else None
            if target_id == _SELF_PEER_ID:
                operation.memory_fields.pop("peer_id", None)
            elif target_id:
                operation.memory_fields["peer_id"] = target_id
            else:
                operation.memory_fields.pop("peer_id", None)

        if not target_ids:
            return self._skip_operation(
                operation,
                skip_code or MemoryOperationSkipCode.NO_WRITABLE_TARGET,
            )

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
