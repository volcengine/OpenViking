# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scheduled Compile: due-slot checks and task submission via Redis expiry.

``trigger_if_due`` owns the decision and submission under the target record
lock. The distributed ExpiryScheduler discovers candidates; a startup scan and
six-hour compensation scans repair registrations and recover missed triggers,
because the target record (not Redis) is the source of truth.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from openviking.server.identity import RequestContext, Role
from openviking.service.compile_targets import CompileStore, task_ctx
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    NotFoundError,
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


def next_run_at(record: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    if not record.get("auto_update"):
        return None
    return next_slot(record["update_schedule"], now or datetime.now(TZ)).isoformat()


def is_due(record: Dict[str, Any], slot: datetime) -> bool:
    def before(value: Optional[str]) -> bool:
        return value is None or datetime.fromisoformat(value) < slot

    enabled_at = record.get("auto_update_enabled_at")
    return (
        record.get("status") == "active"
        and bool(record.get("auto_update"))
        and enabled_at is not None
        and datetime.fromisoformat(enabled_at) < slot
        and before(record.get("last_triggered_slot"))
    )


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


def build_credential_encryptor(encryption_config: Any) -> Any:
    """Encryptor for saved API keys; unlike AGFS encryption, ignores ``encryption.enabled``."""
    from openviking.crypto.encryptor import FileEncryptor
    from openviking.crypto.providers import create_root_key_provider

    config = encryption_config.model_dump()
    return FileEncryptor(create_root_key_provider(config.get("provider", "local"), config))


class CompileScheduler:
    def __init__(
        self,
        compile_service: Any,
        store: CompileStore,
        viking_fs: Any,
    ) -> None:
        self._compile = compile_service
        self._store = store
        self._fs = viking_fs
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

    async def save_credential(self, ctx: RequestContext) -> tuple[Dict[str, Any], Dict[str, str]]:
        """Return ``(executor, credential)``; a trusted-mode root key is saved as ``key_type=root``."""
        key_type = "user"
        if self._identity_resolver is not None and ctx.api_key:
            try:
                identity = await self._identity_resolver(ctx.api_key)
            except Exception:
                identity = None
            if identity is not None and identity.role == Role.ROOT and identity.account_id is None:
                key_type = "root"
        executor = {"account_id": ctx.account_id, "user_id": ctx.user.user_id, "key_type": key_type}
        return executor, await encrypt_credential(self.credential_encryptor, ctx)

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
            for item in await self._store.list_summaries(account_id):
                try:
                    if item.get("auto_update") and is_due(
                        item, current_slot(item["update_schedule"], now)
                    ):
                        await self.trigger_if_due(account_id, item["to"], now)
                    elif item.get("auto_update"):
                        await self.reschedule(account_id, item)
                except Exception:
                    logger.exception("Compile schedule check failed for %s", item.get("to"))

    async def reschedule(
        self,
        account_id: str,
        record: Dict[str, Any],
        *,
        raise_on_error: bool = False,
    ) -> None:
        """Mirror the target URI's current schedule in the expiry scheduler.

        Strict callers receive 412 when enabling without Redis and 503 on registration failure.
        """
        if self._expiry is None:
            if raise_on_error and record.get("auto_update"):
                raise FailedPreconditionError(
                    "Scheduled Compile requires a configured Redis scheduler",
                    details={"reason": "COMPILE_SCHEDULER_DISABLED"},
                )
            return
        from openviking.scheduling.expiry import ExpiryMode, ExpiryRegistration, ItemIdentity

        identity = ItemIdentity(
            COMPILE_TARGET_ITEM_TYPE,
            account_id,
            "_system",
            record["to"],
        )
        try:
            if record.get("status") == "active" and record.get("auto_update"):
                schedule = record["update_schedule"]
                due = next_slot(schedule, datetime.now(TZ))
                await self._expiry.report(
                    ExpiryRegistration(
                        identity=identity,
                        due_at_ms=int(due.timestamp() * 1000),
                        timeout_seconds=int(_period(schedule).total_seconds()),
                        mode=ExpiryMode.IDLE_ONESHOT,
                    )
                )
            else:
                await self._expiry.unregister(identity)
        except Exception as exc:
            # The compensation scan repairs a missing or stale registration.
            logger.warning("Compile reschedule failed for %s", record["to"], exc_info=True)
            if raise_on_error:
                raise UnavailableError(
                    "compile scheduler", "Cannot register scheduled Compile; retry the request"
                ) from exc

    # ---------- decision and submission ----------

    async def trigger_if_due(self, account_id: str, to: str, now: Optional[datetime] = None) -> str:
        """Submit the URI's due slot once under its state lock.

        ``now`` defaults to Beijing time. Return submitted, disabled or not_due;
        missing targets raise LookupError. Saved slots prevent duplicate submissions.
        """
        to = to.rstrip("/")
        now = now or datetime.now(TZ)
        async with self._store.edit(account_id, to) as record:
            if record["to"] != to:
                raise LookupError(f"Compile target not found: {to}")
            slot = current_slot(record["update_schedule"], now) if record["auto_update"] else None
            if slot is None or not is_due(record, slot):
                await self.reschedule(account_id, record, raise_on_error=True)
                return "not_due"
            slot_iso = slot.isoformat()
            key = f"compile-schedule:{hashlib.sha256(to.encode('utf-8')).hexdigest()}:{slot_iso}"
            outcome = "submitted"
            try:
                ctx, connection = await self._executor(record)
                owner_ctx = task_ctx(account_id, ctx.user.user_id)
                # Initial failures can leave the output directory uncreated.
                if record.get("initialized") and not await self._fs.exists(
                    record["to"], ctx=owner_ctx
                ):
                    raise NotFoundError(record["to"], "compile target")
            except (FailedPreconditionError, NotFoundError) as exc:
                record["auto_update"] = False
                record["execution_context"]["credential"] = None
                record["last_error"] = (
                    f"Compile target is missing: {record['to']}"
                    if isinstance(exc, NotFoundError)
                    else exc.message
                )
                outcome = "disabled"
            else:
                try:
                    await self._compile.create_scheduled(
                        record, ctx=ctx, connection=connection, idempotency_key=key
                    )
                except ConflictError:
                    pass  # Same slot already submitted with older parameters.
            record["last_triggered_slot"] = slot_iso
        await self.reschedule(account_id, record, raise_on_error=True)
        return outcome

    async def _executor(self, record: Dict[str, Any]) -> tuple[RequestContext, Dict[str, Any]]:
        """Verify the saved key and load the executor's current account groups.

        A user key must resolve to the same user; a root key must still be the
        root key, and the run acts as the saved user (as trusted requests do).
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
        group_ids = ()
        if self._identity_resolver is not None:
            try:
                identity = await self._identity_resolver(api_key)
            except Exception as exc:
                raise invalid from exc
            root_key = identity.role == Role.ROOT and identity.account_id is None
            if root_key != (executor.get("key_type") == "root") or (
                not root_key
                and (identity.account_id, identity.user_id)
                != (executor["account_id"], executor["user_id"])
            ):
                raise invalid
            group_ids = tuple(
                await self._group_resolver(executor["account_id"], executor["user_id"])
            )
        ctx = RequestContext(
            user=UserIdentifier(account_id=executor["account_id"], user_id=executor["user_id"]),
            role=Role.USER,
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
        """Trigger the callback's target URI; missing targets complete and failures retry."""
        from openviking.scheduling.expiry import CallbackResult, CallbackStatus

        identity = request.identity
        to = identity.local_id
        if not isinstance(to, str) or not to:
            return CallbackResult(CallbackStatus.COMPLETED, "not_found")
        try:
            outcome = await self._scheduler.trigger_if_due(identity.account_id, to)
        except LookupError:
            return CallbackResult(CallbackStatus.COMPLETED, "not_found")
        except Exception:
            logger.warning("Compile schedule callback failed", exc_info=True)
            return CallbackResult(CallbackStatus.RETRY, "trigger_failed")
        return CallbackResult(CallbackStatus.COMPLETED, outcome)
