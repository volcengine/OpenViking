# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Due-slot scheduling for creator-owned Compile configurations.

The persisted next slot is authoritative; Redis wakes the shared execution path
and compensation scans recover missed notifications without changing manual run times.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from openviking.core.identifiers import validate_user_id
from openviking.crypto.encryptor import build_credential_encryptor as build_credential_encryptor
from openviking.server.identity import RequestContext, Role
from openviking.service.compile_targets import CompileStore, validate_compile_name
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    UnavailableError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

TZ = ZoneInfo("Asia/Shanghai")
COMPILE_TARGET_ITEM_TYPE = "compile_target"
COMPENSATION_SCAN_SECONDS = 6 * 3600.0
CREDENTIAL_PROVIDER = "encrypted_api_key"


def now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(TZ)).astimezone(TZ).isoformat()


def _period(schedule: Dict[str, Any]) -> timedelta:
    return timedelta(days=7 if schedule["frequency"] == "weekly" else 1)


def current_slot(schedule: Dict[str, Any], now: datetime) -> datetime:
    """Most recent Beijing-time trigger of ``schedule`` not after ``now``."""
    local = now.astimezone(TZ)
    slot = local.replace(hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0)
    if schedule["frequency"] == "weekly":
        slot -= timedelta(days=(local.isoweekday() - schedule["weekday"]) % 7)
    return slot - _period(schedule) if slot > local else slot


def next_slot(schedule: Dict[str, Any], now: datetime) -> datetime:
    return current_slot(schedule, now) + _period(schedule)


def is_due(record: Dict[str, Any], now: datetime) -> bool:
    """Return whether the saved schedule has an unclaimed slot at or before ``now``."""
    if not record.get("auto_update") or not record.get("next_triggered_slot"):
        return False
    if datetime.fromisoformat(record["next_triggered_slot"]) > now:
        return False
    slot = current_slot(record["update_schedule"], now)
    previous = record.get("last_triggered_slot")
    return previous is None or datetime.fromisoformat(previous) < slot


async def encrypt_credential(encryptor: Any, ctx: RequestContext) -> Dict[str, str]:
    from openviking.crypto.exceptions import ConfigError

    if not ctx.api_key:
        raise FailedPreconditionError(
            "Scheduled Compile requires an OpenViking API key",
            details={"reason": "COMPILE_CREDENTIAL_INVALID"},
        )
    plaintext = json.dumps({"api_key": ctx.api_key}).encode("utf-8")
    try:
        ciphertext = await encryptor.encrypt(ctx.account_id, plaintext)
    except (OSError, ConfigError) as exc:
        # e.g. the default ~/.openviking/master.key cannot be created or read.
        raise FailedPreconditionError(
            "Cannot load the credential encryption key; configure a readable and writable "
            f"encryption.local.key_file, or another encryption.provider: {exc}",
            details={"reason": "COMPILE_CREDENTIAL_KEY_UNAVAILABLE"},
        ) from exc
    return {"provider": CREDENTIAL_PROVIDER, "ciphertext": base64.b64encode(ciphertext).decode()}


