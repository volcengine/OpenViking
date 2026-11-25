# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis read-through cache over the authoritative MySQL account store."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import weakref
from collections.abc import Awaitable, Callable, Coroutine
from threading import Lock
from typing import Any, TypeVar

from openviking.pyagfs.protocols import CacheRuntimeHandleProtocol
from openviking.server.account_stores.base import AccountStore
from openviking.server.account_stores.models import (
    AccountSummary,
    AuthSnapshot,
    DeletionRecord,
    GroupSummary,
    UsersPage,
    UserSummary,
)
from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.redis_runtime import (
    get_shared_redis_runtime,
    wait_redis_cleanup,
)
from openviking.server.identity import Role
from openviking_cli.exceptions import InvalidArgumentError, UnavailableError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.cache_config import validate_cache_instance_name
from openviking_cli.utils.config.config_utils import normalize_service_name

logger = get_logger(__name__)
T = TypeVar("T")

# All script keys share the stable account hash tag. Generation values are
# random, persistent tokens; only cached data expires. READ derives its data
# key inside the script so a bump cannot race between generation and hit reads.
_READ_GENERATIONS_SCRIPT = """
-- read-generations
local account = redis.call('GET', KEYS[1])
local user = ''
if ARGV[3] == '1' then
    user = redis.call('GET', KEYS[2])
end
if not account or not user then
    -- Unverified credential hints must never allocate permanent metadata.
    if ARGV[4] ~= '1' then
        return {account or '', user or '', false}
    end
    if not account then
        account = ARGV[1]
        redis.call('SET', KEYS[1], account)
    end
    if not user then
        user = ARGV[2]
        redis.call('SET', KEYS[2], user)
    end
end
local key = KEYS[3] .. ':g:' .. account .. ':' .. user
return {account, user, redis.call('GET', key)}
"""

_SET_IF_GENERATIONS_SCRIPT = """
-- set-if-generations
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
if ARGV[3] == '1' and redis.call('GET', KEYS[2]) ~= ARGV[2] then
    return 0
end
redis.call('SET', KEYS[3], ARGV[4], 'EX', ARGV[5])
return 1
"""

_BUMP_GENERATION_SCRIPT = """
-- bump-generation
-- SET also removes any legacy TTL, and must run even if the key is absent.
redis.call('SET', KEYS[1], ARGV[1])
return 1
"""

