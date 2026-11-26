# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic replacement of registered secrets with owner-bound references."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import ChainMap, Counter
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, Literal, NamedTuple

from openviking.core.namespace import (
    canonical_user_root,
    classify_uri,
    is_session_uri,
    owner_space_for_uri,
    resolve_request_uri,
)
from openviking.observability.log_safety import safe_uri_for_log
from openviking.privacy.content import PrivacyContent
from openviking.privacy.models import UserPrivacyConfigVersion
from openviking.privacy.reference import PrivacyFieldReference
from openviking.pyagfs import get_privacy_matcher_class
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.privacy.service import UserPrivacyConfigService

logger = get_logger(__name__)

_AMBIGUOUS_PLACEHOLDER = "{{ov_privacy:ambiguous}}"
MIN_PROTECTED_VALUE_CHARS = 6
DEFAULT_MATCHER_CACHE_TTL_SECONDS = 3600.0
DEFAULT_SANITIZER_MAX_CONTENT_BYTES = 16 * 1024 * 1024
DEFAULT_SANITIZER_MAX_REPLACEMENTS = 10000


@dataclass(frozen=True)
class PrivacyReplacement:
    """Original match span, or its enclosing scalar for structured content."""

    start: int
    end: int
    reference: str


def merge_privacy_spans(
    spans: list[tuple[int, int, str | None]],
) -> Iterator[tuple[int, int, str | None]]:
    """Union overlapping hits; reuse a reference only when it covers the full union."""
    if not spans:
        return
    spans.sort(key=lambda span: (span[0], -span[1]))
    start, end, reference = spans[0]
    for left, right, candidate in spans[1:]:
        if left >= end:
            yield start, end, reference
            start, end, reference = left, right, candidate
        elif right > end:
            end, reference = right, None
        elif left == start and right == end and candidate is not None:
            reference = candidate
    yield start, end, reference


@dataclass(frozen=True)
class PrivacySanitizationResult:
    content: str
    replacements: tuple[PrivacyReplacement, ...] = ()
    ambiguous_references: tuple[tuple[str, ...], ...] = ()
    match_limit_exceeded: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.replacements)


@dataclass(frozen=True)
class _PreparedMatcher:
    native: Any
    ambiguous: Mapping[str, tuple[str, ...]]
    has_values: bool
    # References into the ``auto`` category, i.e. not yet saved under a name (seal only).
    temporary: frozenset[str] = frozenset()
    # The Credential revision read before the catalog was (seal only).
    revision: str = ""


class SealMatcher(NamedTuple):
    find: Callable[[str], list[PrivacyReplacement]]
    # References that point into ``auto``.
    temporary: frozenset[str]
    # Read before the catalog, so a concurrent write can only make it older
    # than what ``find`` matches, never newer.
    revision: str


