# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CacheRuntime persistence for distributed expiry scheduling."""

from __future__ import annotations

import asyncio
import json
import time
from importlib import resources
from typing import Any, Literal, Sequence, cast

from openviking.pyagfs.protocols import CacheRuntimeHandleProtocol

from .api import ExpiryRegistration, ExpiryRegistrationResult, ItemIdentity
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


class CacheRuntimeExpiryStore:
    """Persist expiry state through one named RAGFS CacheRuntime."""

    def __init__(
        self,
        cache_runtime: CacheRuntimeHandleProtocol,
        keys: ExpiryKeys,
        executor: AsyncExecutor,
        metrics: ExpiryMetrics,
    ) -> None:
        """Initialize the Store with its shared Runtime, key factory, and executor."""
        self._cache_runtime = cache_runtime
        self._keys = keys
        self._io = executor
        self._metrics = metrics
        self._scripts: dict[str, str] = {}
        self._ready = False
        self._enabled_consumers: set[str] = set()

    async def open(self) -> None:
        """Register expiry scripts and verify the shared Runtime is reachable."""
        for name in (
            "ensure_metadata",
            "upsert_resource",
            "publish_minute",
            "cleanup_source_page",
            "defer_registration",
            "cas_zset_member",
            "compare_owner",
            "read_zset_page",
            "read_time",
            "read_hash",
            "write_hash",
            "set_progress_if_absent",
            "stage_items",
            "zcard",
        ):
            self._cache_runtime.register_script(f"expiry.v1.{name}", self._read_script(name))
        await self._io.run(lambda: self._run_with_retry(self._cache_runtime.ping, retry="read"))
        self._ready = True

    async def close(self) -> None:
        """Stop accepting Store calls without closing the shared CacheRuntime."""
        self._ready = False

    async def ensure_namespace(self, spec: NamespaceSpec) -> None:
        """Create or validate durable namespace metadata and shard progress keys."""
        key = self._keys.namespace()
        expected = {
            "shard_count": str(spec.shard_count),
            "hash_name": spec.hash_name,
            "hash_seed": str(spec.hash_seed),
            "identity_encoding": spec.identity_encoding,
        }
        current = await self._read_hash(key)
        self._enabled_consumers = {
            field.removeprefix("consumer_enabled:")
            for field, value in current.items()
            if field.startswith("consumer_enabled:") and value == "1"
        }
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
            await self._write_hash(key, created, retry="idempotent")
        for shard_id in range(spec.shard_count):
            progress_key = self._keys.progress(shard_id)
            if initializing:
                await self._eval(
                    "set_progress_if_absent",
                    [progress_key],
                    ["0"],
                    retry="idempotent",
                )
            elif await self._cache_call(
                lambda progress_key=progress_key: self._cache_runtime.get(progress_key), retry="read"
            ) is None:
                raise StoreOperationError(
                    "namespace_corrupt", f"Timer progress is missing for shard {shard_id}"
                )
        if initializing:
            await self._write_hash(key, {"state": "READY"}, retry="idempotent")
        self._ready = True

    def consumer_enabled(self, item_type: str) -> bool:
        """Return whether the named callback consumer has been enabled."""
        return item_type in self._enabled_consumers

    async def enable_consumer(self, item_type: str) -> None:
        """Persist and cache the enabled state for one callback consumer."""
        if not self.consumer_enabled(item_type):
            await self._write_hash(
                self._keys.namespace(),
                {f"consumer_enabled:{item_type}": "1"},
                retry="idempotent",
            )
            self._enabled_consumers.add(item_type)

    async def ensure_metadata(self, item_id: str, task: ExpiryRegistration) -> None:
        """Create or refresh immutable metadata for one registered item."""
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
        """Read one item's metadata or return None when it has expired."""
        raw = await self._read_hash(self._keys.metadata(item_id))
        if not raw:
            return None
        required = ("item_type", "account_id", "user_id", "local_id")
        if any(name not in raw for name in required):
            raise StoreOperationError("metadata_corrupt", "Timer metadata is incomplete")
        return ItemMetadata(ItemIdentity(*(str(raw[name]) for name in required)))

    async def upsert_resource(
        self, shard_id: int, item_id: str, task: ExpiryRegistration
    ) -> ExpiryRegistrationResult:
        """Register one resource due time and return the accepted timestamp."""
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
        """Remove one resource registration and optionally its metadata."""
        await self._cache_call(
            lambda: self._cache_runtime.zrem(self._keys.source(shard_id), [item_id]),
            retry="idempotent",
        )
        if delete_metadata:
            await self._cache_call(
                lambda: self._cache_runtime.delete([self._keys.metadata(item_id)]),
                retry="idempotent",
            )

    async def read_progress(self, shard_id: int) -> int:
        """Read one shard's durable publication progress."""
        raw = await self._cache_call(
            lambda: self._cache_runtime.get(self._keys.progress(shard_id)), retry="read"
        )
        if raw is None:
            raise StoreOperationError("namespace_corrupt", "Timer progress is missing")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise StoreOperationError("namespace_corrupt", "Timer progress is invalid") from exc

    async def read_scan_window(self, shard_id: int) -> ShardWindow:
        """Build the current source scan window from progress and Redis time."""
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
        """Read one cursor page of due source items."""
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
        """Delete staged bucket keys while the scheduler owner remains valid."""
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
            await self._cache_call(
                lambda key=key: self._cache_runtime.unlink([key]), retry="idempotent"
            )

    async def stage_items(
        self, owner: OwnerToken, bucket: BucketRef, item_ids: Sequence[str]
    ) -> None:
        """Stage resource identifiers in one bucket after owner validation."""
        if not item_ids:
            return
        await self.assert_owner(owner)
        await self._eval(
            "stage_items",
            [self._keys.bucket(bucket)],
            item_ids,
            retry="resolve_by_caller",
        )

    async def publish_minute(
        self,
        owner: OwnerToken,
        *,
        expected_progress_ms: int,
        minute_ms: int,
        buckets: Sequence[BucketRef],
    ) -> PublishResult:
        """Publish staged buckets and atomically advance shard progress."""
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
        status = self._decode_bytes(raw[0], "Invalid timer publish result").decode()
        if status not in {"published", "already_published"}:
            raise StoreOperationError("namespace_corrupt", "Invalid timer publish result")
        return PublishResult(
            cast(Literal["published", "already_published"], status),
            int(raw[1]),
        )

    async def cleanup_source_page(self, shard_id: int, through_ms: int, *, limit: int) -> int:
        """Delete at most one bounded page of published source registrations."""
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
        """Read one cursor page of published bucket registrations."""
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
        """Read Redis time in milliseconds from the namespace owner's node."""
        return await self._read_time_ms_for_key(self._keys.namespace())

    @staticmethod
    def _decode_redis_time(raw: Any) -> int:
        """Validate and convert a Redis TIME reply into milliseconds."""
        try:
            seconds, microseconds = (int(value) for value in raw)
        except (TypeError, ValueError) as exc:
            raise StoreOperationError("namespace_corrupt", "Invalid Redis time") from exc
        if not 0 < seconds < SCORE_TIME_BASE or not 0 <= microseconds < 1_000_000:
            raise StoreOperationError("namespace_corrupt", "Redis time is outside supported range")
        return seconds * 1000 + microseconds // 1000

    async def _read_time_ms_for_key(self, key: str) -> int:
        """Read TIME from the primary owning key so source cutoffs share its clock."""

        raw = await self._eval("read_time", [key], [], retry="read")
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
        """Read a scored cursor page through the Redis-compatible Lua script."""
        raw = await self._eval(
            "read_zset_page",
            [key],
            [
                lower_exclusive,
                "" if upper_inclusive is None else upper_inclusive,
                "" if after_score is None else after_score,
                after_member or "",
                limit,
            ],
            retry="read",
        )
        data = json.loads(self._decode_bytes(raw, "Timer page is invalid"))
        items = tuple((str(member), int(score)) for member, score in data["items"])
        next_raw = data.get("next")
        next_cursor = (int(next_raw[0]), str(next_raw[1])) if next_raw else None
        return items, next_cursor

    async def read_bucket_page(
        self,
        bucket: BucketRef,
        *,
        attempt: int,
        limit: int,
    ) -> BucketPage:
        """Read one retry-layer page from a staged bucket."""
        lower, upper = claim_layer_bounds(attempt)
        raw, _ = await self._read_zset_page(
            self._keys.bucket(bucket),
            lower_exclusive=lower - 1,
            upper_inclusive=upper,
            after_score=None,
            after_member=None,
            limit=limit,
        )
        items: list[BucketItem] = []
        try:
            for item_id, score in raw:
                if not lower <= score <= upper:
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
        """Reschedule one published bucket when its callback owner still holds it."""
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
        """Atomically claim one ready bucket item for the requested attempt."""
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
        """Move a still-owned claim to its next retry layer."""
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
        """Delete a claim only when its score still matches."""
        raw = await self._eval(
            "cas_zset_member",
            [self._keys.bucket(claim.bucket)],
            ["delete", claim.item_id, claim.claim_score, ""],
            retry="idempotent",
        )
        return bool(int(raw))

    async def remove_registration_if_empty(self, bucket: BucketRef) -> bool:
        """Remove a registry member after confirming its bucket is empty."""
        if int(
            await self._eval("zcard", [self._keys.bucket(bucket)], [], retry="read")
        ) != 0:
            return False
        await self._cache_call(
            lambda: self._cache_runtime.zrem(
                self._keys.registry(bucket.shard_id),
                [self._keys.registry_member(bucket)],
            ),
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
        """Acquire one expiring owner token without replaying an uncertain result."""
        ttl_ms = self._owner_ttl_ms(token, ttl_seconds)
        # Wall-clock expiry is observational, so derive it from the conservative
        # request start instead of adding TTL after the response arrives.
        started_at_ms = time.time_ns() // 1_000_000
        owned = await self._cache_call(
            lambda: self._cache_runtime.set(
                self._keys.owner(role, shard_id, item_type=item_type),
                token.encode(),
                ttl_seconds=ttl_ms / 1000,
                if_not_exists=True,
            ),
            retry="resolve_by_caller",
        )
        if not owned:
            return None
        return OwnerToken(role, shard_id, token, started_at_ms + ttl_ms, item_type)

    async def renew_owner(self, owner: OwnerToken, ttl_seconds: int) -> OwnerToken | None:
        """Renew an owner token only while its stored value still matches."""
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
        """Validate an owner token TTL and convert it to milliseconds."""
        if not token:
            raise StoreOperationError("invalid_owner", "Timer owner token is empty")
        if type(ttl_seconds) is not int or not 0 < ttl_seconds <= MAX_EXACT_INTEGER // 1000:
            raise StoreOperationError("invalid_integer", "Timer owner TTL is invalid")
        return ttl_seconds * 1000

    async def assert_owner(self, owner: OwnerToken) -> None:
        """Raise when the current owner key no longer contains the token."""
        raw = await self._cache_call(
            lambda: self._cache_runtime.get(
                self._keys.owner(
                    owner.role,
                    owner.shard_id,
                    item_type=owner.item_type,
                )
            ),
            retry="read",
        )
        if raw != owner.token.encode():
            raise StoreOperationError("owner_lost", "Shard owner changed")

    async def release_owner(self, owner: OwnerToken) -> bool:
        """Release an owner token only while its stored value still matches."""
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

    async def _eval(
        self,
        script: str,
        keys: Sequence[str],
        args: Sequence[object],
*,
        retry: RetryPolicy,
    ) -> Any:
        """Execute one registered expiry Lua script through CacheRuntime."""
        return await self._cache_call(
            lambda: self._cache_runtime.execute_script(
                f"expiry.v1.{script}",
                list(keys),
                [self._encode_argument(value) for value in args],
            ),
            retry=retry,
        )

    async def _cache_call(self, operation: Any, *, retry: RetryPolicy) -> Any:
        """Run one CacheRuntime operation through the expiry executor."""
        if not self._ready:
            raise StoreOperationError("not_started", "Expiry CacheRuntime store is closed")
        return await self._io.run(lambda: self._run_with_retry(operation, retry=retry))

    async def _read_hash(self, key: str) -> dict[str, str]:
        """Read one Redis hash through the named Runtime script."""
        raw = await self._eval("read_hash", [key], [], retry="read")
        try:
            decoded = json.loads(self._decode_bytes(raw, "Timer hash is invalid"))
            if not isinstance(decoded, dict):
                raise ValueError
            return {str(field): str(value) for field, value in decoded.items()}
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StoreOperationError("namespace_corrupt", "Timer hash is invalid") from exc

    async def _write_hash(
        self, key: str, values: dict[str, str], *, retry: RetryPolicy
    ) -> None:
        """Write one or more hash fields through a persistent Lua operation."""
        args: list[str] = []
        for field, value in values.items():
            args.extend((field, value))
        await self._eval("write_hash", [key], args, retry=retry)

    async def _run_with_retry(self, operation: Any, *, retry: RetryPolicy) -> Any:
        """Retry safe CacheRuntime operations inside the executor's total time budget."""
        delay = 0.01
        while True:
            try:
                return await operation()
            except StoreOperationError:
                raise
            except Exception as exc:
                translated = self._translate_runtime_error(exc, retry=retry)
                if retry == "resolve_by_caller" or not translated.retryable:
                    raise translated from exc
                await asyncio.sleep(delay)
                delay = min(delay * 2, 0.2)

    def _read_script(self, name: str) -> str:
        """Load and cache one expiry Lua source prefixed with shared helpers."""
        if name not in self._scripts:
            base = resources.files(__package__).joinpath("lua")
            common = base.joinpath("_common.lua").read_text(encoding="utf-8")
            body = base.joinpath(f"{name}.lua").read_text(encoding="utf-8")
            self._scripts[name] = common + "\n" + body
        return self._scripts[name]

    @staticmethod
    def _encode_argument(value: object) -> bytes:
        """Encode one Lua argument in the bytes-only CacheRuntime contract."""
        if isinstance(value, bytes):
            return value
        return str(value).encode()

    @staticmethod
    def _decode_bytes(value: Any, message: str) -> bytes:
        """Decode a CacheRuntime byte result or report corrupt expiry state."""
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        raise StoreOperationError("namespace_corrupt", message)

    def _translate_runtime_error(self, exc: Exception, *, retry: RetryPolicy) -> StoreOperationError:
        """Map native CacheRuntime failures into the expiry error contract."""
        message = str(exc)
        normalized = message.lower()
        known = (
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
            "invalid_hash_write",
        )
        code = (
            "wrong_key_type"
            if "wrongtype" in normalized
            else next((item for item in known if item in normalized), "redis_error")
        )
        unknown = retry == "resolve_by_caller" and code == "redis_error"
        return StoreOperationError(
            code,
            message or type(exc).__name__,
            retryable=code == "redis_error",
            outcome_unknown=unknown,
        )