_ACCOUNT_STORE_SCRIPTS = (
    ("accountstore.v2.read_generations", _READ_GENERATIONS_SCRIPT),
    ("accountstore.v2.set_if_generations", _SET_IF_GENERATIONS_SCRIPT),
    ("accountstore.v2.bump_generation", _BUMP_GENERATION_SCRIPT),
)
_script_registration_lock = Lock()
_registered_script_runtimes: dict[int, weakref.ReferenceType[Any]] = {}
_registered_native_runtime_ids: set[int] = set()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _key_part(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode(raw: str | bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _register_runtime_scripts(cache_runtime: CacheRuntimeHandleProtocol) -> None:
    """Register Account Store scripts once for each CacheRuntime handle."""
    runtime_id = id(cache_runtime)
    with _script_registration_lock:
        try:
            reference = weakref.ref(cache_runtime)
        except TypeError:
            # ponytail: PyO3 handles lack weakrefs; AsyncAGFSClient retains each
            # handle, so process-lifetime ids stay stable until runtime replacement exists.
            if runtime_id in _registered_native_runtime_ids:
                return
            _registered_native_runtime_ids.add(runtime_id)
        else:
            if _registered_script_runtimes.get(runtime_id, lambda: None)() is cache_runtime:
                return
            _registered_script_runtimes[runtime_id] = reference
        for script_id, script in _ACCOUNT_STORE_SCRIPTS:
            cache_runtime.register_script(script_id, script)


class RedisMySQLAccountStore(AccountStore):
    """Cache hot account reads while preserving MySQL as the authority.

    Persistent account/user generations reject obsolete fills after a successful
    bump. MySQL commits precede best-effort Redis invalidation, so this is not a
    strongly consistent cache: failed invalidation retains fixed-TTL staleness.
    Local fills share tasks on a private loop, without Redis locks.

    The fixed TTL starts at fill time, not mutation time. Rollout requires every
    writer to use this v2 generation protocol: v1 entries are ignored, but v1
    writers cannot invalidate v2 readers. Upgrade all writers before relying on
    generation invalidation; mixed-version deployment offers only TTL expiry.

    Auth snapshots validate cold hints in one aggregate MySQL read, then only
    initialize generations. A later read fills the payload using captured tokens;
    the preverification result can never be published under newer generations.

    """

    def __init__(
        self,
        *,
        service_name: str,
        params: dict[str, object] | None = None,
        mysql_store: MySQLAccountStore | None = None,
        cache_runtime: CacheRuntimeHandleProtocol | None = None,
        bootstrap_default_account: bool = True,
    ) -> None:
        try:
            normalized_service_name = normalize_service_name(service_name)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None
        values = dict(params or {})
        mysql_params = values.pop("mysql", None)
        cache_spec = values.pop("cache", None)
        if values:
            names = ", ".join(sorted(values))
            raise InvalidArgumentError(f"Unknown Redis MySQL account store parameters: {names}")
        if mysql_store is None:
            if not isinstance(mysql_params, dict):
                raise InvalidArgumentError("redis_mysql account storage requires mysql parameters")
            mysql_store = MySQLAccountStore(
                service_name=normalized_service_name,
                params=mysql_params,
                bootstrap_default_account=bootstrap_default_account,
            )
        elif mysql_store._service_name != normalized_service_name:
            raise InvalidArgumentError(
                "Redis and MySQL account stores must use the same service_name"
            )
        self.cache_instance_from_params({"cache": cache_spec})
        if cache_runtime is None:
            raise InvalidArgumentError("redis_mysql account storage requires CacheRuntime")
        self._key_prefix = "ov:account-cache"
        runtime_ttl = cache_runtime.default_ttl_seconds
        if (
            isinstance(runtime_ttl, bool)
            or not isinstance(runtime_ttl, (int, float))
            or runtime_ttl <= 0
            or (isinstance(runtime_ttl, float) and not runtime_ttl.is_integer())
        ):
            raise InvalidArgumentError(
                "CacheRuntime default_ttl_seconds must be a positive integer"
            )

        self._mysql = mysql_store
        self._service_name = normalized_service_name
        self._cache_runtime = cache_runtime
        self._ttl_seconds = int(runtime_ttl)
        self._redis_runtime = get_shared_redis_runtime()
        self._redis_lease = False
        self._closing = False
        # Accessed exclusively on RedisRuntime's loop. Missing generations use
        # empty tokens; cold reads still retain any existing account/user version.
        self._fill_tasks: dict[tuple[str, str, str], asyncio.Task[Any]] = {}
        self._mysql_tasks: set[asyncio.Task[Any]] = set()
        self._redis_lifecycle_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
        _register_runtime_scripts(self._cache_runtime)

    @staticmethod
    def cache_instance_from_params(params: dict[str, object] | None) -> str:
        """Return the sole CacheRuntime instance selected by Redis MySQL settings."""
        cache = dict(params or {}).get("cache")
        if not isinstance(cache, dict):
            raise InvalidArgumentError("redis_mysql account storage requires cache.instance")
        unknown = set(cache) - {"instance"}
        if unknown:
            raise InvalidArgumentError(
                f"Unknown Redis MySQL account store cache parameters: {', '.join(sorted(unknown))}"
            )
        instance = cache.get("instance")
        if not isinstance(instance, str) or not instance:
            raise InvalidArgumentError("redis_mysql account storage requires cache.instance")
        try:
            return validate_cache_instance_name(
                instance,
                "redis_mysql account storage cache.instance",
            )
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None

    async def load(self) -> None:
        await self._run_lifecycle(self._load_store)

    async def close(self) -> None:
        await wait_redis_cleanup(self._run_lifecycle(self._close_store))

    async def _run_lifecycle(self, operation: Callable[[], Awaitable[None]]) -> None:
        async def invoke() -> None:
            loop = asyncio.get_running_loop()
            lifecycle = self._redis_lifecycle_lock
            if lifecycle is None or lifecycle[0] is not loop:
                lifecycle = (loop, asyncio.Lock())
                self._redis_lifecycle_lock = lifecycle
            async with lifecycle[1]:
                await operation()

        await self._redis_runtime.run(invoke)

    async def _load_store(self) -> None:
        try:
            self._closing = False
            await self._mysql.load()
            if not self._redis_lease:
                # Both steps run on the Redis loop without a cancellation point.
                await self._redis_runtime.acquire()
                self._redis_lease = True
            await self._cache_runtime.ping()
        except BaseException:
            # Keep the lifecycle lock until failed-load cleanup has completed.
            await wait_redis_cleanup(self._close_store())
            raise

    async def _close_store(self) -> None:
        self._closing = True
        try:
            # Shielded fills may outlive every waiter. Settle their MySQL/Redis
            # cancellation before releasing the client, loop lease, or MySQL.
            # Track all MySQL reads, including uncached reads and fill winners.
            # Never cancel the task performing close itself if close is called
            # inside a loader.
            current = asyncio.current_task()
            tasks = (set(self._fill_tasks.values()) | self._mysql_tasks) - {current}
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._fill_tasks.clear()
            self._mysql_tasks.difference_update(tasks)
        finally:
            await self._close_resources()

    async def _close_resources(self) -> None:
        try:
            if self._redis_lease:
                self._redis_lease = False
                await self._redis_runtime.release()
        finally:
            await self._mysql.close()

    async def _execute_script(
        self, script_id: str, keys: list[str], args: list[bytes]
    ) -> Any:
        async def invoke() -> Any:
            return await self._cache_runtime.execute_script(script_id, keys, args)

        return await self._redis_runtime.run(invoke)

    def _account_tag(self, account_id: str) -> str:
        return _key_part(f"{self._service_name}\0{account_id}")

    def _key(self, account_id: str, suffix: str) -> str:
        return f"{self._key_prefix}:v2:{{{self._account_tag(account_id)}}}:{suffix}"

    def _account_generation_key(self, account_id: str) -> str:
        return self._key(account_id, "generation:account")

    def _user_generation_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"generation:user:{_key_part(user_id)}")

    def _generation_keys(self, account_id: str, user_id: str | None) -> tuple[str, str]:
        account = self._account_generation_key(account_id)
        return account, account if user_id is None else self._user_generation_key(
            account_id, user_id
        )

    @staticmethod
    def _generation_cache_key(key: str, account_generation: str, user_generation: str) -> str:
        return f"{key}:g:{account_generation}:{user_generation}"

    def _user_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"user:{_key_part(user_id)}")

    def _group_ids_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"groups:{_key_part(user_id)}")

    def _account_deletion_key(self, account_id: str) -> str:
        return self._key(account_id, "deletion:account")

    def _user_deletion_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"deletion:user:{_key_part(user_id)}")

    def _auth_snapshot_key(self, account_id: str, api_key: str, user_id: str) -> str:
        digest_input = self._service_name + "\0" + api_key
        return self._key(account_id, f"auth_snapshot:{_key_part(user_id)}:{_digest(digest_input)}")

    @staticmethod
    def _normalize_auth_snapshot(
        value: Any, account_id: str | None, user_id: str | None
    ) -> AuthSnapshot | None:
        if not isinstance(value, dict):
            return None
        if any(
            not isinstance(value.get(field), str) for field in ("account_id", "user_id", "role")
        ):
            return None
        if not value["account_id"] or not value["user_id"]:
            return None
        try:
            role = Role.parse(value["role"])
        except ValueError:
            return None
        if role == Role.ROOT:
            return None
        if account_id is not None and value["account_id"] != account_id:
            return None
        if user_id is not None and value["user_id"] != user_id:
            return None
        groups = value.get("group_ids")
        if not isinstance(groups, (list, tuple)) or any(
            not isinstance(group, str) for group in groups
        ):
            return None
        return AuthSnapshot(
            account_id=value["account_id"],
            user_id=value["user_id"],
            role=role,
            group_ids=tuple(groups),
        )

    @staticmethod
    def _nullable_payload(value: Any) -> dict[str, Any]:
        return {"found": value is not None, "value": value}

    @staticmethod
    def _read_nullable(payload: dict[str, Any] | None) -> tuple[bool, Any]:
        if payload is None or not isinstance(payload.get("found"), bool):
            return False, None
        if not payload["found"]:
            return True, None
        if "value" not in payload:
            return False, None
        return True, payload["value"]

    async def _read_generations(
        self, account_id: str, user_id: str | None, key: str, *, initialize: bool = True
    ) -> tuple[str, str, dict[str, Any] | None]:
        """Atomically read a hit; read-only mode preserves partially missing generations."""
        result = await self._execute_script(
            "accountstore.v2.read_generations",
            [*self._generation_keys(account_id, user_id), key],
            [
                secrets.token_hex(24).encode(),
                secrets.token_hex(24).encode(),
                str(int(user_id is not None)).encode(),
                str(int(initialize)).encode(),
            ],
        )
        if not isinstance(result, (list, tuple)) or len(result) != 3:
            raise ValueError("Invalid Redis generation read response")
        account_generation, user_generation, raw = result
        if isinstance(account_generation, bytes):
            account_generation = account_generation.decode("ascii")
        if isinstance(user_generation, bytes):
            user_generation = user_generation.decode("ascii")
        if (
            not isinstance(account_generation, str)
            or not isinstance(user_generation, str)
            or (
                initialize
                and (not account_generation or (user_id is not None and not user_generation))
            )
        ):
            raise ValueError("Invalid Redis generations")
        return account_generation, user_generation, _decode(raw)

    async def _set_if_generations(
        self,
        account_id: str,
        user_id: str | None,
        key: str,
        account_generation: str,
        user_generation: str,
        value: dict[str, Any],
    ) -> bool:
        return bool(
            await self._execute_script(
                "accountstore.v2.set_if_generations",
                [
                    *self._generation_keys(account_id, user_id),
                    self._generation_cache_key(key, account_generation, user_generation),
                ],
                [
                    account_generation.encode(),
                    user_generation.encode(),
                    str(int(user_id is not None)).encode(),
                    json.dumps(value, separators=(",", ":")).encode(),
                    str(self._ttl_seconds).encode(),
                ],
            )
        )

    async def _bump_generation(self, account_id: str, user_id: str | None = None) -> None:
        key = (
            self._account_generation_key(account_id)
            if user_id is None
            else self._user_generation_key(account_id, user_id)
        )
        await self._execute_script(
            "accountstore.v2.bump_generation",
            [key],
            [secrets.token_hex(24).encode()],
        )

    async def _read_mysql(self, loader: Callable[[], Awaitable[T]]) -> T:
        """Track all MySQL reads; leave backpressure to the real MySQL pool."""
        return await self._redis_runtime.run(lambda: self._read_mysql_on_loop(loader))

    async def _read_mysql_on_loop(self, loader: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise UnavailableError("account storage", "Account store is closing")
        task = asyncio.current_task()
        assert task is not None
        own_task = task not in self._mysql_tasks
        if own_task:
            self._mysql_tasks.add(task)
        try:
            return await loader()
        finally:
            # Nested loads on one task must not retire the outer operation.
            if own_task:
                self._mysql_tasks.discard(task)

    async def _read_through(
        self,
        account_id: str,
        key: str,
        loader: Callable[[], Awaitable[T]],
        *,
        user_id: str | None = None,
        verify_cold_hints: bool = False,
        read_payload: Callable[[dict[str, Any] | None], tuple[bool, Any]] | None = None,
        make_payload: Callable[[Any], dict[str, Any]] | None = None,
    ) -> T:
        read_payload = read_payload or self._read_nullable
        make_payload = make_payload or self._nullable_payload

        async def read_on_loop() -> T:
            if self._closing:
                raise UnavailableError("account storage", "Account store is closing")
            try:
                account_generation, user_generation, payload = await self._read_generations(
                    account_id, user_id, key, initialize=not verify_cold_hints
                )
                hit, value = read_payload(payload)
                if hit:
                    return value
            except Exception as exc:
                logger.info(
                    "Redis account cache read failed; reading from MySQL",
                    extra={"error_type": type(exc).__name__},
                )
                # Redis read failure cannot authorize a cache write.
                return await self._read_mysql(loader)

            if not account_generation or (user_id is not None and not user_generation):
                # Only auth snapshots read without initializing cold hints.
                # The shared cold task owns verification and initialization, so
                # individual cancellation cannot multiply queries for waiters.
                return await self._singleflight(
                    (key, account_generation, user_generation),
                    lambda: self._read_cold_auth_snapshot(
                        account_id,
                        user_id,
                        key,
                        loader,
                    ),
                )

            return await self._singleflight(
                (key, account_generation, user_generation),
                lambda: self._fill_cache(
                    account_id,
                    user_id,
                    key,
                    account_generation,
                    user_generation,
                    loader,
                    make_payload=make_payload,
                ),
            )

        return await self._redis_runtime.run(read_on_loop)

    async def _singleflight(
        self, identity: tuple[str, str, str], operation: Callable[[], Coroutine[Any, Any, T]]
    ) -> T:
        """Share one owned task on the Redis loop and retire every completion."""
        if self._closing:
            raise UnavailableError("account storage", "Account store is closing")
        task = self._fill_tasks.get(identity)
        if task is None:
            task = asyncio.create_task(operation())
            self._fill_tasks[identity] = task

            def finished(completed: asyncio.Task[Any]) -> None:
                if self._fill_tasks.get(identity) is completed:
                    del self._fill_tasks[identity]
                # Retrieve failures even if all individual waiters cancelled.
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _read_cold_auth_snapshot(
        self,
        account_id: str,
        user_id: str | None,
        key: str,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        verified = await self._read_mysql(loader)
        if verified is None:
            return verified
        # Only initialize tokens after an authoritative positive read. A
        # mutation can have bumped them during that read; never publish the
        # earlier payload under these newly captured generations.
        try:
            await self._read_generations(account_id, user_id, key)
        except Exception as exc:
            logger.info(
                "Redis snapshot initialization failed; using MySQL result",
                extra={"error_type": type(exc).__name__},
            )
        return verified

    async def _fill_cache(
        self,
        account_id: str,
        user_id: str | None,
        key: str,
        account_generation: str,
        user_generation: str,
        loader: Callable[[], Awaitable[T]],
        *,
        make_payload: Callable[[Any], dict[str, Any]] | None = None,
    ) -> T:
        make_payload = make_payload or self._nullable_payload
        # Late MISS responses may start another query after a flight retires.
        # Always fill against the original tokens, never a newer generation.
        value = await self._read_mysql(loader)
        try:
            await self._set_if_generations(
                account_id,
                user_id,
                key,
                account_generation,
                user_generation,
                make_payload(value),
            )
        except Exception as exc:
            logger.info(
                "Redis account cache population failed; using MySQL result",
                extra={"error_type": type(exc).__name__},
            )
        return value

    async def _mutate_account(
        self,
        account_id: str,
        operation: Callable[[], Awaitable[T]],
        *,
        user_id: str | None = None,
    ) -> T:
        try:
            return await operation()
        finally:
            # Cancellation/failure can occur after a MySQL commit. Conservatively
            # bump on every attempted mutation and settle cleanup before returning.
            try:
                await wait_redis_cleanup(self._bump_generation(account_id, user_id))
            except Exception as exc:
                logger.error(
                    "Redis account cache generation update failed",
                    extra={"account_id": account_id, "error_type": type(exc).__name__},
                )

    async def verify_api_key(
        self,
        api_key: str,
        *,
        account_id_hint: str | None = None,
        user_id_hint: str | None = None,
    ) -> tuple[str, str] | None:
        async def verify_in_mysql() -> tuple[str, str] | None:
            return await self._mysql.verify_api_key(
                api_key, account_id_hint=account_id_hint, user_id_hint=user_id_hint
            )

        return await self._read_mysql(verify_in_mysql)

    async def get_auth_snapshot(
        self,
        api_key: str,
        *,
        account_id_hint: str | None = None,
        user_id_hint: str | None = None,
    ) -> AuthSnapshot | None:
        async def load_snapshot() -> AuthSnapshot | None:
            value = await self._mysql.get_auth_snapshot(
                api_key, account_id_hint=account_id_hint, user_id_hint=user_id_hint
            )
            return self._normalize_auth_snapshot(value, account_id_hint, user_id_hint)

        if account_id_hint is None or user_id_hint is None:
            return await self._read_mysql(load_snapshot)

        credential_digest = _digest(self._service_name + "\0" + api_key)

        def make_payload(value: Any) -> dict[str, Any]:
            return {**self._nullable_payload(value), "credential_digest": credential_digest}

        def read_payload(payload: dict[str, Any] | None) -> tuple[bool, Any]:
            if payload is None or payload.get("credential_digest") != credential_digest:
                return False, None
            hit, value = self._read_nullable(payload)
            if not hit:
                return False, None
            if payload["found"] is False:
                return (
                    (True, None)
                    if "value" in payload and payload["value"] is None
                    else (False, None)
                )
            if value is None:
                return False, None
            snapshot = self._normalize_auth_snapshot(value, account_id_hint, user_id_hint)
            return (True, snapshot) if snapshot is not None else (False, None)

        return await self._read_through(
            account_id_hint,
            self._auth_snapshot_key(account_id_hint, api_key, user_id_hint),
            load_snapshot,
            user_id=user_id_hint,
            verify_cold_hints=True,
            read_payload=read_payload,
            make_payload=make_payload,
        )

    async def get_user(self, account_id: str, user_id: str) -> UserSummary | None:
        value = await self._read_through(
            account_id,
            self._user_key(account_id, user_id),
            lambda: self._mysql.get_user(account_id, user_id),
            user_id=user_id,
        )
        return value if value is None else UserSummary(value)

    async def get_user_membership(self, account_id: str, user_id: str) -> UserSummary | None:
        # Bypass the read-through cache: the admin membership probe must reflect
        # the latest persisted state, which MySQL indexes directly.
        return await self._read_mysql(lambda: self._mysql.get_user_membership(account_id, user_id))

    async def get_user_group_ids(self, account_id: str, user_id: str) -> tuple[str, ...]:
        value = await self._read_through(
            account_id,
            self._group_ids_key(account_id, user_id),
            lambda: self._mysql.get_user_group_ids(account_id, user_id),
            user_id=user_id,
        )
        return tuple(value or ())

    async def get_deletion(
        self, account_id: str, user_id: str | None = None
    ) -> DeletionRecord | None:
        account_deletion = await self._read_through(
            account_id,
            self._account_deletion_key(account_id),
            lambda: self._mysql.get_deletion(account_id),
        )
        if account_deletion is not None or user_id is None:
            return account_deletion
        value = await self._read_through(
            account_id,
            self._user_deletion_key(account_id, user_id),
            lambda: self._mysql.get_deletion(account_id, user_id),
            user_id=user_id,
        )
        return value

    async def iter_deletions(self) -> list[tuple[str, str | None, DeletionRecord]]:
        return await self._read_mysql(lambda: self._mysql.iter_deletions())

    async def get_account(self, account_id: str) -> AccountSummary | None:
        return await self._read_mysql(lambda: self._mysql.get_account(account_id))

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
        *,
        lease_ref: object | None = None,
    ) -> None:
        del lease_ref
        await self._mutate_account(
            account_id,
            lambda: self._mysql.create_account(account_id, admin_user_id),
        )

    async def create_account_with_api_key(
        self,
        account_id: str,
        admin_user_id: str,
        api_key: str,
        *,
        lease_ref: object | None = None,
    ) -> None:
        del lease_ref
        await self._mutate_account(
            account_id,
            lambda: self._mysql.create_account_with_api_key(
                account_id,
                admin_user_id,
                api_key,
            ),
        )

    async def delete_account(self, account_id: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.delete_account(account_id),
        )

    async def list_accounts(self, **kwargs) -> list[AccountSummary]:
        return await self._read_mysql(lambda: self._mysql.list_accounts(**kwargs))

    async def create_user(self, account_id: str, user_id: str, role: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.create_user(account_id, user_id, role),
            user_id=user_id,
        )

    async def create_user_with_api_key(
        self,
        account_id: str,
        user_id: str,
        role: str,
        api_key: str,
    ) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.create_user_with_api_key(
                account_id,
                user_id,
                role,
                api_key,
            ),
            user_id=user_id,
        )

    async def list_users_page(self, account_id: str, **kwargs) -> UsersPage:
        return await self._read_mysql(lambda: self._mysql.list_users_page(account_id, **kwargs))

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.set_role(account_id, user_id, role),
            user_id=user_id,
        )

    async def create_group(self, account_id: str, group_id: str) -> GroupSummary:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.create_group(account_id, group_id),
        )

    async def get_groups(self, account_id: str) -> list[GroupSummary]:
        return await self._read_mysql(lambda: self._mysql.get_groups(account_id))

    async def get_group_members(self, account_id: str, group_id: str) -> list[str]:
        return await self._read_mysql(lambda: self._mysql.get_group_members(account_id, group_id))

    async def add_group_members(self, account_id: str, group_id: str, user_ids: list[str]) -> bool:
        user_ids = list(dict.fromkeys(user_ids))
        # One account bump invalidates snapshots and memberships for the whole batch.
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.add_group_members(account_id, group_id, user_ids),
        )

    async def remove_group_members(
        self, account_id: str, group_id: str, user_ids: list[str]
    ) -> bool:
        user_ids = list(dict.fromkeys(user_ids))
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.remove_group_members(account_id, group_id, user_ids),
        )

    async def delete_group(self, account_id: str, group_id: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.delete_group(account_id, group_id),
        )

    async def ensure_trusted_identities(self, identities: dict[str, set[str]]) -> dict[str, int]:
        result = {"created_accounts": 0, "created_users": 0}
        for account_id, user_ids in identities.items():
            created = await self._mutate_account(
                account_id,
                lambda account_id=account_id, user_ids=user_ids: (
                    self._mysql.ensure_trusted_identities({account_id: user_ids})
                ),
            )
            result["created_accounts"] += created["created_accounts"]
            result["created_users"] += created["created_users"]
        return result

    async def begin_deletion(
        self, account_id: str, user_id: str | None, **kwargs
    ) -> tuple[DeletionRecord, bool]:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.begin_deletion(account_id, user_id, **kwargs),
            user_id=user_id,
        )

    async def replace_deletion_task(
        self, account_id: str, user_id: str | None, **kwargs
    ) -> DeletionRecord:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.replace_deletion_task(account_id, user_id, **kwargs),
            user_id=user_id,
        )

    async def finish_deletion(self, account_id: str, user_id: str | None, task_id: str) -> bool:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.finish_deletion(account_id, user_id, task_id),
            user_id=user_id,
        )

    async def replace_active_user_api_key(
        self,
        account_id: str,
        user_id: str,
        api_key: str,
        *,
        expected_api_key: str | None = None,
    ) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.replace_active_user_api_key(
                account_id, user_id, api_key, expected_api_key=expected_api_key
            ),
            user_id=user_id,
        )

    async def revoke_active_api_keys(self, account_id: str, user_id: str | None = None) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.revoke_active_api_keys(account_id, user_id),
            user_id=user_id,
        )

    async def get_user_key_fingerprint(self, account_id: str, user_id: str) -> str | None:
        return await self._read_mysql(
            lambda: self._mysql.get_user_key_fingerprint(account_id, user_id)
        )
