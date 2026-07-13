# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable fencing ledger for Alice-owned OpenViking session writes.

The public session API predates distributed writer leases and intentionally remains
backwards compatible.  Alice uses the dedicated fenced API instead.  Every fenced
write is serialized on a stable, account/user-scoped AGFS sentinel and records both
the highest accepted fencing token and an operation receipt before acknowledging the
caller.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openviking.core.namespace import canonical_user_root
from openviking.server.identity import RequestContext
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction import LockContext, get_lock_manager
from openviking_cli.exceptions import NotFoundError, OpenVikingError, UnavailableError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

ALICE_FENCING_PROTOCOL = "openviking-alice-session-fencing"
ALICE_FENCING_VERSION = 2
ALICE_FENCING_MODE_ENV = "OPENVIKING_ALICE_FENCING_MODE"
_ALLOWED_MODES = frozenset({"observe", "required"})
_LEDGER_VERSION = 1
_MAX_FENCING_TOKEN = 9_007_199_254_740_991


def get_alice_fencing_mode() -> Literal["observe", "required"]:
    """Return the configured compatibility mode, rejecting unsafe typos."""
    raw = os.getenv(ALICE_FENCING_MODE_ENV, "observe").strip().lower()
    if raw not in _ALLOWED_MODES:
        raise RuntimeError(
            f"{ALICE_FENCING_MODE_ENV} must be one of observe|required, got {raw!r}"
        )
    return raw  # type: ignore[return-value]


