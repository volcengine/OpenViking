# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis persistence for distributed expiry scheduling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from importlib import resources
from typing import Any, Literal, Sequence, cast
from urllib.parse import urlsplit

from .api import ExpiryRegistration, ExpiryRegistrationResult, ItemIdentity
from .config import ExpiryRedisConfig
from .keys import ExpiryKeys
from .metrics import ExpiryMetrics
from .models import (
    MAX_EXACT_INTEGER,
    SCORE_TIME_BASE,
    AsyncExecutor,
    BucketItem,
    BucketPage,
    BucketRef,
    Claim,
    ItemMetadata,
    NamespaceSpec,
    OwnerRole,
    OwnerToken,
    PublishResult,
    RegistryCursor,
    RegistryPage,
    ShardWindow,
    SourceCursor,
    SourceItem,
    SourcePage,
    StoreOperationError,
    claim_layer_bounds,
    decode_claim_score,
    encode_claim_score,
    encode_ready_claim_score,
    metadata_ttl_ms,
)

RetryPolicy = Literal["read", "idempotent", "resolve_by_caller"]


class RedisExpiryStore:
    def __init__(
        self,
        config: ExpiryRedisConfig,
        keys: ExpiryKeys,
        io: AsyncExecutor,
        metrics: ExpiryMetrics,
    ) -> None:
        self._config = config
        self._keys = keys
        self._io = io
        self._metrics = metrics
        self._client: Any = None
        self._scripts: dict[str, str] = {}
        self._script_shas: dict[str, str] = {}
        self._ready = False

    async def open(self) -> None:
        await self._io.run(self._open_on_loop)

    async def close(self) -> None:
        self._ready = False
        if self._client is not None:
            await self._io.run(self._close_on_loop)

    async def ensure_namespace(self, spec: NamespaceSpec) -> None:
        key = self._keys.namespace()
        expected = {
            "shard_count": str(spec.shard_count),
            "hash_name": spec.hash_name,
            "hash_seed": str(spec.hash_seed),
            "identity_encoding": spec.identity_encoding,
        }
        current = await self._command("hgetall", key)
        initializing = True
        if current:
            normalized = {str(k): str(v) for k, v in current.items()}
            for field, value in expected.items():
                if normalized.get(field) != value:
                    raise StoreOperationError(
                        "namespace_mismatch", f"Timer namespace mismatch: {field}"
                    )
            state = normalized.get("state")
            if state not in {"INITIALIZING", "READY"}:
                raise StoreOperationError("namespace_corrupt", "Invalid timer namespace state")
            initializing = state == "INITIALIZING"
        else:
            created = dict(expected)
            created["state"] = "INITIALIZING"
            await self._command("hset", key, mapping=created, retry="idempotent")
        for shard_id in range(spec.shard_count):
            progress_key = self._keys.progress(shard_id)
            if initializing:
                await self._command("set", progress_key, "0", nx=True, retry="idempotent")
            elif await self._command("get", progress_key) is None:
                raise StoreOperationError(
                    "namespace_corrupt", f"Timer progress is missing for shard {shard_id}"
                )
        if initializing:
            await self._command("hset", key, "state", "READY", retry="idempotent")
        self._ready = True

    async def ensure_metadata(self, item_id: str, task: ExpiryRegistration) -> None:
        identity = task.identity
        await self._eval(
            "ensure_metadata",
            [self._keys.metadata(item_id)],
            [
                identity.item_type,
                identity.local_id,
                identity.account_id,
                identity.user_id,
                metadata_ttl_ms(task.timeout_seconds),
            ],
            retry="idempotent",
        )

    async def read_metadata(self, item_id: str) -> ItemMetadata | None:
        raw = await self._command("hgetall", self._keys.metadata(item_id))
        if not raw:
            return None
        required = ("item_type", "account_id", "user_id", "local_id")
        if any(name not in raw for name in required):
            raise StoreOperationError("metadata_corrupt", "Timer metadata is incomplete")
        return ItemMetadata(ItemIdentity(*(str(raw[name]) for name in required)))

    async def upsert_resource(
        self, shard_id: int, item_id: str, task: ExpiryRegistration
    ) -> ExpiryRegistrationResult:
        raw = await self._eval(
            "upsert_resource",
            [self._keys.source(shard_id)],
            [item_id, task.due_at_ms],
            retry="resolve_by_caller",
        )
        return ExpiryRegistrationResult(int(raw))

    async def unregister_resource(
        self,
        shard_id: int,
        item_id: str,
        *,
        delete_metadata: bool = True,
    ) -> None:
        await self._command("zrem", self._keys.source(shard_id), item_id, retry="idempotent")
        if delete_metadata:
            await self._single_key_command(
                "DEL",
                self._keys.metadata(item_id),
                retry="idempotent",
            )

    async def read_progress(self, shard_id: int) -> int:
        raw = await self._command("get", self._keys.progress(shard_id))
        if raw is None:
            raise StoreOperationError("namespace_corrupt", "Timer progress is missing")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise StoreOperationError("namespace_corrupt", "Timer progress is invalid") from exc

    async def read_scan_window(self, shard_id: int) -> ShardWindow:
        progress = await self.read_progress(shard_id)
        source_now_ms = await self._read_time_ms_for_key(self._keys.source(shard_id))
        upper = source_now_ms // 60_000 * 60_000
        if upper < progress:
            raise StoreOperationError(
                "clock_regression", "Source time is behind published progress"
            )
        return ShardWindow(progress, upper, source_now_ms)

    async def read_source_page(
        self,
        shard_id: int,
        *,
        lower_exclusive_ms: int,
        upper_inclusive_ms: int,
        after: SourceCursor | None,
        limit: int,
    ) -> SourcePage:
        items, next_raw = await self._read_zset_page(
            self._keys.source(shard_id),
            lower_exclusive=lower_exclusive_ms,
            upper_inclusive=upper_inclusive_ms,
            after_score=None if after is None else after.due_at_ms,
            after_member=None if after is None else after.item_id,
            limit=limit,
        )
        page_items = tuple(SourceItem(item, score) for item, score in items)
        cursor = SourceCursor(*next_raw) if next_raw else None
        return SourcePage(page_items, cursor)

    async def reset_unpublished_minute(
        self,
        owner: OwnerToken,
        *,
        minute_ms: int,
        expected_progress_ms: int,
        item_types: Sequence[str],
    ) -> None:
        await self.assert_owner(owner)
        current = await self.read_progress(owner.shard_id)
        if current != expected_progress_ms or minute_ms <= current:
            raise StoreOperationError("progress_conflict", "Timer progress changed")
        keys = [
            self._keys.bucket(BucketRef(item_type, owner.shard_id, minute_ms))
            for item_type in item_types
        ]
        # Bucket keys deliberately have no shared cluster hash tag. Delete them
        # one at a time, bypassing redis-py's cross-slot pipeline helper.
        for key in keys:
            await self._single_key_command("UNLINK", key, retry="idempotent")

    async def stage_items(
        self, owner: OwnerToken, bucket: BucketRef, item_ids: Sequence[str]
    ) -> None:
        if not item_ids:
            return
        await self.assert_owner(owner)
        mapping = dict.fromkeys(item_ids, 0)
        await self._command("zadd", self._keys.bucket(bucket), mapping, retry="resolve_by_caller")

    async def publish_minute(
        self,
        owner: OwnerToken,
        *,
        expected_progress_ms: int,
        minute_ms: int,
        buckets: Sequence[BucketRef],
    ) -> PublishResult:
        members = [self._keys.registry_member(ref) for ref in buckets]
        raw = await self._eval(
            "publish_minute",
            [
                self._keys.registry(owner.shard_id),
                self._keys.progress(owner.shard_id),
                self._keys.owner(OwnerRole.SCHEDULER, owner.shard_id),
            ],
            [owner.token, expected_progress_ms, minute_ms, len(members), *members],
            retry="resolve_by_caller",
        )
        status = str(raw[0])
        if status not in {"published", "already_published"}:
            raise StoreOperationError("namespace_corrupt", "Invalid timer publish result")
        return PublishResult(
            cast(Literal["published", "already_published"], status),
            int(raw[1]),
        )

    async def cleanup_source_page(self, shard_id: int, through_ms: int, *, limit: int) -> int:
        return int(
            await self._eval(
                "cleanup_source_page",
                [self._keys.source(shard_id)],
                [through_ms, limit],
                retry="idempotent",
            )
        )

    async def read_registry_page(
        self, shard_id: int, *, after: RegistryCursor | None, limit: int
    ) -> RegistryPage:
        items, next_raw = await self._read_zset_page(
            self._keys.registry(shard_id),
            lower_exclusive=-1,
            upper_inclusive=None,
            after_score=None if after is None else after.score_ms,
            after_member=None if after is None else after.member,
            limit=limit,
        )
        buckets = tuple(
            self._keys.parse_registry_member(str(member), expected_shard=shard_id)
            for member, _score in items
        )
        cursor = RegistryCursor(*next_raw) if next_raw else None
        return RegistryPage(buckets, cursor)

    async def read_time_ms(self) -> int:
        return self._decode_redis_time(await self._command("time"))

    @staticmethod
    def _decode_redis_time(raw: Any) -> int:
        try:
            seconds, microseconds = (int(value) for value in raw)
        except (TypeError, ValueError) as exc:
            raise StoreOperationError("namespace_corrupt", "Invalid Redis time") from exc
        if not 0 < seconds < SCORE_TIME_BASE or not 0 <= microseconds < 1_000_000:
            raise StoreOperationError("namespace_corrupt", "Redis time is outside supported range")
        return seconds * 1000 + microseconds // 1000

    async def _read_time_ms_for_key(self, key: str) -> int:
        """Read TIME from the primary owning key so source cutoffs share its clock."""

        async def operation() -> Any:
            if self._client is None:
                raise StoreOperationError("not_started", "Redis timer store is closed")
            kwargs: dict[str, object] = {}
            if self._config.mode == "cluster":
                node = self._client.get_node_from_key(key)
                if node is None:
                    raise RuntimeError("Redis Cluster has no primary for the timer key")
                kwargs["target_nodes"] = node
            return await self._client.time(**kwargs)

        raw = await self._io.run(lambda: self._run_with_retry(operation, retry="read"))
        return self._decode_redis_time(raw)

    async def _read_zset_page(
        self,
        key: str,
        *,
        lower_exclusive: int,
        upper_inclusive: int | None,
        after_score: int | None,
        after_member: str | None,
        limit: int,
    ) -> tuple[tuple[tuple[str, int], ...], tuple[int, str] | None]:
        if after_score is None:
            upper = (
                await self._read_time_ms_for_key(key)
                if upper_inclusive is None
                else upper_inclusive
            )
            raw_items = await self._command(
                "zrangebyscore",
                key,
                f"({lower_exclusive}",
                upper,
                start=0,
                num=limit + 1,
                withscores=True,
            )
            items = self._decode_zset_items(
                raw_items,
                lower_exclusive=lower_exclusive,
                upper_inclusive=upper,
            )
            has_more = len(items) > limit
            items = items[:limit]
            next_cursor = (items[-1][1], items[-1][0]) if has_more else None
            return items, next_cursor

        raw = await self._eval(
            "read_zset_page",
            [key],
            [
                lower_exclusive,
                "" if upper_inclusive is None else upper_inclusive,
                after_score,
                after_member or "",
                limit,
            ],
            retry="read",
        )
        data = json.loads(raw)
        items = tuple((str(member), int(score)) for member, score in data["items"])
        next_raw = data.get("next")
        next_cursor = (int(next_raw[0]), str(next_raw[1])) if next_raw else None
        return items, next_cursor

    @staticmethod
    def _decode_zset_items(
        raw_items: Any,
        *,
        lower_exclusive: int,
        upper_inclusive: int,
    ) -> tuple[tuple[str, int], ...]:
        items: list[tuple[str, int]] = []
        try:
            for member, raw_score in raw_items:
                numeric_score = float(raw_score)
                score = int(numeric_score)
                if numeric_score != score or score <= lower_exclusive or score > upper_inclusive:
                    raise ValueError
                items.append((str(member), score))
        except (TypeError, ValueError) as exc:
            raise StoreOperationError(
                "namespace_corrupt", "Timer sorted set contains an invalid score"
            ) from exc
        return tuple(items)

    async def read_bucket_page(
        self,
        bucket: BucketRef,
        *,
        attempt: int,
        limit: int,
    ) -> BucketPage:
        lower, upper = claim_layer_bounds(attempt)
        raw = await self._command(
            "zrangebyscore",
            self._keys.bucket(bucket),
            lower,
            upper,
            start=0,
            num=limit,
            withscores=True,
        )
        items: list[BucketItem] = []
        try:
            for item_id, raw_score in raw:
                numeric_score = float(raw_score)
                score = int(numeric_score)
                if numeric_score != score or not lower <= score <= upper:
                    raise ValueError
                items.append(BucketItem(str(item_id), score))
        except (TypeError, ValueError) as exc:
            raise StoreOperationError(
                "namespace_corrupt", "Timer bucket contains an invalid score"
            ) from exc
        return BucketPage(tuple(items))

    async def defer_registration(
        self,
        owner: OwnerToken,
        bucket: BucketRef,
        *,
        next_poll_at_ms: int,
    ) -> bool:
        if (
            owner.role is not OwnerRole.CALLBACK
            or owner.item_type != bucket.item_type
            or owner.shard_id != bucket.shard_id
        ):
            raise StoreOperationError(
                "invalid_owner", "Callback owner does not match the timer bucket"
            )
        raw = await self._eval(
            "defer_registration",
            [
                self._keys.registry(bucket.shard_id),
                self._keys.owner(
                    owner.role,
                    owner.shard_id,
                    item_type=owner.item_type,
                ),
            ],
            [owner.token, self._keys.registry_member(bucket), next_poll_at_ms],
            retry="idempotent",
        )
        return bool(int(raw))

    async def claim(
        self,
        bucket: BucketRef,
        item: BucketItem,
        *,
        attempt: int,
        started_at_seconds: int,
    ) -> Claim | None:
        if item.score == 0:
            if attempt != 0:
                raise StoreOperationError(
                    "namespace_corrupt", "Unclaimed expiry item is outside attempt zero"
                )
        else:
            item_attempt, _ = decode_claim_score(item.score)
            if item_attempt != attempt:
                raise StoreOperationError(
                    "namespace_corrupt", "Expiry item is outside its expected attempt layer"
                )
        claim_score = encode_claim_score(attempt, started_at_seconds)
        raw = await self._eval(
            "cas_zset_member",
            [self._keys.bucket(bucket)],
            ["set", item.item_id, item.score, claim_score],
            retry="resolve_by_caller",
        )
        return Claim(bucket, item.item_id, claim_score) if bool(int(raw)) else None

    async def advance_retry(self, claim: Claim) -> bool:
        attempt, _ = decode_claim_score(claim.claim_score)
        retry_score = encode_ready_claim_score(attempt + 1)
        raw = await self._eval(
            "cas_zset_member",
            [self._keys.bucket(claim.bucket)],
            ["set", claim.item_id, claim.claim_score, retry_score],
            retry="resolve_by_caller",
        )
        return bool(int(raw))

    async def ack(self, claim: Claim) -> bool:
        raw = await self._eval(
            "cas_zset_member",
            [self._keys.bucket(claim.bucket)],
            ["delete", claim.item_id, claim.claim_score, ""],
            retry="idempotent",
        )
        return bool(int(raw))

    async def remove_registration_if_empty(self, bucket: BucketRef) -> bool:
        if int(await self._command("zcard", self._keys.bucket(bucket))) != 0:
            return False
        await self._command(
            "zrem",
            self._keys.registry(bucket.shard_id),
            self._keys.registry_member(bucket),
            retry="idempotent",
        )
        return True

    async def try_acquire_owner(
        self,
        role: OwnerRole,
        shard_id: int,
        token: str,
        ttl_seconds: int,
        *,
        item_type: str | None = None,
    ) -> OwnerToken | None:
        ttl_ms = self._owner_ttl_ms(token, ttl_seconds)
        # Wall-clock expiry is observational, so derive it from the conservative
        # request start instead of adding TTL after the response arrives.
        started_at_ms = time.time_ns() // 1_000_000
        owned = await self._command(
            "set",
            self._keys.owner(role, shard_id, item_type=item_type),
            token,
            nx=True,
            px=ttl_ms,
            retry="resolve_by_caller",
        )
        if not owned:
            return None
        return OwnerToken(role, shard_id, token, started_at_ms + ttl_ms, item_type)

    async def renew_owner(self, owner: OwnerToken, ttl_seconds: int) -> OwnerToken | None:
        ttl_ms = self._owner_ttl_ms(owner.token, ttl_seconds)
        started_at_ms = time.time_ns() // 1_000_000
        raw = int(
            await self._eval(
                "compare_owner",
                [
                    self._keys.owner(
                        owner.role,
                        owner.shard_id,
                        item_type=owner.item_type,
                    )
                ],
                ["renew", owner.token, ttl_ms],
                retry="resolve_by_caller",
            )
        )
        return (
            None
            if raw == 0
            else OwnerToken(
                owner.role,
                owner.shard_id,
                owner.token,
                started_at_ms + ttl_ms,
                owner.item_type,
            )
        )

    @staticmethod
    def _owner_ttl_ms(token: str, ttl_seconds: int) -> int:
        if not token:
            raise StoreOperationError("invalid_owner", "Timer owner token is empty")
        if type(ttl_seconds) is not int or not 0 < ttl_seconds <= MAX_EXACT_INTEGER // 1000:
            raise StoreOperationError("invalid_integer", "Timer owner TTL is invalid")
        return ttl_seconds * 1000

    async def assert_owner(self, owner: OwnerToken) -> None:
        raw = await self._command(
            "get",
            self._keys.owner(
                owner.role,
                owner.shard_id,
                item_type=owner.item_type,
            ),
        )
        if raw != owner.token:
            raise StoreOperationError("owner_lost", "Shard owner changed")

    async def release_owner(self, owner: OwnerToken) -> bool:
        raw = await self._eval(
            "compare_owner",
            [
                self._keys.owner(
                    owner.role,
                    owner.shard_id,
                    item_type=owner.item_type,
                )
            ],
            ["release", owner.token, ""],
            retry="idempotent",
        )
        return bool(int(raw))

    async def _open_on_loop(self) -> None:
        try:
            import redis.asyncio as redis_async
        except ImportError as exc:
            raise StoreOperationError(
                "dependency_missing",
                "Redis expiry scheduling requires the 'expiry' optional dependency",
            ) from exc
        endpoint = self._config.endpoints[0]
        if "://" not in endpoint:
            endpoint = ("rediss" if self._config.tls else "redis") + "://" + endpoint
        password = self._config.resolve_password()
        kwargs: dict[str, Any] = {
            "username": self._config.username or None,
            "password": password,
            "decode_responses": True,
            "socket_connect_timeout": self._config.connect_timeout_seconds,
            "socket_timeout": self._config.socket_timeout_seconds,
            "max_connections": self._config.max_connections,
        }
        if self._config.mode == "standalone":
            self._client = redis_async.Redis.from_url(endpoint, **kwargs)
        else:
            from redis.asyncio.cluster import ClusterNode, RedisCluster
            from redis.backoff import NoBackoff
            from redis.retry import Retry

            nodes = []
            use_tls = self._config.tls
            for raw in self._config.endpoints:
                normalized = (
                    raw
                    if "://" in raw
                    else (("rediss" if self._config.tls else "redis") + f"://{raw}")
                )
                parsed = urlsplit(normalized)
                use_tls = parsed.scheme == "rediss"
                nodes.append(ClusterNode(parsed.hostname or "", parsed.port or 6379))
            # Store retry policies must be the only authority for replaying
            # commands: a lost CAS/SET NX response cannot safely be replayed.
            # These instance-local flags leave MOVED/ASK routing intact.
            self._client = RedisCluster(
                startup_nodes=nodes,
                ssl=use_tls,
                cluster_error_retry_attempts=0,
                retry=Retry(NoBackoff(), 0),
                **kwargs,
            )
        try:
            await self._client.ping()
        except Exception as exc:
            await self._close_on_loop()
            raise self._translate_redis_error(exc, retry="read") from exc

    async def _close_on_loop(self) -> None:
        client = self._client
        if client is not None:
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                result = close()
                if result is not None:
                    await result
            # Cancellation/failure is not proof of a closed client. Keep the
            # reference until cleanup succeeds so partial startup can retry.
            self._client = None

    async def _command(
        self, name: str, *args: object, retry: RetryPolicy = "read", **kwargs: Any
    ) -> Any:
        async def operation() -> Any:
            if self._client is None:
                raise StoreOperationError("not_started", "Redis timer store is closed")
            return await self._run_with_retry(
                lambda: getattr(self._client, name)(*args, **kwargs),
                retry=retry,
            )

        return await self._io.run(operation)

    async def _single_key_command(
        self,
        command: str,
        key: str,
        *,
        retry: RetryPolicy,
    ) -> Any:
        # redis-py 5.3.1 routes DEL/UNLINK helpers through ClusterPipeline,
        # which skips execution when cluster_error_retry_attempts is zero.
        return await self._command("execute_command", command, key, retry=retry)

    async def _eval(
        self,
        script: str,
        keys: Sequence[str],
        args: Sequence[object],
        *,
        retry: RetryPolicy,
    ) -> Any:
        return await self._io.run(lambda: self._eval_on_loop(script, keys, args, retry=retry))

    async def _eval_on_loop(
        self,
        script: str,
        keys: Sequence[str],
        args: Sequence[object],
        *,
        retry: RetryPolicy,
    ) -> Any:
        if self._client is None:
            raise StoreOperationError("not_started", "Redis timer store is closed")
        source = self._read_script(script)
        sha = self._script_shas[script]
        try:
            return await self._run_with_retry(
                lambda: self._client.evalsha(sha, len(keys), *keys, *args),
                retry=retry,
            )
        except StoreOperationError as exc:
            if exc.code != "noscript":
                raise
        return await self._run_with_retry(
            lambda: self._client.eval(source, len(keys), *keys, *args),
            retry=retry,
        )

    async def _run_with_retry(self, operation: Any, *, retry: RetryPolicy) -> Any:
        """Retry safe Redis operations inside the executor's total time budget."""
        delay = 0.01
        while True:
            try:
                return await operation()
            except StoreOperationError:
                raise
            except Exception as exc:
                translated = self._translate_redis_error(exc, retry=retry)
                if retry == "resolve_by_caller" or not translated.retryable:
                    raise translated from exc
                await asyncio.sleep(delay)
                delay = min(delay * 2, 0.2)

    def _read_script(self, name: str) -> str:
        if name not in self._scripts:
            base = resources.files(__package__).joinpath("lua")
            common = base.joinpath("_common.lua").read_text(encoding="utf-8")
            body = base.joinpath(f"{name}.lua").read_text(encoding="utf-8")
            self._scripts[name] = common + "\n" + body
            self._script_shas[name] = hashlib.sha1(
                self._scripts[name].encode("utf-8"), usedforsecurity=False
            ).hexdigest()
        return self._scripts[name]

    def _translate_redis_error(self, exc: Exception, *, retry: RetryPolicy) -> StoreOperationError:
        message = str(exc)
        normalized = message.lower()
        known = (
            "noscript",
            "invalid_identity",
            "invalid_report",
            "invalid_integer",
            "invalid_owner",
            "invalid_owner_action",
            "invalid_claim_time",
            "invalid_cas_action",
            "invalid_publish",
            "metadata_conflict",
            "owner_lost",
            "progress_conflict",
            "namespace_corrupt",
            "wrong_key_type",
        )
        code = (
            "noscript"
            if type(exc).__name__ == "NoScriptError"
            else (
                "wrong_key_type"
                if "wrongtype" in normalized
                else next((item for item in known if item in normalized), "redis_error")
            )
        )
        unknown = retry == "resolve_by_caller" and code == "redis_error"
        return StoreOperationError(
            code,
            message or type(exc).__name__,
            retryable=code == "redis_error",
            outcome_unknown=unknown,
        )
