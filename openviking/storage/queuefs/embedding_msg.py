# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable, action-specific messages for the embedding queue."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union
from uuid import uuid4

from openviking.storage.index_action import FieldPatch, IndexAction

EmbeddingInput = Union[str, List[Dict[str, Any]]]


class IncompleteInitialRecordError(RuntimeError):
    """A missing record cannot be recreated from the queued seed."""

    def __init__(self, record_id: str, missing_fields: List[str]):
        self.record_id = record_id
        self.missing_fields = list(missing_fields)
        super().__init__(
            "update_fields could not recreate missing vector record: "
            f"record_id={record_id} missing_fields={self.missing_fields}"
        )


def missing_initial_record_fields(record: Mapping[str, Any]) -> List[str]:
    """Return fields required to create a usable vector record."""
    missing = [name for name in ("id", "uri", "account_id", "level") if record.get(name) is None]
    if not record.get("vector") and not record.get("sparse_vector"):
        missing.append("vector")
    return missing


@dataclass
class EmbedPayload:
    message: EmbeddingInput
    context_data: Dict[str, Any]
    field_patch: FieldPatch | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"type": "embed", "message": self.message, "context_data": self.context_data}
        if self.field_patch is not None:
            data["field_patch"] = self.field_patch.to_dict()
        return data


@dataclass
class UpdateFieldsPayload:
    record_id: str
    context_data: Dict[str, Any]
    field_patch: FieldPatch

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "update_fields",
            "record_id": self.record_id,
            "context_data": self.context_data,
            "field_patch": self.field_patch.to_dict(),
        }


@dataclass
class DeletePayload:
    record_ids: List[str]
    context_data: Dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "delete",
            "record_ids": list(self.record_ids),
            "context_data": self.context_data,
        }


@dataclass
class NoopPayload:
    context_data: Dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "noop", "context_data": self.context_data}


EmbeddingPayload = EmbedPayload | UpdateFieldsPayload | DeletePayload | NoopPayload
_PAYLOAD_FIELDS = {
    "embed": frozenset({"type", "message", "context_data", "field_patch"}),
    "update_fields": frozenset({"type", "record_id", "context_data", "field_patch"}),
    "delete": frozenset({"type", "record_ids", "context_data"}),
    "noop": frozenset({"type", "context_data"}),
}


