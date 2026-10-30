# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis read-through cache over the authoritative MySQL account store."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar, cast
from urllib.parse import parse_qs, unquote, urlsplit

from openviking.concurrency import AsyncSemaphore
from openviking.server.account_stores.base import AccountStore
from openviking.server.account_stores.models import (
    AccountSummary,
    DeletionRecord,
    GroupSummary,
    UsersPage,
    UserSummary,
)
from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.redis_client import (
    RedisClientHandle,
    create_redis_client,
)
from openviking_cli.exceptions import InvalidArgumentError, UnavailableError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.config_utils import normalize_service_name

logger = get_logger(__name__)
T = TypeVar("T")

_MYSQL_FALLBACK_MAX_CONCURRENT = 8
_MYSQL_FALLBACK_WAIT_SECONDS = 0.1

_RELEASE_LOCK_SCRIPT = """
-- release-lock
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_GET_IF_LOCKED_SCRIPT = """
-- get-if-locked
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return {0}
end
local value = redis.call('GET', KEYS[2])
if value then
    return {2, value}
end
return {1}
"""

_SET_IF_LOCKED_SCRIPT = """
-- set-if-locked
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
if ARGV[5] == '1' then
    local ttl = redis.call('PTTL', KEYS[2])
    if ttl <= 0 then
        redis.call('DEL', KEYS[2])
        return 0
    end
    local now = redis.call('TIME')
    local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
    redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now_ms)
    redis.call('ZADD', KEYS[3], now_ms + ttl, KEYS[2])
    redis.call('EXPIRE', KEYS[3], ARGV[4])
end
return 1
"""

_DELETE_AND_UNINDEX_SCRIPT = """
-- delete-and-unindex
if #KEYS == 1 then
    return 0
