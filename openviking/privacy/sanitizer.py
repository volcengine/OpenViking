# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic replacement of registered secrets with owner-bound references."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, Literal

from openviking.core.namespace import (
    canonical_user_root,
    classify_uri,
    is_session_uri,
    owner_space_for_uri,
    resolve_request_uri,
)
from openviking.core.skill_loader import SkillLoader
from openviking.privacy.models import UserPrivacyConfigVersion
from openviking.privacy.reference import PrivacyFieldReference
from openviking.pyagfs import get_privacy_matcher_class
from openviking.server.identity import RequestContext
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.privacy.service import UserPrivacyConfigService

logger = get_logger(__name__)

_AMBIGUOUS_PLACEHOLDER = "{{ov_privacy:ambiguous}}"
DEFAULT_MATCHER_CACHE_TTL_SECONDS = 3600.0
DEFAULT_SANITIZER_MAX_CONTENT_BYTES = 16 * 1024 * 1024
DEFAULT_SANITIZER_MAX_REPLACEMENTS = 10000


@dataclass(frozen=True)
class PrivacyReplacement:
    start: int
    end: int
    reference: str


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
    ambiguous: dict[str, tuple[str, ...]]


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
            tuple[str, str, int, str, tuple[str, str] | None, int, str],
            tuple[float, _PreparedMatcher],
        ] = {}
        self._cache_lock = asyncio.Lock()
        self._ambiguous_matches = 0
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

    async def prepare_matcher(
        self,
        ctx: RequestContext,
        *,
        scope: tuple[str, str] | None = None,
        settings: ResolvedPrivacyConfig | None = None,
        uri: str = "",
    ) -> Callable[[str], list[PrivacyReplacement]]:
        """Prepare a cached, bounded match query for a batch of original texts.

        The callable may run in a worker thread. It returns positions and existing
        references so rule extraction can combine hits before rewriting content.
        """
        if settings is None:
            settings = await self._privacy.get_settings(ctx)
        if settings.mode == "off":
            return lambda _: []
        matcher = await self._matcher(ctx, scope=scope, settings=settings)

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
        if not content or self._is_privacy_storage_uri(uri, ctx):
            return PrivacySanitizationResult(content=content)
        if settings is None:
            settings = await self._privacy.get_settings(ctx)
        if settings.mode == "off":
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
        scope = self.scope_from_uri(uri, ctx)
        content_start = 0
        if uri.lower().endswith((".md", ".markdown")) and (
            (scope is not None and scope[0] == "skill") or classify_uri(uri).is_skill
        ):
            if uri.endswith("/.abstract.md"):
                # Skill L0 is generated from metadata rather than body content.
                return PrivacySanitizationResult(content=content)
            frontmatter = SkillLoader.FRONTMATTER_PATTERN.match(content)
            if frontmatter is not None:
                content_start = frontmatter.start(2)
        matcher = await self._matcher(ctx, scope=scope, settings=settings)
        effective_max_replacements = (
            self._max_replacements if max_replacements is None else max_replacements
        )
        if effective_max_replacements <= 0:
            raise ValueError("max_replacements must be positive")
        (
            rewritten,
            raw_replacements,
            _,
            match_limit_exceeded,
        ) = matcher.native.sanitize_limited(content[content_start:], effective_max_replacements)
        if match_limit_exceeded and fail_on_match_limit:
            raise ResourceExhaustedError(
                "Content exceeds the Privacy sanitizer replacement limit",
                details={
                    "uri": uri,
                    "max_replacements": effective_max_replacements,
                },
            )
        replacements = tuple(
            PrivacyReplacement(
                start=start + content_start, end=end + content_start, reference=reference
            )
            for start, end, reference in raw_replacements
        )
        return PrivacySanitizationResult(
            content=content[:content_start] + rewritten,
            replacements=replacements,
            ambiguous_references=self._check_ambiguity(matcher, replacements, uri=uri),
            match_limit_exceeded=match_limit_exceeded,
        )

    async def settings_for_uri(self, uri: str, ctx: RequestContext) -> ResolvedPrivacyConfig | None:
        """Return settings when automatic matching applies to this content URI."""
        uri = resolve_request_uri(uri, ctx)
        if self._is_privacy_storage_uri(uri, ctx) or (uri.endswith("/.abstract.md") and classify_uri(uri).is_skill):
            return None
        owner = owner_space_for_uri(uri)
        if owner and owner != ctx.user.user_id:
            return None
        settings = await self._privacy.get_settings(ctx)
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
        """Apply automatic matching to the content selected by user settings."""
        if not content:
            return content
        if settings is None:
            settings = await self.settings_for_uri(uri, ctx)
        if settings is None:
            return content
        result = await self.sanitize(content, ctx=ctx, uri=uri, settings=settings)
        return result.content

    async def _matcher(
        self,
        ctx: RequestContext,
        *,
        scope: tuple[str, str] | None = None,
        settings: ResolvedPrivacyConfig,
    ) -> _PreparedMatcher:
        settings_hash = hashlib.sha256(
            json.dumps(asdict(settings), sort_keys=True).encode("utf-8")
        ).hexdigest()
        cache_key, current = await self._matcher_state(ctx, scope, settings_hash)
        now = self._monotonic_clock()
        cached = self._cache.get(cache_key)
        if cached is not None and now - cached[0] < self._cache_ttl_seconds:
            return cached[1]
        async with self._cache_lock:
            cache_key, current = await self._matcher_state(ctx, scope, settings_hash)
            now = self._monotonic_clock()
            cached = self._cache.get(cache_key)
            if cached is not None and now - cached[0] < self._cache_ttl_seconds:
                return cached[1]
            matcher = await self._build_matcher(
                ctx, scope=scope, current=current, settings=settings
            )
            owner_key = cache_key[:2]
            self._cache = {
                key: item
                for key, item in self._cache.items()
                if now - item[0] < self._cache_ttl_seconds
                and (key[:2] != owner_key or (key[2:4] == cache_key[2:4] and key[4] != scope))
            }
            self._cache[cache_key] = (now, matcher)
            return matcher

    async def _matcher_state(
        self, ctx: RequestContext, scope: tuple[str, str] | None, settings_hash: str
    ) -> tuple[
        tuple[str, str, int, str, tuple[str, str] | None, int, str],
        UserPrivacyConfigVersion | None,
    ]:
        current = await self._privacy.get_current(ctx, *scope) if scope else None
        return (
            (
                ctx.account_id,
                ctx.user.user_id,
                self._privacy.catalog_revision(ctx),
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
    ) -> _PreparedMatcher:
        from openviking.privacy.rules import prepare_privacy_extractor

        discover = prepare_privacy_extractor(settings=settings)
        never_mask = set(settings.never_mask)
        references: dict[str, list[PrivacyFieldReference]] = {}
        configs = [
            ("credential", target, await self._privacy.get_current(ctx, "credential", target))
            for target in await self._privacy.list_targets(ctx, "credential")
        ]
        if scope and current is not None:
            configs.append((*scope, current))
        for category, target_key, config in configs:
            if config is None:
                continue
            for field_name, value in config.values.items():
                if not isinstance(value, str) or not value or value in never_mask:
                    continue
                if category != "credential":
                    try:
                        matches = discover(value)
                    except InvalidArgumentError:
                        continue
                    if len(matches) != 1 or matches[0].start != 0 or matches[0].end != len(value):
                        continue
                references.setdefault(value, []).append(
                    PrivacyFieldReference(ctx.user.user_id, category, target_key, field_name)
                )

        targets: dict[str, list[str]] = {}
        ambiguous = {}
        for secret, candidates in references.items():
            credentials = [item for item in candidates if item.category == "credential"]
            ordered = sorted(
                set(credentials or candidates),
                key=lambda item: (item.category, item.target_key, item.field),
            )
            selected = tuple(item.render() for item in ordered)
            targets[secret] = [selected[0]]
            if len(selected) > 1:
                ambiguous[selected[0]] = selected
        return _PreparedMatcher(self._matcher_class(targets, _AMBIGUOUS_PLACEHOLDER), ambiguous)

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
        logger.warning(
            "Privacy ambiguous_matches=%d strict=%s uri=%s canonical_references=%s",
            occurrences,
            strict,
            uri,
            tuple(items[0] for items in ambiguous),
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