@dataclass(init=False)
class EmbeddingMsg:
    """Stable queue envelope whose payload is determined by ``action``."""

    action: IndexAction
    payload: EmbeddingPayload
    id: str = field(default_factory=lambda: str(uuid4()))
    telemetry_id: str = ""
    queue_enqueued_at: float = 0.0

    def __init__(
        self,
        message: Optional[EmbeddingInput] = None,
        context_data: Optional[Dict[str, Any]] = None,
        telemetry_id: str = "",
        action: IndexAction | str = IndexAction.UPSERT,
        record_ids: Optional[List[str]] = None,
        update_fields: Optional[Dict[str, Any]] = None,
        field_modes: Optional[Dict[str, str]] = None,
        initial_fields: Optional[Dict[str, Any]] = None,
        queue_enqueued_at: float = 0.0,
        *,
        payload: EmbeddingPayload | None = None,
    ) -> None:
        self.id = str(uuid4())
        self.telemetry_id = telemetry_id
        self.queue_enqueued_at = max(float(queue_enqueued_at or 0.0), 0.0)
        self.action = IndexAction(action)
        if payload is not None:
            if any(
                value is not None
                for value in (
                    message,
                    context_data,
                    record_ids,
                    update_fields,
                    field_modes,
                    initial_fields,
                )
            ):
                raise ValueError("payload cannot be combined with legacy embedding fields")
            self.payload = payload
        else:
            self.payload = self._legacy_payload(
                message=message,
                context_data=dict(context_data or {}),
                record_ids=list(record_ids or []),
                update_fields=dict(update_fields or {}),
                field_modes=dict(field_modes or {}),
                initial_fields=dict(initial_fields or {}),
            )
        self._validate_payload()
        self._validate_account_context()

    def _validate_account_context(self) -> None:
        context_data = getattr(self.payload, "context_data", {})
        account_id = context_data.get("account_id") if isinstance(context_data, dict) else None
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("Embedding message requires account_id")

    def _legacy_payload(
        self,
        *,
        message: Optional[EmbeddingInput],
        context_data: Dict[str, Any],
        record_ids: List[str],
        update_fields: Dict[str, Any],
        field_modes: Dict[str, str],
        initial_fields: Dict[str, Any],
    ) -> EmbeddingPayload:
        patch = (
            FieldPatch(update_fields, field_modes, initial_fields)
            if update_fields or field_modes or initial_fields
            else None
        )
        if self.action is IndexAction.NONE:
            if message is not None or record_ids or patch is not None:
                raise ValueError("none action cannot carry work")
            return NoopPayload(context_data)
        if self.action is IndexAction.DELETE:
            if message is not None or patch is not None:
                raise ValueError("delete does not accept embedding or field-patch data")
            return DeletePayload(record_ids, context_data)
        if self.action is IndexAction.UPDATE_FIELDS:
            if message is not None or len(record_ids) != 1 or patch is None:
                raise ValueError("update_fields requires one record id and a field patch")
            return UpdateFieldsPayload(record_ids[0], context_data, patch)
        if not isinstance(message, (str, list)):
            raise ValueError(f"{self.action.value} requires an embedding message")
        if self.action is IndexAction.UPSERT:
            if field_modes:
                raise ValueError("upsert carries resolved fields and cannot use field modes")
            # Historical flat UPSERT messages allowed these fields but never
            # consumed them. Preserve queue-read compatibility while emitting
            # only the strict action-specific payload for new messages.
            return EmbedPayload(message, context_data)
        return EmbedPayload(message, context_data, patch)

    def _validate_payload(self) -> None:
        expected = {
            IndexAction.NONE: NoopPayload,
            IndexAction.UPSERT: EmbedPayload,
            IndexAction.MERGE: EmbedPayload,
            IndexAction.UPDATE_FIELDS: UpdateFieldsPayload,
            IndexAction.DELETE: DeletePayload,
        }[self.action]
        if not isinstance(self.payload, expected):
            raise ValueError(
                f"{self.action.value} requires {expected.__name__}, got {type(self.payload).__name__}"
            )
        if isinstance(self.payload, EmbedPayload) and not isinstance(
            self.payload.message, (str, list)
        ):
            raise ValueError(f"{self.action.value} requires an embedding message")
        if isinstance(self.payload, DeletePayload) and not self.payload.record_ids:
            raise ValueError("delete requires record_ids")
        if isinstance(self.payload, UpdateFieldsPayload):
            if not self.payload.record_id or not self.payload.field_patch.values:
                raise ValueError("update_fields requires one record id and a non-empty field patch")
        if (
            self.action is IndexAction.UPSERT
            and isinstance(self.payload, EmbedPayload)
            and self.payload.field_patch is not None
        ):
            raise ValueError("upsert does not accept a field patch")

    @classmethod
    def for_embed(
        cls,
        *,
        message: EmbeddingInput,
        context_data: Dict[str, Any],
        action: IndexAction | str = IndexAction.UPSERT,
        field_patch: FieldPatch | None = None,
        telemetry_id: str = "",
    ) -> "EmbeddingMsg":
        action = IndexAction(action)
        return cls(
            action=action,
            payload=EmbedPayload(message, context_data, field_patch),
            telemetry_id=telemetry_id,
        )

    @classmethod
    def for_update_fields(
        cls,
        *,
        record_id: str,
        context_data: Dict[str, Any],
        field_patch: FieldPatch | None = None,
        fields: Optional[Dict[str, Any]] = None,
        field_modes: Optional[Dict[str, str]] = None,
        initial_fields: Optional[Dict[str, Any]] = None,
        telemetry_id: str = "",
    ) -> "EmbeddingMsg":
        if field_patch is not None and any(
            value is not None for value in (fields, field_modes, initial_fields)
        ):
            raise ValueError(
                "field_patch cannot be combined with legacy fields, field_modes, or initial_fields"
            )
        resolved_patch = field_patch or FieldPatch(
            fields or {}, field_modes or {}, initial_fields or {}
        )
        return cls(
            action=IndexAction.UPDATE_FIELDS,
            payload=UpdateFieldsPayload(record_id, context_data, resolved_patch),
            telemetry_id=telemetry_id,
        )

    @classmethod
    def for_delete(
        cls,
        *,
        record_ids: List[str],
        context_data: Dict[str, Any],
        telemetry_id: str = "",
    ) -> "EmbeddingMsg":
        return cls(
            action=IndexAction.DELETE,
            payload=DeletePayload(record_ids, context_data),
            telemetry_id=telemetry_id,
        )

    @classmethod
    def noop(cls, *, context_data: Dict[str, Any], telemetry_id: str = "") -> "EmbeddingMsg":
        return cls(
            action=IndexAction.NONE,
            payload=NoopPayload(context_data),
            telemetry_id=telemetry_id,
        )

    @property
    def context_data(self) -> Dict[str, Any]:
        return self.payload.context_data

    @property
    def message(self) -> Optional[EmbeddingInput]:
        return self.payload.message if isinstance(self.payload, EmbedPayload) else None

    @property
    def record_ids(self) -> List[str]:
        if isinstance(self.payload, DeletePayload):
            return self.payload.record_ids
        if isinstance(self.payload, UpdateFieldsPayload):
            return [self.payload.record_id]
        return []

    @property
    def field_patch(self) -> FieldPatch | None:
        if isinstance(self.payload, (EmbedPayload, UpdateFieldsPayload)):
            return self.payload.field_patch
        return None

    @property
    def update_fields(self) -> Dict[str, Any]:
        return dict(self.field_patch.values) if self.field_patch is not None else {}

    @property
    def field_modes(self) -> Dict[str, str]:
        return dict(self.field_patch.modes) if self.field_patch is not None else {}

    @property
    def initial_fields(self) -> Dict[str, Any]:
        return dict(self.field_patch.seed_fields) if self.field_patch is not None else {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "telemetry_id": self.telemetry_id,
            "queue_enqueued_at": self.queue_enqueued_at,
            "action": self.action.value,
            "payload": self.payload.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmbeddingMsg":
        if not isinstance(data, dict):
            raise ValueError("embedding message must be an object")
        action = IndexAction(data.get("action", IndexAction.UPSERT.value))
        payload_data = data.get("payload")
        if isinstance(payload_data, dict):
            ambiguous = {
                name
                for name in (
                    "message",
                    "context_data",
                    "record_ids",
                    "update_fields",
                    "field_modes",
                    "initial_fields",
                )
                if name in data
            }
            if ambiguous:
                raise ValueError(
                    f"payload cannot be combined with legacy embedding fields: {sorted(ambiguous)}"
                )
            payload_type = payload_data.get("type")
            allowed_fields = _PAYLOAD_FIELDS.get(payload_type)
            if allowed_fields is None:
                raise ValueError(f"unsupported embedding payload type: {payload_type}")
            unknown_fields = set(payload_data) - allowed_fields
            if unknown_fields:
                raise ValueError(
                    f"{payload_type} payload contains unsupported fields: {sorted(unknown_fields)}"
                )
            context_data = dict(payload_data.get("context_data") or {})
            if payload_type == "embed":
                raw_patch = payload_data.get("field_patch")
                payload: EmbeddingPayload = EmbedPayload(
                    payload_data.get("message"),
                    context_data,
                    FieldPatch.from_dict(raw_patch) if isinstance(raw_patch, dict) else None,
                )
            elif payload_type == "update_fields":
                payload = UpdateFieldsPayload(
                    str(payload_data.get("record_id") or ""),
                    context_data,
                    FieldPatch.from_dict(dict(payload_data.get("field_patch") or {})),
                )
            elif payload_type == "delete":
                payload = DeletePayload(
                    [str(value) for value in payload_data.get("record_ids") or []],
                    context_data,
                )
            elif payload_type == "noop":
                payload = NoopPayload(context_data)
            obj = cls(
                action=action,
                payload=payload,
                telemetry_id=str(data.get("telemetry_id") or ""),
                queue_enqueued_at=data.get("queue_enqueued_at", 0.0),
            )
        else:
            obj = cls(
                message=data.get("message"),
                context_data=dict(data.get("context_data") or {}),
                telemetry_id=str(data.get("telemetry_id") or ""),
                action=action,
                record_ids=data.get("record_ids"),
                update_fields=data.get("update_fields"),
                field_modes=data.get("field_modes"),
                initial_fields=data.get("initial_fields"),
                queue_enqueued_at=data.get("queue_enqueued_at", 0.0),
            )
        obj.id = str(data.get("id") or obj.id)
        return obj

    @classmethod
    def from_json(cls, json_str: str) -> "EmbeddingMsg":
        try:
            return cls.from_dict(json.loads(json_str))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON string: {exc}") from exc


__all__ = [
    "DeletePayload",
    "EmbedPayload",
    "EmbeddingMsg",
    "IncompleteInitialRecordError",
    "NoopPayload",
    "UpdateFieldsPayload",
    "missing_initial_record_fields",
]