class FencedOperationEnvelope(BaseModel):
    """Common, strictly validated identity for one durable writer operation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    writer: Literal["alice"]
    session_scope_id: str = Field(min_length=1, max_length=2048)
    turn_id: str = Field(min_length=1, max_length=256)
    operation_id: str = Field(min_length=1, max_length=256)
    fencing_token: int = Field(gt=0, le=_MAX_FENCING_TOKEN)

    @field_validator("session_scope_id", "turn_id", "operation_id")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("control characters are not allowed")
        return value


class FencedOperationConflict(OpenVikingError):
    """Structured 409 emitted by the fencing ledger."""

    def __init__(self, message: str, *, reason: str, details: Optional[dict[str, Any]] = None):
        conflict_details = {"reason": reason}
        if details:
            conflict_details.update(details)
        super().__init__(message, code="CONFLICT", details=conflict_details)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def operation_digest(
    operation: str,
    envelope: FencedOperationEnvelope,
    *,
    resource_id: str,
    actor_peer_id: Optional[str] = None,
) -> str:
    """Hash the immutable effect request, excluding the replaceable lease token."""
    # The operation ID is the stable idempotency map key.  W2 takeover changes
    # only the lease token; writer/scope/turn remain immutable and therefore
    # participate in the content digest.
    body = envelope.model_dump(
        mode="json",
        exclude={
            "operation_id",
            "fencing_token",
        },
    )
    encoded = _canonical_json(
        {
            "operation": operation,
            "resource_id": resource_id,
            "request": body,
            # This identity can change the effective peer_id even when the
            # request body omits peer_id, so it is part of effect identity.
            "actor_peer_id": actor_peer_id,
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_message_id(envelope: FencedOperationEnvelope, index: int = 0) -> str:
    """Derive an OpenViking message ID stable across retries and process crashes."""
    raw = "\x1f".join(
        (
            envelope.writer,
            envelope.session_scope_id,
            envelope.turn_id,
            envelope.operation_id,
            str(index),
        )
    )
    return f"msg_fenced_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:40]}"


def _record_metric(operation: str, outcome: str, latency_seconds: float) -> None:
    """Publish bounded fencing metrics without making correctness depend on metrics."""
    try:
        from openviking.metrics.datasources.session import SessionLifecycleDataSource

        SessionLifecycleDataSource.record_fencing(
            operation=operation,
            outcome=outcome,
            latency_seconds=max(0.0, latency_seconds),
        )
    except Exception:
        logger.debug("Failed to record fenced-session metric", exc_info=True)


# Test-only crash seam: tests replace this function with a one-shot exception to
# model process loss after an idempotent effect but before the ledger receipt.
async def after_fenced_effect_before_receipt(operation: str) -> None:
    del operation


# Test-only freeze seam immediately before the short PostgreSQL submit
# transaction.  No database lock or OpenViking effect is owned at this point.
async def after_fenced_submit_preflight(operation: str) -> None:
    del operation


# Synchronous test seam after PostgreSQL advisory-xact locks are held.  The
# connection-level idle-in-transaction timeout must release them even when the
# submitting process is SIGSTOP-frozen.
def after_fenced_submit_locks_acquired(operation: str) -> None:
    del operation


async def after_fenced_writer_claimed(operation_id: str) -> None:
    """Test seam: row is running, but AGFS has not been authorized/touched."""
    del operation_id


async def after_fenced_writer_effect_started(operation_id: str) -> None:
    """Test seam: durable single-writer boundary crossed; recovery is mandatory."""
    del operation_id


class FencedOperationLedger:
    """AGFS-backed high-watermark and idempotency ledger for one writer scope."""

    def __init__(self, viking_fs: Any, ctx: RequestContext, envelope: FencedOperationEnvelope):
        self._viking_fs = viking_fs
        self._ctx = ctx
        self._envelope = envelope
        scope_hash = hashlib.sha256(
            f"{envelope.writer}\x1f{envelope.session_scope_id}".encode("utf-8")
        ).hexdigest()
        self._ledger_uri = (
            f"{canonical_user_root(ctx)}/.alice-session-fencing/{scope_hash}.json"
        )
        self._ledger_path = viking_fs._uri_to_path(self._ledger_uri, ctx=ctx)

    def _session_binding_uri(self, resource_id: str) -> str:
        resource_hash = hashlib.sha256(resource_id.encode("utf-8")).hexdigest()
        return (
            f"{canonical_user_root(self._ctx)}/.alice-session-fencing/"
            f"session-bindings/{resource_hash}.json"
        )

    def _session_binding_path(self, resource_id: str) -> str:
        return self._viking_fs._uri_to_path(
            self._session_binding_uri(resource_id),
            ctx=self._ctx,
        )

    def _effect_receipt_uri(self, operation: str, resource_id: str) -> str:
        resource_hash = hashlib.sha256(resource_id.encode("utf-8")).hexdigest()
        operation_hash = hashlib.sha256(
            f"{operation}\x1f{self._envelope.operation_id}".encode("utf-8")
        ).hexdigest()
        return (
            f"{canonical_user_root(self._ctx)}/.alice-session-fencing/effects/"
            f"{resource_hash}/{operation_hash}.json"
        )

    async def load_effect_result(
        self, operation: str, resource_id: str
    ) -> Optional[dict[str, Any]]:
        """Read an effect-level receipt used to bridge callback/ledger crash gaps."""
        uri = self._effect_receipt_uri(operation, resource_id)
        try:
            raw = await self._viking_fs.read_file(uri, ctx=self._ctx)
        except NotFoundError:
            return None
        try:
            receipt = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Corrupt fenced-session effect receipt") from exc
        expected_digest = operation_digest(
            operation,
            self._envelope,
            resource_id=resource_id,
            actor_peer_id=self._ctx.actor_peer_id,
        )
        if not isinstance(receipt, dict):
            raise RuntimeError("Corrupt fenced-session effect receipt")
        if receipt.get("digest") != expected_digest:
            raise FencedOperationConflict(
                "Operation ID was already used for different content",
                reason="operation_digest_conflict",
                details={"operation_id": self._envelope.operation_id},
            )
        result = receipt.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Corrupt fenced-session effect result")
        return result

    async def save_effect_result(
        self,
        operation: str,
        resource_id: str,
        result: dict[str, Any],
    ) -> None:
        """Persist an idempotent effect result before returning from the callback."""
        uri = self._effect_receipt_uri(operation, resource_id)
        digest = operation_digest(
            operation,
            self._envelope,
            resource_id=resource_id,
            actor_peer_id=self._ctx.actor_peer_id,
        )
        await self._viking_fs.write_file(
            uri,
            _canonical_json(
                {
                    "version": _LEDGER_VERSION,
                    "operation_id": self._envelope.operation_id,
                    "digest": digest,
                    "result": result,
                    "completed_at": _utc_now(),
                }
            ),
            ctx=self._ctx,
        )

    async def _check_session_binding(self, resource_id: str) -> bool:
        """Validate a binding under the caller-held resource lock.

        Returns True when the binding must be published after the effect.  This
        avoids leaving a failed-operation placeholder that can permanently
        claim a session which was never successfully mutated.
        """
        binding_uri = self._session_binding_uri(resource_id)
        try:
            raw = await self._viking_fs.read_file(binding_uri, ctx=self._ctx)
        except NotFoundError:
            return True
        try:
            binding = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Corrupt fenced-session binding") from exc

        expected = {
            "version": _LEDGER_VERSION,
            "account_id": self._ctx.account_id,
            "user_id": self._ctx.user.user_id,
            "writer": self._envelope.writer,
            "session_scope_id": self._envelope.session_scope_id,
            "session_id": resource_id,
        }
        if not isinstance(binding, dict) or any(
            binding.get(key) != value for key, value in expected.items()
        ):
            raise FencedOperationConflict(
                "Session is already bound to a different writer scope",
                reason="session_scope_conflict",
                details={"session_id": resource_id},
            )
        return False

    async def _publish_session_binding(self, resource_id: str) -> None:
        expected = {
            "version": _LEDGER_VERSION,
            "account_id": self._ctx.account_id,
            "user_id": self._ctx.user.user_id,
            "writer": self._envelope.writer,
            "session_scope_id": self._envelope.session_scope_id,
            "session_id": resource_id,
            "created_at": _utc_now(),
        }
        await self._viking_fs.write_file(
            self._session_binding_uri(resource_id),
            _canonical_json(expected),
            ctx=self._ctx,
        )

    @staticmethod
    def _prune_old_receipts(
        operations: dict[str, Any],
        *,
        fencing_token: int,
        preserve_operation_id: str,
    ) -> None:
        """Keep receipts only for the active fence; older requests are now stale."""
        stale_ids = [
            operation_id
            for operation_id, receipt in operations.items()
            if operation_id != preserve_operation_id
            and int(receipt.get("fencing_token", 0) or 0) < fencing_token
        ]
        for operation_id in stale_ids:
            operations.pop(operation_id, None)

    async def _load(self) -> dict[str, Any]:
        try:
            raw = await self._viking_fs.read_file(self._ledger_uri, ctx=self._ctx)
        except NotFoundError:
            return {
                "version": _LEDGER_VERSION,
                "account_id": self._ctx.account_id,
                "user_id": self._ctx.user.user_id,
                "writer": self._envelope.writer,
                "session_scope_id": self._envelope.session_scope_id,
                "highest_fencing_token": 0,
                "active_turn_id": None,
                "operations": {},
                "closed_sessions": {},
                "updated_at": _utc_now(),
            }

        try:
            ledger = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Corrupt fenced-session ledger") from exc
        if not isinstance(ledger, dict) or ledger.get("version") != _LEDGER_VERSION:
            raise RuntimeError("Unsupported fenced-session ledger version")
        expected_identity = {
            "account_id": self._ctx.account_id,
            "user_id": self._ctx.user.user_id,
            "writer": self._envelope.writer,
            "session_scope_id": self._envelope.session_scope_id,
        }
        for key, expected in expected_identity.items():
            if ledger.get(key) != expected:
                raise RuntimeError(f"Fenced-session ledger identity mismatch: {key}")
        if not isinstance(ledger.get("operations"), dict):
            raise RuntimeError("Corrupt fenced-session operation map")
        ledger.setdefault("active_turn_id", None)
        ledger.setdefault("closed_sessions", {})
        if not isinstance(ledger.get("closed_sessions"), dict):
            raise RuntimeError("Corrupt fenced-session closure map")
        return ledger

    async def _save(self, ledger: dict[str, Any]) -> None:
        ledger["updated_at"] = _utc_now()
        await self._viking_fs.write_file(
            self._ledger_uri,
            _canonical_json(ledger),
            ctx=self._ctx,
        )

    async def execute(
        self,
        operation: str,
        resource_id: str,
        callback: Callable[[], Awaitable[dict[str, Any]]],
    ) -> tuple[dict[str, Any], bool]:
        """Execute or replay one effect while holding the durable writer-scope lock."""
        started = time.perf_counter()
        digest = operation_digest(
            operation,
            self._envelope,
            resource_id=resource_id,
            actor_peer_id=self._ctx.actor_peer_id,
        )
        outcome = "error"
        try:
            if get_alice_fencing_mode() == "required":
                # Required mode must never fall back to the expiring AGFS path
                # lock.  PostgreSQL owns the high watermark and keeps a
                # transaction/advisory lock alive across the external effect.
                from openviking.server.fenced_postgres import (
                    PostgresFencedOperationLedger,
                )

                result, replayed = await PostgresFencedOperationLedger(
                    self._ctx,
                    self._envelope,
                ).execute(operation, resource_id, callback)
                outcome = "duplicate" if replayed else "write"
                return result, replayed
            async with LockContext(
                get_lock_manager(),
                [self._ledger_path, self._session_binding_path(resource_id)],
                lock_mode="exact",
            ):
                ledger = await self._load()
                token = self._envelope.fencing_token
                highest = int(ledger.get("highest_fencing_token", 0) or 0)
                if token < highest:
                    outcome = "stale"
                    raise FencedOperationConflict(
                        "Fencing token is stale",
                        reason="stale_fence",
                        details={
                            "highest_fencing_token": highest,
                            "received_fencing_token": token,
                        },
                    )

                active_turn = ledger.get("active_turn_id")
                if (
                    token == highest
                    and highest > 0
                    and active_turn is not None
                    and active_turn != self._envelope.turn_id
                ):
                    outcome = "conflict"
                    raise FencedOperationConflict(
                        "The active fencing token is already bound to another turn",
                        reason="turn_fence_conflict",
                        details={"active_turn_id": active_turn},
                    )

                operations: dict[str, Any] = ledger["operations"]
                receipt = operations.get(self._envelope.operation_id)
                if receipt is not None:
                    if receipt.get("digest") != digest:
                        outcome = "conflict"
                        raise FencedOperationConflict(
                            "Operation ID was already used for different content",
                            reason="operation_digest_conflict",
                            details={"operation_id": self._envelope.operation_id},
                        )
                    if receipt.get("state") == "done":
                        if token > highest:
                            ledger["highest_fencing_token"] = token
                            receipt["fencing_token"] = token
                            self._prune_old_receipts(
                                operations,
                                fencing_token=token,
                                preserve_operation_id=self._envelope.operation_id,
                            )
                            await self._save(ledger)
                        outcome = "duplicate"
                        cached = receipt.get("result")
                        if not isinstance(cached, dict):
                            raise RuntimeError("Corrupt fenced-session cached result")
                        return cached, True

                closed_sessions: dict[str, Any] = ledger["closed_sessions"]
                closure = closed_sessions.get(resource_id)
                if isinstance(closure, dict) and closure.get("turn_id") == self._envelope.turn_id:
                    if (
                        operation == "commit"
                        and closure.get("operation_id") == self._envelope.operation_id
                    ):
                        if closure.get("digest") != digest:
                            outcome = "conflict"
                            raise FencedOperationConflict(
                                "Operation ID was already used for different content",
                                reason="operation_digest_conflict",
                                details={"operation_id": self._envelope.operation_id},
                            )
                        if not isinstance(closure.get("result"), dict):
                            raise RuntimeError("Corrupt fenced-session closure result")
                        outcome = "duplicate"
                        return dict(closure["result"]), True
                    if operation in {"message", "used", "commit"}:
                        outcome = "conflict"
                        raise FencedOperationConflict(
                            "The session is closed for this turn",
                            reason="session_turn_closed",
                            details={
                                "session_id": resource_id,
                                "turn_id": self._envelope.turn_id,
                            },
                        )
                else:
                    receipt = {
                        "operation": operation,
                        "turn_id": self._envelope.turn_id,
                        "digest": digest,
                        "fencing_token": token,
                        "state": "prepared",
                        "prepared_at": _utc_now(),
                    }
                    operations[self._envelope.operation_id] = receipt

                receipt["fencing_token"] = max(
                    int(receipt.get("fencing_token", 0) or 0), token
                )
                if token > highest:
                    self._prune_old_receipts(
                        operations,
                        fencing_token=token,
                        preserve_operation_id=self._envelope.operation_id,
                    )
                    if active_turn != self._envelope.turn_id:
                        closed_sessions.clear()

                # Advancing the high watermark and recording prepared are one locked,
                # durable transition and always happen before the external effect.
                ledger["highest_fencing_token"] = max(highest, token)
                ledger["active_turn_id"] = self._envelope.turn_id
                await self._save(ledger)

                binding_missing = await self._check_session_binding(resource_id)
                result = await callback()
                if not isinstance(result, dict):
                    raise TypeError("Fenced operation callback must return a dict")

                seam_result = after_fenced_effect_before_receipt(operation)
                if inspect.isawaitable(seam_result):
                    await seam_result

                if binding_missing:
                    await self._publish_session_binding(resource_id)

                receipt["state"] = "done"
                receipt["result"] = result
                receipt["completed_at"] = _utc_now()
                if operation == "commit":
                    closed_sessions[resource_id] = {
                        "turn_id": self._envelope.turn_id,
                        "fencing_token": token,
                        "operation_id": self._envelope.operation_id,
                        "digest": digest,
                        "result": result,
                        "closed_at": _utc_now(),
                    }
                await self._save(ledger)
                outcome = "write"
                return result, False
        except LockAcquisitionError as exc:
            outcome = "busy"
            raise UnavailableError(
                "fenced session ledger",
                "writer scope is busy; retry the same operation_id",
            ) from exc
        finally:
            _record_metric(operation, outcome, time.perf_counter() - started)