class PrivacySanitizer:
    """Sanitize text with the active sensitive values owned by the current user."""

    def __init__(
        self,
        privacy: "UserPrivacyConfigService",
        *,
        cache_ttl_seconds: float = DEFAULT_MATCHER_CACHE_TTL_SECONDS,
        max_content_bytes: int = DEFAULT_SANITIZER_MAX_CONTENT_BYTES,
        max_replacements: int = DEFAULT_SANITIZER_MAX_REPLACEMENTS,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ):
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        if max_content_bytes <= 0:
            raise ValueError("max_content_bytes must be positive")
        if max_replacements <= 0:
            raise ValueError("max_replacements must be positive")
        self._privacy = privacy
        self._cache_ttl_seconds = float(cache_ttl_seconds)
        self._max_content_bytes = int(max_content_bytes)
        self._max_replacements = int(max_replacements)
        self._monotonic_clock = monotonic_clock
        matcher_class: Any = get_privacy_matcher_class()
        self._matcher_class = matcher_class
        self._cache: dict[
            tuple[str, str, str, str, tuple[str, str] | None, int, str],
            tuple[float, _PreparedMatcher],
        ] = {}
        self._cache_lock = asyncio.Lock()
        self._ambiguous_matches = 0
        self.write_failures: Counter[str] = Counter()
        self._ambiguity_lock = Lock()

    @property
    def max_content_bytes(self) -> int:
        """Maximum UTF-8 input size accepted by this sanitizer."""
        return self._max_content_bytes

    @property
    def max_replacements(self) -> int:
        return self._max_replacements

    @property
    def ambiguous_matches(self) -> int:
        """Number of ambiguous occurrences observed by this sanitizer instance."""
        return self._ambiguous_matches

    @staticmethod
    def _privacy_read_context(ctx: RequestContext) -> RequestContext:
        """Return the actor's context without GLOBAL resource bypass for private catalog reads."""
        if ctx.role == Role.GLOBAL and ctx.bypass_acl:
            return replace(ctx, bypass_acl=False)
        return ctx

    async def prepare_matcher(
        self,
        ctx: RequestContext,
        *,
        scope: tuple[str, str] | None = None,
        settings: ResolvedPrivacyConfig | None = None,
        uri: str = "",
        snapshot: UserPrivacyConfigVersion | None = None,
    ) -> Callable[[str], list[PrivacyReplacement]]:
        """Prepare a cached, bounded match query for a batch of original texts.

        The callable may run in a worker thread. It returns positions and existing
        references so rule extraction can combine hits before rewriting content.
        A snapshot may be supplied under the target lock; version zero denotes
        a target without a persisted configuration.
        """
        ctx = self._privacy_read_context(ctx)
        if settings is None:
            settings = await self._privacy.get_settings(ctx)
        if settings.mode == "off":
            return lambda _: []
        matcher = await self._matcher(ctx, scope=scope, settings=settings, snapshot=snapshot)
        return self._finder(matcher, uri)

    async def prepare_seal_matcher(
        self, ctx: RequestContext, *, settings: ResolvedPrivacyConfig
    ) -> SealMatcher:
        """Prepare the seal endpoint's matcher, its temporary references and revision.

        Unlike the storage paths, it matches the ``auto`` category as well as
        ``credential``, and the values of earlier stored versions of each
        ``credential`` target, mapped to the field's reference. On conflict a
        current ``credential`` value beats an earlier one, which beats an
        ``auto`` one. References into ``auto`` are temporary. The matcher and
        the temporary set are built together and cached per revision.
        """
        matcher = await self._matcher(ctx, settings=settings, seal=True)
        return SealMatcher(self._finder(matcher, ""), matcher.temporary, matcher.revision)

    def _finder(
        self, matcher: _PreparedMatcher, uri: str
    ) -> Callable[[str], list[PrivacyReplacement]]:
        def find(content: str) -> list[PrivacyReplacement]:
            try:
                replacements, _, exceeded = matcher.native.find_limited(
                    content, self._max_replacements, self._max_content_bytes
                )
            except OverflowError:
                raise ResourceExhaustedError(
                    "Content exceeds the Privacy sanitizer size limit"
                ) from None
            if exceeded:
                raise ResourceExhaustedError(
                    "Content exceeds the Privacy sanitizer replacement limit"
                )
            matches = [PrivacyReplacement(*item) for item in replacements]
            self._check_ambiguity(matcher, matches, uri=uri)
            return matches

        return find

    async def sanitize(
        self,
        content: str,
        *,
        ctx: RequestContext,
        uri: str = "",
        max_replacements: int | None = None,
        fail_on_match_limit: bool = True,
        settings: ResolvedPrivacyConfig | None = None,
    ) -> PrivacySanitizationResult:
        """Replace registered values in an explicitly selected document."""
        if (
            not content
            or self._is_privacy_storage_uri(uri, ctx)
            or PrivacyContent.is_skill_metadata(uri)
        ):
            return PrivacySanitizationResult(content=content)
        ctx = self._privacy_read_context(ctx)
        if settings is None:
            settings = await self._privacy.get_settings(ctx)
        if settings.mode == "off":
            return PrivacySanitizationResult(content=content)
        effective_max_replacements = (
            self._max_replacements if max_replacements is None else max_replacements
        )
        if effective_max_replacements <= 0:
            raise ValueError("max_replacements must be positive")
        scope = self.scope_from_uri(uri, ctx)
        matcher = await self._matcher(ctx, scope=scope, settings=settings)
        if not matcher.has_values:
            return PrivacySanitizationResult(content=content)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > self._max_content_bytes:
            raise ResourceExhaustedError(
                "Content exceeds the Privacy sanitizer size limit",
                details={
                    "uri": uri,
                    "size_bytes": content_bytes,
                    "max_bytes": self._max_content_bytes,
                },
            )
        return await asyncio.to_thread(
            self._sanitize_content,
            content,
            uri,
            matcher,
            effective_max_replacements,
            fail_on_match_limit,
        )

    def _sanitize_content(
        self,
        content: str,
        uri: str,
        matcher: _PreparedMatcher,
        max_replacements: int,
        fail_on_match_limit: bool,
    ) -> PrivacySanitizationResult:
        """Parse, match and render outside the server event loop."""
        document = PrivacyContent(content, uri, preserve_skill_metadata=True)
        changed, exceeded = matcher.native.sanitize_many_limited(
            [item.value for item in document.strings], max_replacements
        )
        if exceeded:
            if fail_on_match_limit:
                raise ResourceExhaustedError(
                    "Content exceeds the Privacy sanitizer replacement limit",
                    details={"uri": uri, "max_replacements": max_replacements},
                )
            return PrivacySanitizationResult(content=content, match_limit_exceeded=True)
        replacements: list[PrivacyReplacement] = []
        rewritten: dict[int, str] = {}
        for index, value, matches, _ in changed:
            item = document.strings[index]
            rewritten[item.start] = value
            replacements.extend(
                PrivacyReplacement(
                    start=item.start if item.quoted else item.start + start,
                    end=item.end if item.quoted else item.start + end,
                    reference=reference,
                )
                for start, end, reference in matches
            )
        return PrivacySanitizationResult(
            content=document.render(lambda item: rewritten.get(item.start, item.value)),
            replacements=tuple(replacements),
            ambiguous_references=self._check_ambiguity(matcher, replacements, uri=uri),
        )

    async def settings_for_uri(self, uri: str, ctx: RequestContext) -> ResolvedPrivacyConfig | None:
        """Return settings when automatic matching applies to this content URI."""
        uri = resolve_request_uri(uri, ctx)
        if self._is_privacy_storage_uri(uri, ctx) or PrivacyContent.is_skill_metadata(uri):
            return None
        owner = owner_space_for_uri(uri)
        if owner and owner != ctx.user.user_id:
            return None
        settings = await self._privacy.get_settings(self._privacy_read_context(ctx))
        if settings.mode == "off":
            return None
        if settings.mode != "auto_strict" and self.scope_from_uri(uri, ctx) is None:
            return None
        return settings

    async def sanitize_for_write(
        self,
        content: str,
        *,
        ctx: RequestContext,
        uri: str,
        settings: ResolvedPrivacyConfig | None = None,
    ) -> str:
        """Apply automatic matching, keeping input on overflow or structure failure."""
        if not content:
            return content
        if settings is None:
            settings = await self.settings_for_uri(uri, ctx)
        if settings is None:
            return content
        reason = "match_limit_exceeded"
        try:
            result = await self.sanitize(
                content, ctx=ctx, uri=uri, settings=settings, fail_on_match_limit=False
            )
            if not result.match_limit_exceeded:
                return result.content
        except InvalidArgumentError as exc:
            if exc.details.get("reason") != "privacy_structure_changed":
                raise
            reason = "privacy_structure_changed"
        self.write_failures[reason] += 1
        target_id = safe_uri_for_log(uri)
        logger.error(
            "privacy write kept original content target_id=%s error_code=%s",
            target_id,
            reason,
            extra={"degraded": True, "target_id": target_id, "error_code": reason},
        )
        return content

    async def _matcher(
        self,
        ctx: RequestContext,
        *,
        scope: tuple[str, str] | None = None,
        settings: ResolvedPrivacyConfig,
        snapshot: UserPrivacyConfigVersion | None = None,
        seal: bool = False,
    ) -> _PreparedMatcher:
        settings_hash = hashlib.sha256(
            json.dumps(asdict(settings), sort_keys=True).encode("utf-8")
        ).hexdigest()
        if seal:
            # A separate cache entry: the storage paths must keep matching
            # current Credential values only.
            settings_hash += ":seal"
        cache_key, current = await self._matcher_state(ctx, scope, settings_hash, snapshot)
        now = self._monotonic_clock()
        cached = self._cache.get(cache_key)
        if cached is not None and now - cached[0] < self._cache_ttl_seconds:
            return cached[1]
        async with self._cache_lock:
            cache_key, current = await self._matcher_state(ctx, scope, settings_hash, snapshot)
            now = self._monotonic_clock()
            cached = self._cache.get(cache_key)
            if cached is not None and now - cached[0] < self._cache_ttl_seconds:
                return cached[1]
            shared_key = (*cache_key[:4], None, 0, "")
            shared = self._cache.get(shared_key)
            if shared is None or now - shared[0] >= self._cache_ttl_seconds:
                shared = (
                    now,
                    replace(
                        await self._build_matcher(
                            ctx,
                            scope=None,
                            current=None,
                            settings=settings,
                            seal=seal,
                        ),
                        revision=cache_key[2],
                    ),
                )
            matcher = shared[1]
            if cache_key[4] is not None:
                matcher = await self._build_matcher(
                    ctx, scope=scope, current=current, settings=settings, preferred=matcher
                )
            owner_key = cache_key[:2]
            self._cache = {
                key: item
                for key, item in self._cache.items()
                if now - item[0] < self._cache_ttl_seconds
                # Keep other settings (the seal matcher, other modes) of the same
                # revision so alternating callers do not rebuild each other's.
                and (
                    key[:2] != owner_key or (key[2] == cache_key[2] and key[3:5] != cache_key[3:5])
                )
            }
            self._cache[shared_key] = shared
            if matcher is not shared[1]:
                self._cache[cache_key] = (shared[0], matcher)
            return matcher

    async def _matcher_state(
        self,
        ctx: RequestContext,
        scope: tuple[str, str] | None,
        settings_hash: str,
        snapshot: UserPrivacyConfigVersion | None,
    ) -> tuple[
        tuple[str, str, str, str, tuple[str, str] | None, int, str],
        UserPrivacyConfigVersion | None,
    ]:
        current = snapshot
        if current is None and scope:
            current = await self._privacy.get_current(ctx, *scope)
        if scope is None or current is None or not current.values:
            scope, current = None, None
        return (
            (
                ctx.account_id,
                ctx.user.user_id,
                # Persisted and shared by workers, so another worker's write
                # invalidates this cache on the next call; the TTL is a backstop.
                await self._privacy.credential_revision(ctx),
                settings_hash,
                scope,
                current.version if current else 0,
                current.created_at if current else "",
            ),
            current,
        )

    async def _build_matcher(
        self,
        ctx: RequestContext,
        *,
        scope: tuple[str, str] | None,
        current: UserPrivacyConfigVersion | None,
        settings: ResolvedPrivacyConfig,
        preferred: _PreparedMatcher | None = None,
        seal: bool = False,
    ) -> _PreparedMatcher:
        from openviking.privacy.rules import prepare_privacy_extractor
        from openviking.privacy.service import AUTO_CATEGORY, SEALED_CATEGORIES

        discover = prepare_privacy_extractor(settings=settings)
        never_mask = set(settings.never_mask)
        references: dict[str, list[PrivacyFieldReference]] = {}
        # Lower-priority candidates, in priority order: earlier Credential
        # values, then current ``auto`` values (seal only).
        fallbacks: list[dict[str, list[PrivacyFieldReference]]] = [{}, {}]
        temporary: set[str] = set()
        categories = SEALED_CATEGORIES if seal else ("credential",)
        configs = (
            [(*scope, current)]
            if scope
            else [
                (category, target, await self._privacy.get_current(ctx, category, target))
                for category in categories
                for target in await self._privacy.list_targets(ctx, category)
            ]
        )

        def protected(value: Any) -> bool:
            return (
                isinstance(value, str)
                and len(value) >= MIN_PROTECTED_VALUE_CHARS
                and value not in never_mask
            )

        for category, target_key, config in configs:
            if config is None:
                continue
            if category == AUTO_CATEGORY:
                temporary.update(
                    PrivacyFieldReference(ctx.user.user_id, category, target_key, name).render()
                    for name in config.values
                )
            if seal and category == "credential":
                # An ``auto`` target never has a second version.
                await self._collect_earlier_values(
                    ctx, category, target_key, config, protected, fallbacks[0]
                )
            current_values = references if category != AUTO_CATEGORY else fallbacks[1]
            for field_name, value in config.values.items():
                if not protected(value):
                    continue
                if category not in SEALED_CATEGORIES:
                    try:
                        matches = discover(value)
                    except InvalidArgumentError:
                        continue
                    if len(matches) != 1 or matches[0].start != 0 or matches[0].end != len(value):
                        continue
                current_values.setdefault(value, []).append(
                    PrivacyFieldReference(ctx.user.user_id, category, target_key, field_name)
                )

        for fallback in fallbacks:
            for secret, candidates in fallback.items():
                # A higher-priority holder of the same value decides its reference.
                references.setdefault(secret, candidates)

        targets: dict[str, list[str]] = {}
        ambiguous = {}
        for secret, candidates in references.items():
            ordered = sorted(
                set(candidates),
                key=lambda item: (item.category, item.target_key, item.field),
            )
            selected = tuple(item.render() for item in ordered)
            targets[secret] = [selected[0]]
            if len(selected) > 1:
                ambiguous[selected[0]] = selected
        if preferred is not None and not targets:
            return preferred
        if preferred is not None and preferred.has_values:
            native = self._matcher_class(
                targets, _AMBIGUOUS_PLACEHOLDER, preferred=preferred.native
            )
            return _PreparedMatcher(native, ChainMap(ambiguous, preferred.ambiguous), True)
        return _PreparedMatcher(
            self._matcher_class(targets, _AMBIGUOUS_PLACEHOLDER),
            ambiguous,
            bool(targets),
            frozenset(temporary),
        )

    async def _collect_earlier_values(
        self,
        ctx: RequestContext,
        category: str,
        target_key: str,
        config: UserPrivacyConfigVersion,
        protected: Callable[[Any], bool],
        earlier: dict[str, list[PrivacyFieldReference]],
    ) -> None:
        """Collect a target's earlier values, each mapped to its field's reference."""
        owner = ctx.user.user_id
        for number in await self._privacy.list_versions(ctx, category, target_key):
            if number == config.version:
                continue
            version = await self._privacy.get_version(ctx, category, target_key, number)
            if version is None:
                continue
            for field_name, value in version.values.items():
                if protected(value):
                    earlier.setdefault(value, []).append(
                        PrivacyFieldReference(owner, category, target_key, field_name)
                    )

    def _check_ambiguity(
        self,
        matcher: _PreparedMatcher,
        replacements: tuple[PrivacyReplacement, ...] | list[PrivacyReplacement],
        *,
        uri: str = "",
    ) -> tuple[tuple[str, ...], ...]:
        if not matcher.ambiguous:
            return ()
        counts = Counter(
            matcher.ambiguous[item.reference]
            for item in replacements
            if item.reference in matcher.ambiguous
        )
        if not counts:
            return ()
        ambiguous = tuple(counts)
        occurrences = sum(counts.values())
        with self._ambiguity_lock:
            self._ambiguous_matches += occurrences
        strict = os.environ.get("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        target_id = safe_uri_for_log(uri)
        logger.info(
            "privacy ambiguous matches total=%d strict=%s target_id=%s",
            occurrences,
            strict,
            target_id,
            extra={"degraded": True, "total": occurrences, "target_id": target_id},
        )
        if strict:
            raise InvalidArgumentError(
                "Content contains a Privacy value mapped to multiple fields; "
                "insert the intended owner-bound reference explicitly",
                details={"uri": uri, "candidate_references": [list(items) for items in ambiguous]},
            )
        return ambiguous

    @staticmethod
    def scope_from_uri(
        uri: str, ctx: RequestContext
    ) -> tuple[Literal["skill", "session"], str] | None:
        """Return a Skill/Session target, excluding other users' private URIs."""
        if not uri:
            return None
        classification = classify_uri(uri)
        if classification.scope == "~":
            uri = resolve_request_uri(uri, ctx)
            classification = classify_uri(uri)
        parts = classification.parts
        if classification.scope == "user" and parts[1:2] != (ctx.user.user_id,):
            return None
        index = classification.content_index
        if classification.is_skill and index is not None and len(parts) > index + 1:
            return "skill", parts[index + 1]
        if is_session_uri(uri) and len(parts) > 3:
            from openviking.privacy.session_extractor import session_privacy_target

            return "session", session_privacy_target(parts[3])
        return None

    @staticmethod
    def _is_privacy_storage_uri(uri: str, ctx: RequestContext) -> bool:
        user_root = canonical_user_root(ctx)
        return any(
            uri.rstrip("/") == root or uri.startswith(f"{root}/")
            for root in (f"{user_root}/privacy", f"{user_root}/settings")
        )
