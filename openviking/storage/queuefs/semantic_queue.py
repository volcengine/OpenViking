# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticQueue: Semantic extraction queue."""

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

from .named_queue import NamedQueue
from .semantic_msg import SemanticMsg

logger = get_logger(__name__)


@dataclass
class _TrackedSemanticRequest:
    msg: SemanticMsg
    queue_msg_id: str
    follow_up_msg: Optional[SemanticMsg] = None


class SemanticQueue(NamedQueue):
    """Semantic extraction queue for async generation of .abstract.md and .overview.md."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._semantic_lock = threading.Lock()
        self._tracked_by_key: Dict[str, _TrackedSemanticRequest] = {}
        self._queue_id_to_key: Dict[str, str] = {}
        self._completed_request_at: Dict[str, float] = {}

    @staticmethod
    def _normalize_changes(changes: Optional[Dict[str, list]]) -> Dict[str, list]:
        normalized: Dict[str, list] = {"added": [], "modified": [], "deleted": []}
        if not changes:
            return normalized

        for key in normalized:
            values = changes.get(key) or []
            normalized[key] = sorted({str(value) for value in values})
        return normalized

    @classmethod
    def _logical_key(cls, msg: SemanticMsg) -> str:
        payload = {
            "uri": msg.uri,
            "context_type": msg.context_type,
            "recursive": msg.recursive,
            "account_id": msg.account_id,
            "user_id": msg.user_id,
            "agent_id": msg.agent_id,
            "role": msg.role,
            "skip_vectorization": msg.skip_vectorization,
            "telemetry_id": msg.telemetry_id or "",
            "target_uri": msg.target_uri or "",
            "lifecycle_lock_handle_id": msg.lifecycle_lock_handle_id or "",
            "is_code_repo": msg.is_code_repo,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _request_fingerprint(cls, msg: SemanticMsg) -> str:
        payload = {
            "logical_key": cls._logical_key(msg),
            "changes": cls._normalize_changes(msg.changes),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return digest.hexdigest()

    @classmethod
    def _merge_msgs(cls, base: SemanticMsg, incoming: SemanticMsg) -> SemanticMsg:
        merged = SemanticMsg.from_dict(base.to_dict())
        merged.changes = cls._normalize_changes(base.changes)
        incoming_changes = cls._normalize_changes(incoming.changes)

        if merged.changes == {"added": [], "modified": [], "deleted": []}:
            merged.changes = None

        if incoming_changes == {"added": [], "modified": [], "deleted": []}:
            return merged

        if merged.changes is None:
            merged.changes = incoming_changes
            return merged

        for key in ("added", "modified", "deleted"):
            merged_values = set(merged.changes.get(key, []))
            merged_values.update(incoming_changes.get(key, []))
            merged.changes[key] = sorted(merged_values)
        return merged

    def _cooldown_seconds(self) -> int:
        try:
            return max(0, int(get_openviking_config().semantic.summary_enqueue_cooldown_seconds))
        except Exception:
            return 0

    async def enqueue(self, msg: SemanticMsg, bypass_cooldown: bool = False) -> str:
        """Serialize SemanticMsg object and store in queue."""
        now = time.monotonic()
        logical_key = self._logical_key(msg)
        request_fingerprint = self._request_fingerprint(msg)
        cooldown_seconds = self._cooldown_seconds()

        with self._semantic_lock:
            last_completed_at = self._completed_request_at.get(request_fingerprint)
            if (
                not bypass_cooldown
                and cooldown_seconds > 0
                and last_completed_at is not None
                and now - last_completed_at < cooldown_seconds
            ):
                logger.info(
                    "Skipped semantic enqueue within cooldown: uri=%s context=%s",
                    msg.uri,
                    msg.context_type,
                )
                tracked = self._tracked_by_key.get(logical_key)
                return tracked.queue_msg_id if tracked else msg.id

            tracked = self._tracked_by_key.get(logical_key)
            if tracked:
                baseline_msg = tracked.follow_up_msg or tracked.msg
                merged_msg = self._merge_msgs(baseline_msg, msg)
                baseline_fingerprint = self._request_fingerprint(baseline_msg)
                merged_fingerprint = self._request_fingerprint(merged_msg)
                should_preserve_retry = msg.id == tracked.msg.id
                if should_preserve_retry:
                    if tracked.follow_up_msg is None:
                        tracked.follow_up_msg = SemanticMsg.from_dict(msg.to_dict())
                        logger.info(
                            "Preserved semantic re-enqueue for active request: uri=%s context=%s",
                            msg.uri,
                            msg.context_type,
                        )
                    else:
                        logger.info(
                            "Kept existing coalesced follow-up for active retry: uri=%s context=%s",
                            msg.uri,
                            msg.context_type,
                        )
                elif baseline_fingerprint != merged_fingerprint:
                    tracked.follow_up_msg = merged_msg
                    logger.info(
                        "Coalesced semantic enqueue while request is active: uri=%s context=%s",
                        msg.uri,
                        msg.context_type,
                    )
                else:
                    logger.debug(
                        "Deduped identical semantic enqueue while request is active: uri=%s context=%s",
                        msg.uri,
                        msg.context_type,
                    )
                return tracked.queue_msg_id

        queue_msg_id = await super().enqueue(msg.to_dict())

        with self._semantic_lock:
            self._tracked_by_key[logical_key] = _TrackedSemanticRequest(
                msg=SemanticMsg.from_dict(msg.to_dict()),
                queue_msg_id=queue_msg_id,
            )
            self._queue_id_to_key[queue_msg_id] = logical_key

        return queue_msg_id

    async def ack(self, msg_id: str) -> None:
        """Acknowledge successful processing and release tracked semantic state."""
        follow_up_msg: Optional[SemanticMsg] = None
        logical_key: Optional[str] = None
        tracked: Optional[_TrackedSemanticRequest] = None

        if not msg_id:
            return

        ack_file = f"{self.path}/ack"
        with self._semantic_lock:
            logical_key = self._queue_id_to_key.get(msg_id)
            if logical_key:
                tracked = self._tracked_by_key.get(logical_key)

        try:
            self._agfs.write(ack_file, msg_id.encode("utf-8"))
        except Exception as e:
            logger.warning(f"[SemanticQueue] Ack failed for {self.name} msg_id={msg_id}: {e}")
            return

        with self._semantic_lock:
            logical_key = self._queue_id_to_key.pop(msg_id, None)
            if logical_key:
                tracked = self._tracked_by_key.pop(logical_key, None)
                if tracked:
                    self._completed_request_at[self._request_fingerprint(tracked.msg)] = (
                        time.monotonic()
                    )
                    follow_up_msg = tracked.follow_up_msg

        if follow_up_msg is not None:
            await self.enqueue(follow_up_msg, bypass_cooldown=True)

    async def dequeue(self) -> Optional[SemanticMsg]:
        """Get message from queue and deserialize to SemanticMsg object."""
        data_dict = await super().dequeue()
        if not data_dict:
            return None

        if "data" in data_dict and isinstance(data_dict["data"], str):
            try:
                return SemanticMsg.from_json(data_dict["data"])
            except Exception as e:
                logger.debug(f"[SemanticQueue] Failed to parse message data: {e}")
                return None

        try:
            return SemanticMsg.from_dict(data_dict)
        except Exception as e:
            logger.debug(f"[SemanticQueue] Failed to create SemanticMsg from dict: {e}")
            return None

    async def peek(self) -> Optional[SemanticMsg]:
        """Peek at queue head message."""
        data_dict = await super().peek()
        if not data_dict:
            return None

        if "data" in data_dict and isinstance(data_dict["data"], str):
            try:
                return SemanticMsg.from_json(data_dict["data"])
            except Exception:
                return None

        try:
            return SemanticMsg.from_dict(data_dict)
        except Exception:
            return None

    async def clear(self) -> bool:
        """Clear queue data and reset semantic tracking state."""
        cleared = await super().clear()
        if cleared:
            with self._semantic_lock:
                self._tracked_by_key.clear()
                self._queue_id_to_key.clear()
                self._completed_request_at.clear()
        return cleared