class CompileScheduler:
    """Schedule creator-owned configurations independently of their mutable execution identity."""

    def __init__(
        self,
        compile_service: Any,
        store: CompileStore,
    ) -> None:
        self._compile = compile_service
        self._store = store
        self._expiry: Any = None
        self._identity_resolver: Any = None
        self._group_resolver: Any = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        # Installed by OpenVikingService; see build_credential_encryptor.
        self.credential_encryptor: Any = None

    # ---------- wiring ----------

    def set_identity_resolver(self, resolver: Any, group_resolver: Any) -> None:
        """Install async API-key identity and account/user group resolvers."""
        self._identity_resolver = resolver
        self._group_resolver = group_resolver

    async def save_credential(self, ctx: RequestContext) -> Dict[str, str]:
        """Return the encrypted request key; reject missing keys."""
        return await encrypt_credential(self.credential_encryptor, ctx)

    def install_expiry(self, scheduler: Any) -> None:
        """Register the redis backend handler; call before ExpiryRuntime.start_roles()."""
        from openviking.scheduling.expiry import HandlerOptions

        self._expiry = scheduler
        scheduler.register_handler(
            COMPILE_TARGET_ITEM_TYPE,
            _ExpiryHandler(self),
            options=HandlerOptions(concurrency=4, max_attempts=3, metadata_enabled=False),
        )

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except Exception:
                logger.exception("Compile scheduler scan failed")
            try:
                await asyncio.wait_for(self._stop.wait(), COMPENSATION_SCAN_SECONDS)
            except asyncio.TimeoutError:
                pass

    # ---------- discovery ----------

    async def scan_once(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(TZ)
        for account_id in await self._store.list_accounts():
            async for compile_name, record in self._store.iter_records(account_id):
                try:
                    owner_user_id = record["execution_context"]["owner_user_id"]
                    if is_due(record, now):
                        await self.trigger_if_due(account_id, owner_user_id, compile_name, now)
                    elif record["auto_update"]:
                        await self.reschedule(account_id, owner_user_id, compile_name, record)
                except Exception:
                    logger.exception("Compile schedule check failed for %s", compile_name)

    async def reschedule(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        record: Dict[str, Any],
        *,
        raise_on_error: bool = False,
    ) -> None:
        """Mirror the persisted slot in Redis without advancing it or changing execution state."""
        if self._expiry is None:
            if raise_on_error and record["auto_update"]:
                raise FailedPreconditionError(
                    "Scheduled Compile requires a configured Redis scheduler"
                )
            return
        from openviking.scheduling.expiry import ExpiryMode, ExpiryRegistration, ItemIdentity

        identity = ItemIdentity(COMPILE_TARGET_ITEM_TYPE, account_id, owner_user_id, compile_name)
        try:
            if record["auto_update"] and record["next_triggered_slot"]:
                due = datetime.fromisoformat(record["next_triggered_slot"])
                await self._expiry.report(
                    ExpiryRegistration(
                        identity=identity,
                        due_at_ms=int(due.timestamp() * 1000),
                        timeout_seconds=int(_period(record["update_schedule"]).total_seconds()),
                        mode=ExpiryMode.IDLE_ONESHOT,
                    )
                )
            else:
                await self._expiry.unregister(identity)
        except Exception as exc:
            logger.warning("Compile reschedule failed for %s", compile_name, exc_info=True)
            if raise_on_error:
                raise UnavailableError(
                    "compile scheduler", "Cannot register scheduled Compile"
                ) from exc

    async def trigger_if_due(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        now: Optional[datetime] = None,
    ) -> str:
        """Accept the latest due slot once through the same execution entry point as manual runs.

        The state lock covers submission and slot advancement. A failed submission leaves the
        slot pending. Durable task metadata recovers an interrupted accepted task even if
        the configuration's saved executor changes before the next delivery.
        """
        from openviking.service.compile_service import CompileRequest

        now = now or datetime.now(TZ)
        async with self._store.edit(account_id, owner_user_id, compile_name) as record:
            if not is_due(record, now):
                await self.reschedule(
                    account_id, owner_user_id, compile_name, record, raise_on_error=True
                )
                return "not_due"
            slot = current_slot(record["update_schedule"], now)
            slot_iso = slot.isoformat()
            if not await self._recover_slot(
                account_id, owner_user_id, compile_name, slot_iso, record
            ):
                ctx, connection = await self._executor(record)
                await self._compile.create(
                    CompileRequest(compile_name=compile_name),
                    ctx=ctx,
                    connection=connection,
                    idempotency_key=f"compile-schedule:{owner_user_id}:{compile_name}:{slot_iso}",
                    trigger="scheduled",
                    locked_record=record,
                    scheduled_slot=slot_iso,
                )
            record["last_triggered_slot"] = slot_iso
            record["next_triggered_slot"] = next_slot(record["update_schedule"], now).isoformat()
        await self.reschedule(account_id, owner_user_id, compile_name, record, raise_on_error=True)
        return "submitted"

    async def _recover_slot(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        slot: str,
        record: Dict[str, Any],
    ) -> bool:
        """Repair an accepted slot's history and dispatch under its original task owner.

        The caller holds the configuration lock. Shared task storage, rather than the
        current executor's idempotency namespace, identifies an accepted slot. Read or
        dispatch failures propagate so the caller cannot advance the slot or submit twice.
        """
        from openviking.service.task_tracker import get_task_tracker

        tasks = await get_task_tracker()._store.list_durable(account_id, task_type="compile")
        for task in tasks:
            control = task.meta.get("request", {}).get("compile") or {}
            if (
                control.get("compile_name") != compile_name
                or control.get("owner_user_id") != owner_user_id
                or control.get("scheduled_slot") != slot
                or control.get("trigger") != "scheduled"
            ):
                continue
            await self._compile.lifecycle.record_queued(task, locked_record=record)
            task.auth = await self._compile._tasks._task_auth(
                task.task_id, task.account_id, task.user_id
            )
            actor = await self._compile.lifecycle.task_ctx(task)
            await self._compile._tasks._resume_submission(task, actor)
            return True
        return False

    async def _executor(self, record: Dict[str, Any]) -> tuple[RequestContext, Dict[str, Any]]:
        """Verify the saved key and load the executor's current account groups.

        A user key must match the saved identity and supplies its current role.
        A root key acts with the saved executor role, defaulting to USER when absent.
        """
        context = record["execution_context"]
        executor, credential = context.get("executor"), context.get("credential")
        invalid = FailedPreconditionError(
            "Scheduled Compile API key is invalid; save the update settings with a valid key",
            details={"reason": "COMPILE_CREDENTIAL_INVALID"},
        )
        if not executor or not credential or self.credential_encryptor is None:
            raise invalid
        try:
            ciphertext = base64.b64decode(credential["ciphertext"], validate=True)
            plaintext = await self.credential_encryptor.decrypt(executor["account_id"], ciphertext)
            api_key = json.loads(plaintext.decode("utf-8"))["api_key"]
        except Exception as exc:
            raise invalid from exc
        role = Role.parse(executor.get("role", Role.USER))
        group_ids = tuple(executor.get("group_ids", ()))
        if self._identity_resolver is not None:
            try:
                identity = await self._identity_resolver(api_key)
            except Exception as exc:
                raise invalid from exc
            root_key = identity.role == Role.ROOT and identity.account_id is None
            if not root_key and (identity.account_id, identity.user_id) != (
                executor["account_id"],
                executor["user_id"],
            ):
                raise invalid
            if not root_key:
                role = identity.role
            group_ids = tuple(
                await self._group_resolver(executor["account_id"], executor["user_id"])
            )
        ctx = RequestContext(
            user=UserIdentifier(account_id=executor["account_id"], user_id=executor["user_id"]),
            role=role,
            group_ids=group_ids,
            api_key=api_key,
        )
        connection = {
            "api_key": api_key,
            "account_id": executor["account_id"],
            "user_id": executor["user_id"],
        }
        return ctx, connection


class _ExpiryHandler:
    def __init__(self, scheduler: CompileScheduler) -> None:
        self._scheduler = scheduler

    async def handle(self, request: Any) -> Any:
        """Trigger a saved name; missing configurations complete and submission failures retry."""
        from openviking.scheduling.expiry import CallbackResult, CallbackStatus

        identity = request.identity
        compile_name = identity.local_id
        try:
            validate_compile_name(compile_name)
            if error := validate_user_id(identity.user_id):
                raise InvalidArgumentError(error)
        except InvalidArgumentError:
            return CallbackResult(CallbackStatus.COMPLETED, "not_found")
        try:
            if (
                await self._scheduler._store.get(
                    identity.account_id, identity.user_id, compile_name
                )
                is None
            ):
                return CallbackResult(CallbackStatus.COMPLETED, "not_found")
            outcome = await self._scheduler.trigger_if_due(
                identity.account_id, identity.user_id, compile_name
            )
        except Exception:
            logger.warning("Compile schedule callback failed", exc_info=True)
            return CallbackResult(CallbackStatus.RETRY, "trigger_failed")
        return CallbackResult(CallbackStatus.COMPLETED, outcome)