end
local deleted = redis.call('DEL', unpack(KEYS, 2))
redis.call('ZREM', KEYS[1], unpack(KEYS, 2))
return deleted
"""

_PRUNE_CACHE_INDEX_SCRIPT = """
-- prune-cache-index
local now = redis.call('TIME')
local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
return redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
"""

_ACCOUNT_CACHE_CLEAR_BATCH_SIZE = 500


class _CacheLockUnavailable(Exception):
    """Redis could not provide the lock needed to coordinate a cache fill."""


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


def _legacy_redis_cache_params(values: dict[str, object]) -> dict[str, object]:
    url = values.pop("url", None)
    if not isinstance(url, str) or not url:
        raise InvalidArgumentError("redis.url must be a non-empty string")
    parsed = urlsplit(url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise InvalidArgumentError("redis.url must use a redis:// or rediss:// URL")
    try:
        port = parsed.port or 6379
    except ValueError as exc:
        raise InvalidArgumentError("redis.url port is invalid") from exc
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    params: dict[str, object] = {
        "mode": "standalone",
        "endpoints": [f"{parsed.scheme}://{host}:{port}"],
        **values,
    }
    if parsed.username and "username" not in params:
        params["username"] = unquote(parsed.username)
    if parsed.password and "password" not in params:
        params["password"] = unquote(parsed.password)
    if parsed.path not in {"", "/"} and "db" not in params:
        try:
            params["db"] = int(parsed.path.lstrip("/"))
        except ValueError as exc:
            raise InvalidArgumentError("redis.url database path is invalid") from exc
    query = parse_qs(parsed.query)
    for query_name, config_name in (
        ("socket_connect_timeout", "connect_timeout_ms"),
        ("socket_timeout", "command_timeout_ms"),
    ):
        if query_name in query and config_name not in params:
            try:
                params[config_name] = max(1, int(float(query[query_name][0]) * 1000))
            except (IndexError, ValueError) as exc:
                raise InvalidArgumentError(f"redis.url {query_name} is invalid") from exc
    return params


class RedisMySQLAccountStore(AccountStore):
    """Cache hot account reads while preserving MySQL as the authority.

    A Redis lease coordinates cache fills and mutations per account. If Redis
    is unavailable, MySQL operations still proceed and cache invalidation is
    best-effort; any stale value is bounded by the fixed cache TTL.
    """

    def __init__(
        self,
        *,
        service_name: str,
        params: dict[str, object] | None = None,
        mysql_store: MySQLAccountStore | None = None,
        redis_client: Any | None = None,
    ) -> None:
        try:
            normalized_service_name = normalize_service_name(service_name)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None
        values = dict(params or {})
        mysql_params = values.pop("mysql", None)
        cache_spec = values.pop("cache", None)
        legacy_redis_params = values.pop("redis", None)
        if values:
            names = ", ".join(sorted(values))
            raise InvalidArgumentError(f"Unknown Redis MySQL account store parameters: {names}")
        if mysql_store is None:
            if not isinstance(mysql_params, dict):
                raise InvalidArgumentError("redis_mysql account storage requires mysql parameters")
            mysql_store = MySQLAccountStore(
                service_name=normalized_service_name,
                params=mysql_params,
            )
        elif mysql_store._service_name != normalized_service_name:
            raise InvalidArgumentError(
                "Redis and MySQL account stores must use the same service_name"
            )
        if cache_spec is None and legacy_redis_params is not None:
            if not isinstance(legacy_redis_params, dict):
                raise InvalidArgumentError("redis_mysql account storage requires cache parameters")
            legacy_values = dict(legacy_redis_params)
            cache_spec = {
                "provider": "redis",
                "params": _legacy_redis_cache_params(legacy_values),
            }
        if not isinstance(cache_spec, dict):
            raise InvalidArgumentError("redis_mysql account storage requires cache parameters")
        unknown_cache_fields = set(cache_spec) - {"provider", "params"}
        if unknown_cache_fields:
            names = ", ".join(sorted(unknown_cache_fields))
            raise InvalidArgumentError(f"Unknown Redis cache fields: {names}")
        cache_provider = cache_spec.get("provider")
        cache_params = cache_spec.get("params")
        if cache_provider != "redis" or not isinstance(cache_params, dict):
            raise InvalidArgumentError(
                "redis_mysql account storage cache must use provider='redis' with a params object"
            )
        redis_values = dict(cache_params)
        self._key_prefix = redis_values.pop("key_prefix", "ov:account-cache")
        self._ttl_seconds = redis_values.pop(
            "ttl_seconds",
            redis_values.get("default_ttl_seconds", 60),
        )
        self._lock_ttl_seconds = redis_values.pop("lock_ttl_seconds", 60)
        self._lock_wait_seconds = redis_values.pop("lock_wait_seconds", 30)
        redis_values.setdefault("connect_timeout_ms", 5000)
        redis_values.setdefault("command_timeout_ms", 5000)
        if not isinstance(self._key_prefix, str) or not self._key_prefix:
            raise InvalidArgumentError("cache.params.key_prefix must be a non-empty string")
        if "{" in self._key_prefix or "}" in self._key_prefix:
            raise InvalidArgumentError(
                "cache.params.key_prefix must not contain Redis hash-tag characters"
            )
        for name in ("ttl_seconds", "lock_ttl_seconds", "lock_wait_seconds"):
            value = getattr(self, f"_{name}")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise InvalidArgumentError(f"cache.params.{name} must be a positive integer")
        if self._lock_ttl_seconds < self._ttl_seconds:
            raise InvalidArgumentError(
                "cache.params.lock_ttl_seconds must be at least cache.params.ttl_seconds"
            )

        self._mysql = mysql_store
        pool_size = cast(int, mysql_store._engine_options.get("pool_size", 10))
        fallback_concurrency = max(1, min(pool_size, _MYSQL_FALLBACK_MAX_CONCURRENT))
        self._mysql_fallback_io = AsyncSemaphore(fallback_concurrency)
        # Bound running plus queued fallbacks across all cached read methods.
        self._mysql_fallback_admission = threading.BoundedSemaphore(fallback_concurrency * 2)
        self._index_ttl_seconds = self._ttl_seconds + self._lock_ttl_seconds
        self._service_name = normalized_service_name
        self._redis = redis_client
        self._redis_config = redis_values
        self._redis_handle: RedisClientHandle | None = None
        self._owns_redis = redis_client is None

    async def load(self) -> None:
        await self._mysql.load()
        try:
            if self._redis is None:
                try:
                    self._redis_handle = create_redis_client(self._redis_config)
                except ImportError:
                    raise RuntimeError(
                        "Redis MySQL account storage requires openviking[redis-mysql]"
                    ) from None
                self._redis = self._redis_handle.client
            await self._redis.ping()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        try:
            if self._redis_handle is not None and self._owns_redis:
                await self._redis_handle.close()
        finally:
            self._redis = None
            self._redis_handle = None
            await self._mysql.close()

    def _account_tag(self, account_id: str) -> str:
        return _key_part(f"{self._service_name}\0{account_id}")

    def _key(self, account_id: str, suffix: str) -> str:
        return f"{self._key_prefix}:v1:{{{self._account_tag(account_id)}}}:{suffix}"

    def _lock_key(self, account_id: str) -> str:
        return self._key(account_id, "lock")

    def _cache_keys_index_key(self, account_id: str) -> str:
        return self._key(account_id, "cache-keys")

    def _user_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"user:{_key_part(user_id)}")

    def _group_ids_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"groups:{_key_part(user_id)}")

    def _account_deletion_key(self, account_id: str) -> str:
        return self._key(account_id, "deletion:account")

    def _user_deletion_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"deletion:user:{_key_part(user_id)}")

    def _account_credential_fence_key(self, account_id: str) -> str:
        return self._key(account_id, "credential-fence:account")

    def _user_credential_fence_key(self, account_id: str, user_id: str) -> str:
        return self._key(account_id, f"credential-fence:user:{_key_part(user_id)}")

    def _binding_key(self, account_id: str, api_key: str) -> str:
        digest_input = self._service_name + "\0" + api_key
        return self._key(account_id, f"binding:{_digest(digest_input)}")

    def _negative_binding_key(self, account_id: str, api_key: str, user_id_hint: str | None) -> str:
        digest_input = self._service_name + "\0" + api_key + "\0" + (user_id_hint or "")
        return self._key(account_id, f"binding-miss:{_digest(digest_input)}")

    def _binding_cache_keys(self, account_id: str, api_key: str, user_id: str) -> tuple[str, ...]:
        return (
            self._binding_key(account_id, api_key),
            self._negative_binding_key(account_id, api_key, user_id),
            self._negative_binding_key(account_id, api_key, None),
        )

    async def _delete(self, *keys: str) -> None:
        assert self._redis is not None
        if keys:
            await self._redis.delete(*keys)

    @asynccontextmanager
    async def _account_lock(self, account_id: str) -> AsyncIterator[str]:
        assert self._redis is not None
        key = self._lock_key(account_id)
        token = secrets.token_urlsafe(24)
        deadline = time.monotonic() + self._lock_wait_seconds
        try:
            while True:
                if await self._redis.set(
                    key,
                    token,
                    nx=True,
                    px=self._lock_ttl_seconds * 1000,
                ):
                    break
                if time.monotonic() >= deadline:
                    raise _CacheLockUnavailable(
                        f"timed out waiting for account cache lock: {account_id}"
                    )
                await asyncio.sleep(0.02 + secrets.randbelow(20) / 1000)
        except _CacheLockUnavailable:
            raise
        except Exception as exc:
            raise _CacheLockUnavailable(
                f"failed to acquire account cache lock: {account_id}"
            ) from exc
        try:
            yield token
        finally:
            try:
                await self._redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
            except Exception:
                logger.exception("Failed to release account cache lock")

    @staticmethod
    def _nullable_payload(value: dict[str, Any] | tuple[str, ...] | None) -> dict[str, Any]:
        return {"found": value is not None, "value": value}

    @staticmethod
    def _read_nullable(payload: dict[str, Any] | None) -> tuple[bool, Any]:
        if payload is None or not isinstance(payload.get("found"), bool):
            return False, None
        return True, payload.get("value")

    async def _get_if_locked(
        self, account_id: str, token: str, key: str
    ) -> tuple[bool, dict[str, Any] | None]:
        assert self._redis is not None
        result = await self._redis.eval(
            _GET_IF_LOCKED_SCRIPT,
            2,
            self._lock_key(account_id),
            key,
            token,
        )
        if not isinstance(result, (list, tuple)) or not result:
            return False, None
        status = int(result[0])
        if status == 0:
            return False, None
        if status == 1:
            return True, None
        return True, _decode(result[1])

    async def _set_if_locked(
        self,
        account_id: str,
        token: str,
        key: str,
        value: dict[str, Any],
        *,
        index: bool = True,
    ) -> bool:
        assert self._redis is not None
        result = await self._redis.eval(
            _SET_IF_LOCKED_SCRIPT,
            3,
            self._lock_key(account_id),
            key,
            self._cache_keys_index_key(account_id),
            token,
            json.dumps(value, separators=(",", ":")),
            self._ttl_seconds,
            self._index_ttl_seconds,
            int(index),
        )
        return bool(result)

    async def _read_mysql_fallback(self, loader: Callable[[], Awaitable[T]]) -> T:
        """Bound Redis-failure reads without occupying a thread while waiting."""
        if not self._mysql_fallback_admission.acquire(blocking=False):
            raise UnavailableError("account storage", "MySQL fallback capacity exceeded")
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._mysql_fallback_io.acquire(), timeout=_MYSQL_FALLBACK_WAIT_SECONDS
                )
            except asyncio.TimeoutError:
                raise UnavailableError("account storage", "MySQL fallback is busy") from None
            acquired = True
            return await loader()
        finally:
            # MySQLRuntime settles database cancellation before returning here.
            if acquired:
                self._mysql_fallback_io.release()
            self._mysql_fallback_admission.release()

    async def _read_through(
        self,
        account_id: str,
        key: str,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        try:
            hit, value = self._read_nullable(_decode(await self._redis.get(key)))
            if hit:
                return value
        except Exception:
            logger.exception("Redis cache read failed; reading %s from MySQL", key)
            return await self._read_mysql_fallback(loader)

        mysql_started = False
        try:
            async with self._account_lock(account_id) as token:
                owned, payload = await self._get_if_locked(account_id, token, key)
                if not owned:
                    logger.warning("Redis cache lease expired before reading %s", key)
                    mysql_started = True
                    return await self._read_mysql_fallback(loader)
                hit, value = self._read_nullable(payload)
                if hit:
                    return value
                mysql_started = True
                value = await loader()
                try:
                    if not await self._set_if_locked(
                        account_id,
                        token,
                        key,
                        self._nullable_payload(value),
                    ):
                        logger.warning("Redis cache lease expired before writing %s", key)
                except Exception:
                    logger.exception("Failed to populate Redis account cache for %s", key)
                return value
        except _CacheLockUnavailable:
            logger.warning("Redis cache lock unavailable; reading %s from MySQL", key)
        except Exception:
            if mysql_started:
                raise
            logger.exception("Redis cache read failed; reading %s from MySQL", key)
        return await self._read_mysql_fallback(loader)

    async def _delete_and_unindex(self, account_id: str, *keys: str) -> None:
        assert self._redis is not None
        if keys:
            await self._redis.eval(
                _DELETE_AND_UNINDEX_SCRIPT,
                len(keys) + 1,
                self._cache_keys_index_key(account_id),
                *keys,
            )

    async def _invalidate(self, account_id: str, *keys: str) -> None:
        try:
            await self._delete_and_unindex(account_id, *keys)
        except Exception:
            logger.exception("Failed to invalidate Redis account cache")

    async def _prune_cache_index(self, account_id: str) -> None:
        assert self._redis is not None
        await self._redis.eval(
            _PRUNE_CACHE_INDEX_SCRIPT,
            1,
            self._cache_keys_index_key(account_id),
        )

    async def _clear_account_cache(self, account_id: str) -> None:
        assert self._redis is not None
        index_key = self._cache_keys_index_key(account_id)
        try:
            await self._prune_cache_index(account_id)
            while True:
                keys = await self._redis.zrange(
                    index_key,
                    0,
                    _ACCOUNT_CACHE_CLEAR_BATCH_SIZE - 1,
                )
                if not keys:
                    break
                await self._delete_and_unindex(account_id, *keys)
            # ZREM removes an emptied sorted set automatically. A separate DEL
            # could remove a newer fill's index after this lease is lost.
        except Exception:
            logger.exception("Failed to clear Redis account cache for %s", account_id)

    async def _invalidate_after_lock_timeout(
        self,
        account_id: str,
        keys: tuple[str, ...],
        *,
        account_wide: bool,
    ) -> None:
        try:
            await self._delete(self._lock_key(account_id))
        except Exception:
            logger.exception("Failed to revoke Redis account cache lock")
        try:
            async with self._account_lock(account_id):
                if account_wide:
                    await self._clear_account_cache(account_id)
                if keys:
                    await self._invalidate(account_id, *keys)
        except _CacheLockUnavailable:
            logger.warning(
                "Redis cache lock remained unavailable after revoking the lease for %s",
                account_id,
            )
            if account_wide:
                await self._clear_account_cache(account_id)
            if keys:
                await self._invalidate(account_id, *keys)

    async def _mutate_account(
        self,
        account_id: str,
        operation: Callable[[], Awaitable[T]],
        invalidation_keys: Callable[[], tuple[str, ...]],
        *,
        account_wide: bool = False,
    ) -> T:
        result = await operation()
        keys = invalidation_keys()
        try:
            async with self._account_lock(account_id):
                if account_wide:
                    await self._clear_account_cache(account_id)
                if keys:
                    await self._invalidate(account_id, *keys)
        except _CacheLockUnavailable:
            logger.warning(
                "Redis cache lock unavailable after mutating %s; "
                "revoking the lease and invalidating without the lock",
                account_id,
            )
            await self._invalidate_after_lock_timeout(
                account_id,
                keys,
                account_wide=account_wide,
            )
        return result

    def _user_cache_keys(self, account_id: str, user_id: str) -> tuple[str, ...]:
        return (
            self._user_key(account_id, user_id),
            self._group_ids_key(account_id, user_id),
            self._user_deletion_key(account_id, user_id),
            self._user_credential_fence_key(account_id, user_id),
        )

    async def _cached_binding(
        self,
        account_id: str,
        api_key: str,
        user_id_hint: str | None,
    ) -> tuple[bool, tuple[str, str] | None]:
        binding = _decode(await self._redis.get(self._binding_key(account_id, api_key)))
        hit, value = self._read_nullable(binding)
        if hit and value is None:
            return True, None
        if isinstance(value, dict):
            try:
                binding_account_id = str(value["account_id"])
                user_id = str(value["user_id"])
                account_fence = str(value["account_fence"])
                user_fence = str(value["user_fence"])
            except (KeyError, TypeError, ValueError):
                pass
            else:
                if user_id_hint is None or user_id == user_id_hint:
                    current_account_fence = _decode(
                        await self._redis.get(
                            self._account_credential_fence_key(binding_account_id)
                        )
                    )
                    current_user_fence = _decode(
                        await self._redis.get(
                            self._user_credential_fence_key(binding_account_id, user_id)
                        )
                    )
                    if (
                        current_account_fence is not None
                        and current_user_fence is not None
                        and current_account_fence.get("value") == account_fence
                        and current_user_fence.get("value") == user_fence
                    ):
                        return True, (binding_account_id, user_id)
        negative = _decode(
            await self._redis.get(self._negative_binding_key(account_id, api_key, user_id_hint))
        )
        negative_hit, negative_value = self._read_nullable(negative)
        return (True, None) if negative_hit and negative_value is None else (False, None)

    async def _cache_binding(
        self,
        account_id: str,
        token: str,
        api_key: str,
        binding: tuple[str, str] | None,
        user_id_hint: str | None,
    ) -> None:
        if binding is None:
            await self._set_if_locked(
                account_id,
                token,
                self._negative_binding_key(account_id, api_key, user_id_hint),
                self._nullable_payload(None),
                index=False,
            )
            return
        binding_account_id, user_id = binding
        if binding_account_id != account_id:
            return
        account_fence = secrets.token_urlsafe(18)
        user_fence = secrets.token_urlsafe(18)
        account_fence_key = self._account_credential_fence_key(account_id)
        user_fence_key = self._user_credential_fence_key(account_id, user_id)
        existing_account_fence = _decode(await self._redis.get(account_fence_key))
        existing_user_fence = _decode(await self._redis.get(user_fence_key))
        if existing_account_fence is not None:
            account_fence = str(existing_account_fence.get("value", account_fence))
        elif not await self._set_if_locked(
            account_id,
            token,
            account_fence_key,
            self._nullable_payload(account_fence),
        ):
            return
        if existing_user_fence is not None:
            user_fence = str(existing_user_fence.get("value", user_fence))
        elif not await self._set_if_locked(
            account_id,
            token,
            user_fence_key,
            self._nullable_payload(user_fence),
        ):
            return
        await self._set_if_locked(
            account_id,
            token,
            self._binding_key(account_id, api_key),
            self._nullable_payload(
                {
                    "account_id": account_id,
                    "user_id": user_id,
                    "account_fence": account_fence,
                    "user_fence": user_fence,
                }
            ),
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

        if account_id_hint is None:
            return await self._read_mysql_fallback(verify_in_mysql)
        try:
            hit, binding = await self._cached_binding(
                account_id_hint,
                api_key,
                user_id_hint,
            )
            if hit:
                return binding
        except Exception:
            logger.exception("Redis API-key cache read failed; falling back to MySQL")
            return await self._read_mysql_fallback(verify_in_mysql)
        mysql_started = False
        try:
            async with self._account_lock(account_id_hint) as token:
                hit, binding = await self._cached_binding(
                    account_id_hint,
                    api_key,
                    user_id_hint,
                )
                if hit:
                    return binding
                mysql_started = True
                binding = await verify_in_mysql()
                try:
                    await self._cache_binding(
                        account_id_hint,
                        token,
                        api_key,
                        binding,
                        user_id_hint,
                    )
                except Exception:
                    logger.exception("Failed to populate Redis API-key cache")
                return binding
        except _CacheLockUnavailable:
            logger.warning("Redis cache lock unavailable; verifying API key in MySQL")
        except Exception:
            if mysql_started:
                raise
            logger.exception("Redis API-key cache write failed; verifying in MySQL")
        return await self._read_mysql_fallback(verify_in_mysql)

    async def get_user(self, account_id: str, user_id: str) -> UserSummary | None:
        value = await self._read_through(
            account_id,
            self._user_key(account_id, user_id),
            lambda: self._mysql.get_user(account_id, user_id),
        )
        return value if value is None else UserSummary(value)

    async def get_user_membership(self, account_id: str, user_id: str) -> UserSummary | None:
        # Bypass the read-through cache: the admin membership probe must reflect
        # the latest persisted state, which MySQL indexes directly.
        return await self._mysql.get_user_membership(account_id, user_id)

    async def get_user_group_ids(self, account_id: str, user_id: str) -> tuple[str, ...]:
        value = await self._read_through(
            account_id,
            self._group_ids_key(account_id, user_id),
            lambda: self._mysql.get_user_group_ids(account_id, user_id),
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
        )
        return value

    async def iter_deletions(self) -> list[tuple[str, str | None, DeletionRecord]]:
        return await self._mysql.iter_deletions()

    async def get_account(self, account_id: str) -> AccountSummary | None:
        return await self._mysql.get_account(account_id)

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
    ) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.create_account(account_id, admin_user_id),
            lambda: self._user_cache_keys(account_id, admin_user_id),
            account_wide=True,
        )

    async def create_account_with_api_key(
        self,
        account_id: str,
        admin_user_id: str,
        api_key: str,
    ) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.create_account_with_api_key(
                account_id,
                admin_user_id,
                api_key,
            ),
            lambda: self._binding_cache_keys(account_id, api_key, admin_user_id),
            account_wide=True,
        )

    async def delete_account(self, account_id: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.delete_account(account_id),
            lambda: (),
            account_wide=True,
        )

    async def list_accounts(self, **kwargs) -> list[AccountSummary]:
        return await self._mysql.list_accounts(**kwargs)

    async def create_user(self, account_id: str, user_id: str, role: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.create_user(account_id, user_id, role),
            lambda: self._user_cache_keys(account_id, user_id),
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
            lambda: (
                self._user_cache_keys(account_id, user_id)
                + self._binding_cache_keys(account_id, api_key, user_id)
            ),
        )

    async def list_users_page(self, account_id: str, **kwargs) -> UsersPage:
        return await self._mysql.list_users_page(account_id, **kwargs)

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.set_role(account_id, user_id, role),
            lambda: (self._user_key(account_id, user_id),),
        )

    async def create_group(self, account_id: str, group_id: str) -> GroupSummary:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.create_group(account_id, group_id),
            lambda: (),
        )

    async def get_groups(self, account_id: str) -> list[GroupSummary]:
        return await self._mysql.get_groups(account_id)

    async def get_group_members(self, account_id: str, group_id: str) -> list[str]:
        return await self._mysql.get_group_members(account_id, group_id)

    async def add_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.add_group_member(account_id, group_id, user_id),
            lambda: (self._group_ids_key(account_id, user_id),),
        )

    async def remove_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.remove_group_member(account_id, group_id, user_id),
            lambda: (self._group_ids_key(account_id, user_id),),
        )

    async def begin_group_deletion(self, account_id: str, group_id: str) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.begin_group_deletion(account_id, group_id),
            lambda: (),
            account_wide=True,
        )

    async def finish_group_deletion(self, account_id: str, group_id: str) -> bool:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.finish_group_deletion(account_id, group_id),
            lambda: (),
            account_wide=True,
        )

    async def iter_deleting_groups(self) -> list[tuple[str, str]]:
        return await self._mysql.iter_deleting_groups()

    async def ensure_trusted_identities(self, identities: dict[str, set[str]]) -> dict[str, int]:
        result = {"created_accounts": 0, "created_users": 0}
        for account_id, user_ids in identities.items():
            created = await self._mutate_account(
                account_id,
                lambda account_id=account_id, user_ids=user_ids: (
                    self._mysql.ensure_trusted_identities({account_id: user_ids})
                ),
                lambda: (),
                account_wide=True,
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
            lambda: (
                self._account_deletion_key(account_id)
                if user_id is None
                else self._user_deletion_key(account_id, user_id),
            ),
        )

    async def replace_deletion_task(
        self, account_id: str, user_id: str | None, **kwargs
    ) -> DeletionRecord:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.replace_deletion_task(account_id, user_id, **kwargs),
            lambda: (
                self._account_deletion_key(account_id)
                if user_id is None
                else self._user_deletion_key(account_id, user_id),
            ),
        )

    async def finish_deletion(self, account_id: str, user_id: str | None, task_id: str) -> bool:
        return await self._mutate_account(
            account_id,
            lambda: self._mysql.finish_deletion(account_id, user_id, task_id),
            lambda: () if user_id is None else self._user_cache_keys(account_id, user_id),
            account_wide=user_id is None,
        )

    async def replace_active_user_api_key(
        self, account_id: str, user_id: str, api_key: str,
        *, expected_api_key: str | None = None,
    ) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.replace_active_user_api_key(
                account_id, user_id, api_key, expected_api_key=expected_api_key
            ),
            lambda: (
                (self._user_credential_fence_key(account_id, user_id),)
                + self._binding_cache_keys(account_id, api_key, user_id)
            ),
        )

    async def revoke_active_api_keys(self, account_id: str, user_id: str | None = None) -> None:
        await self._mutate_account(
            account_id,
            lambda: self._mysql.revoke_active_api_keys(account_id, user_id),
            lambda: (
                (self._account_credential_fence_key(account_id),)
                if user_id is None
                else (self._user_credential_fence_key(account_id, user_id),)
            ),
        )

    async def get_user_key_fingerprint(self, account_id: str, user_id: str) -> str | None:
        return await self._mysql.get_user_key_fingerprint(account_id, user_id)
