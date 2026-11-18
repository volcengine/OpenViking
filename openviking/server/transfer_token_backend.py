"""Optional single-primary Redis owner for short-lived transfer tokens."""

from __future__ import annotations

import asyncio
import json
import math
from contextlib import asynccontextmanager
from dataclasses import asdict, fields

from openviking.server.account_stores.redis_client import create_redis_client
from openviking.server.account_stores.redis_runtime import (
    get_shared_redis_runtime,
    wait_redis_cleanup,
)
from openviking_cli.exceptions import InvalidArgumentError, UnavailableError
from openviking_cli.utils.config.agfs_config import RedisCacheConfig
from openviking_cli.utils.config.config_utils import normalize_service_name

_ISSUE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return false end
local now = redis.call('TIME')
local expires = tonumber(now[1]) + tonumber(now[2]) / 1000000 + tonumber(ARGV[2]) / 1000
local record = {version=1,purpose=ARGV[3],expires_at=expires,payload=ARGV[1]}
redis.call('SET', KEYS[1], cjson.encode(record), 'PX', ARGV[2], 'NX')
return tostring(expires)
"""
_CONSUME = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""


class RedisTransferTokens:
    """Atomic create/expiry/burn on the existing private Redis event loop.

    There is no command retry and no memory fallback. An uncertain consume may
    have burned a token; callers must not assume it remains usable.
    """

    def __init__(self, params, *, service_name, key_prefix="transfer-token"):
        try:
            redis = RedisCacheConfig.model_validate(params)
        except Exception:
            raise InvalidArgumentError("Invalid transfer-token Redis configuration") from None
        if redis.mode != "standalone" or redis.command_timeout_ms <= 0:
            raise InvalidArgumentError(
                "Transfer tokens require standalone Redis and a positive command_timeout_ms"
            )
        self._params = dict(params)
        self._prefix = "ov:" + normalize_service_name(service_name) + ":" + key_prefix + ":v1:"
        self._runtime = get_shared_redis_runtime()
        self._handle = None
        self._gate = None
        self._leased = False
        self._closing = False

    async def initialize(self):
        async def start():
            from redis.asyncio.retry import Retry
            from redis.backoff import NoBackoff

            if self._handle is not None:
                return
            if self._closing:
                raise RuntimeError("A closed transfer-token backend cannot be restarted")
            await self._runtime.acquire()
            self._leased = True
            self._gate = asyncio.Lock()
            self._handle = create_redis_client(
                self._params, retry=Retry(NoBackoff(), 0), retry_on_timeout=False, retry_on_error=[]
            )
            await self._handle.client.ping()

        try:
            await wait_redis_cleanup(self._runtime.run(start))
        except BaseException as exc:
            await self.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise UnavailableError("Transfer-token backend initialization failed") from None

    async def close(self):
        async def finish():
            self._closing = True
            try:
                if self._gate is not None:
                    async with self._gate:
                        if self._handle is not None:
                            await self._handle.close()
                            self._handle = None
                elif self._handle is not None:
                    await self._handle.close()
                    self._handle = None
            finally:
                if self._leased:
                    self._leased = False
                    await self._runtime.release()

        await wait_redis_cleanup(self._runtime.run(finish))

    async def _run(self, operation):
        async def execute():
            if self._closing or self._handle is None or self._gate is None:
                raise UnavailableError("Transfer-token backend is not active")
            async with self._gate:
                if self._closing or self._handle is None:
                    raise UnavailableError("Transfer-token backend is not active")
                return await operation(self._handle.client)

        try:
            return await self._runtime.run(execute)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise UnavailableError(
                "Transfer-token operation failed; its outcome may be unknown"
            ) from None

    def key(self, purpose, token):
        if purpose not in {"upload", "download"}:
            raise InvalidArgumentError("Invalid transfer-token purpose")
        return self._prefix + purpose + ":" + token

    async def issue(self, purpose, info, ttl_seconds):
        from openviking.server.upload_token_store import _new_token

        if type(ttl_seconds) not in {int, float} or not math.isfinite(ttl_seconds):
            raise InvalidArgumentError("Invalid transfer-token lifetime")
        ttl_ms = max(1000, math.ceil(ttl_seconds * 1000))
        if ttl_ms > 2**63 - 1:
            raise InvalidArgumentError("Invalid transfer-token lifetime")
        data = asdict(info)
        data["role"] = str(info.role)
        data["acl"] = info.acl.model_dump(mode="json") if info.acl is not None else None
        record = json.dumps(data, ensure_ascii=False)
        if len(record.encode()) > 64 * 1024:
            raise InvalidArgumentError("Transfer-token record exceeds 64 KiB")
        for _ in range(8):
            token = _new_token()
            key = self.key(purpose, token)
            value = await self._run(
                lambda redis, key=key: redis.eval(_ISSUE, 1, key, record, ttl_ms, purpose)
            )
            if value:
                return token, float(value)
        raise UnavailableError("Unable to create a unique transfer token")

    async def consume(self, purpose, token):
        from openviking.server.identity import Role
        from openviking.server.upload_token_store import UploadTokenError, _TokenInfo
        from openviking.storage.acl import AclSpec

        raw = await self._run(lambda redis: redis.eval(_CONSUME, 1, self.key(purpose, token)))
        if raw is None:
            return None
        try:
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError
            if (
                type(record.get("version")) is not int
                or record["version"] != 1
                or record.get("purpose") != purpose
            ):
                raise ValueError
            data = json.loads(record["payload"])
            if not isinstance(data, dict):
                raise ValueError
            data["expires_at"] = record["expires_at"]
            if set(data) != {field.name for field in fields(_TokenInfo)}:
                raise ValueError
            data["role"] = Role.parse(data["role"])
            data["acl"] = AclSpec.model_validate(data["acl"]) if data["acl"] is not None else None
            string_fields = (
                "account_id",
                "user_id",
                "to",
                "parent",
                "reason",
                "actor_peer_id",
                "processing_mode",
                "tag_mode",
                "parse_mode",
                "kind",
                "skill_target_uri",
                "uri",
            )
            if any(type(data[field]) is not str for field in string_fields):
                raise ValueError
            for field in ("tags", "skill_names"):
                value = data[field]
                if value is not None and (
                    type(value) is not list or any(type(item) is not str for item in value)
                ):
                    raise ValueError
            if any(type(data[field]) is not bool for field in ("list_only", "from_oauth")):
                raise ValueError
            if data["processing_mode"] not in {"semantic_and_vectors", "vectors_only"}:
                raise ValueError
            if (
                not isinstance(data["account_id"], str)
                or not data["account_id"]
                or not isinstance(data["user_id"], str)
                or not data["user_id"]
            ):
                raise ValueError
            if type(data["expires_at"]) not in {int, float} or not math.isfinite(
                data["expires_at"]
            ):
                raise ValueError
            return _TokenInfo(**data)
        except (ValueError, TypeError, KeyError):
            raise UploadTokenError("invalid transfer-token record") from None


@asynccontextmanager
async def transfer_token_lifespan(config):
    from openviking.server.upload_token_store import download_token_store, upload_token_store
    from openviking_cli.utils.config import get_openviking_config

    backend = None
    if config.transfer_tokens.backend == "redis":
        ov_config = get_openviking_config()
        cache = ov_config.cache
        if cache is None or cache.provider != "redis":
            raise InvalidArgumentError(
                "Redis transfer tokens require the top-level Redis cache configuration"
            )
        backend = RedisTransferTokens(
            cache.params,
            service_name=ov_config.service_name,
            key_prefix=config.transfer_tokens.key_prefix,
        )
        await backend.initialize()
    stores = [upload_token_store, download_token_store]
    previous = [store._backend for store in stores]
    if any(value is not None for value in previous):
        if backend is not None:
            await backend.close()
        raise RuntimeError("Transfer-token runtime is already active")
    for store in stores:
        store._backend = backend
    try:
        yield backend
    finally:
        for store in stores:
            store._backend = None
        if backend is not None:
            await backend.close()
