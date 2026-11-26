# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replace sealed values in caller-supplied texts, optionally enrolling new ones."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import string
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable

from openviking.privacy.rules import PrivacyMatch, prepare_privacy_extractor
from openviking.privacy.sanitizer import (
    DEFAULT_SANITIZER_MAX_CONTENT_BYTES,
    MIN_PROTECTED_VALUE_CHARS,
    PrivacyReplacement,
    PrivacySanitizer,
)
from openviking.privacy.service import AUTO_CATEGORY, AUTO_ENROLL_RULE_LABEL
from openviking.privacy.validation import PRIVACY_MAX_STRING_BYTES
from openviking.server.identity import RequestContext
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking_cli.exceptions import FailedPreconditionError, ResourceExhaustedError

if TYPE_CHECKING:
    from openviking.privacy.service import UserPrivacyConfigService

SEAL_MAX_TEXTS = 1024
SEAL_MAX_TOTAL_BYTES = DEFAULT_SANITIZER_MAX_CONTENT_BYTES
SEAL_MAX_ENROLLMENTS = 1024
ENROLL_FIELD = "token"
_TARGET_SUFFIX_CHARS = string.ascii_lowercase + string.digits
_TARGET_SUFFIX_LENGTH = 6
_RULE_PREFIX_MAX_CHARS = 64
_INVALID_TARGET_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class SealText:
    text: str
    enroll: bool = False


async def seal_texts(
    privacy: UserPrivacyConfigService | None,
    sanitizer: PrivacySanitizer | None,
    ctx: RequestContext,
    texts: list[SealText],
) -> dict[str, Any]:
    """Replace registered values with references, independent of ``mode``.

    Registered values are those of the ``credential`` and ``auto`` categories.
    Texts marked ``enroll`` also have detector-recognized tokens stored as new
    ``auto`` targets first. Limits raise instead of returning input unchanged.
    """
    if privacy is None:
        # Callers fail closed on errors; never report unsealed text as sealed.
        raise FailedPreconditionError("Privacy configuration service is unavailable")
    _validate_request(texts)
    sanitizer = sanitizer or PrivacySanitizer(privacy)
    settings = replace(await privacy.get_settings(ctx), mode="registered")

    created: set[str] = set()
    if any(item.enroll and item.text for item in texts):
        find = (await sanitizer.prepare_seal_matcher(ctx, settings=settings)).find
        candidates = await asyncio.to_thread(_enroll_candidates, texts, find, settings)
        if candidates:
            created = await _enroll(privacy, sanitizer, ctx, settings, candidates)

    find, temporary, revision = await sanitizer.prepare_seal_matcher(ctx, settings=settings)
    results = await asyncio.to_thread(
        _replace_all, texts, find, created, temporary, sanitizer.max_replacements
    )
    return {"revision": _seal_revision(revision, settings), "texts": results}


def _validate_request(texts: list[SealText]) -> None:
    size = sum(len(item.text.encode("utf-8")) for item in texts)
    if size > SEAL_MAX_TOTAL_BYTES:
        raise ResourceExhaustedError(
            "Texts exceed the Privacy seal size limit",
            details={"size_bytes": size, "max_bytes": SEAL_MAX_TOTAL_BYTES},
        )


def _enroll_candidates(
    texts: list[SealText],
    find: Callable[[str], list[PrivacyReplacement]],
    settings: ResolvedPrivacyConfig,
) -> dict[str, str]:
    """Return detected, not yet replaced tokens mapped to their detector rule."""
    detect = prepare_privacy_extractor(settings=settings)
    candidates: dict[str, str] = {}
    for item in texts:
        if not item.enroll or not item.text:
            continue
        registered = [(match.start, match.end) for match in find(item.text)]
        for match in detect(item.text):
            if _overlaps(match, registered):
                continue
            value = item.text[match.start : match.end]
            # The matcher never replaces shorter values and the store takes no longer
            # ones, so both stay unenrolled.
            if (
                len(value) >= MIN_PROTECTED_VALUE_CHARS
                and len(value.encode("utf-8")) <= PRIVACY_MAX_STRING_BYTES
            ):
                candidates.setdefault(value, match.detector_id)
    if len(candidates) > SEAL_MAX_ENROLLMENTS:
        raise ResourceExhaustedError(
            "Texts contain more new credentials than one Privacy seal call may enroll",
            details={"max_enrollments": SEAL_MAX_ENROLLMENTS},
        )
    return candidates


def _overlaps(match: PrivacyMatch, spans: list[tuple[int, int]]) -> bool:
    return any(match.start < end and start < match.end for start, end in spans)


async def _enroll(
    privacy: UserPrivacyConfigService,
    sanitizer: PrivacySanitizer,
    ctx: RequestContext,
    settings: ResolvedPrivacyConfig,
    candidates: dict[str, str],
) -> set[str]:
    """Store each unregistered candidate once per user, across workers."""
    created: set[str] = set()
    async with privacy.enroll_lock(ctx) as lease:
        # The matcher is cached per persisted revision, and every enrollment
        # bumps it inside this lock, so it sees other workers' enrollments.
        find = (await sanitizer.prepare_seal_matcher(ctx, settings=settings)).find
        for value, rule in candidates.items():
            if find(value):
                continue
            await _create_enrolled_target(privacy, ctx, value, rule, lease)
            created.add(value)
    return created


async def _create_enrolled_target(
    privacy: UserPrivacyConfigService,
    ctx: RequestContext,
    value: str,
    rule: str,
    lease: dict[str, Any] | None,
) -> None:
    prefix = _INVALID_TARGET_CHARS.sub("-", rule).strip("-.")[:_RULE_PREFIX_MAX_CHARS] or "secret"
    suffix = "".join(secrets.choice(_TARGET_SUFFIX_CHARS) for _ in range(_TARGET_SUFFIX_LENGTH))
    target_key = f"{prefix}-{suffix}"
    # A suffix collision raises AlreadyExistsError (the field already exists).
    await privacy.insert_values(
        ctx,
        AUTO_CATEGORY,
        target_key,
        {ENROLL_FIELD: value},
        updated_by=ctx.user.user_id,
        change_reason="Automatic enrollment",
        labels={AUTO_ENROLL_RULE_LABEL: rule},
        **({"owner_lease_ref": lease} if lease is not None else {}),
    )


def _replace_all(
    texts: list[SealText],
    find: Callable[[str], list[PrivacyReplacement]],
    created: set[str],
    temporary: frozenset[str],
    max_replacements: int,
) -> list[dict[str, Any] | None]:
    results: list[dict[str, Any] | None] = []
    total = 0
    for item in texts:
        matches = sorted(find(item.text), key=lambda match: match.start) if item.text else []
        total += len(matches)
        if total > max_replacements:
            raise ResourceExhaustedError(
                "Texts exceed the Privacy seal replacement limit",
                details={"max_replacements": max_replacements},
            )
        if not matches:
            results.append(None)
            continue
        chunks: list[str] = []
        cursor = 0
        replaced = []
        for match in matches:
            # Native offsets are Unicode code points, matching Python slicing.
            chunks.extend((item.text[cursor : match.start], match.reference))
            cursor = match.end
            replaced.append(
                {
                    "start": match.start,
                    "end": match.end,
                    "reference": match.reference,
                    # Statistic only: true for values enrolled by this call.
                    "created": item.text[match.start : match.end] in created,
                    # True for references in ``auto``; derived from the vault at
                    # this revision, so callers may cache it.
                    "temporary": match.reference in temporary,
                }
            )
        chunks.append(item.text[cursor:])
        results.append({"text": "".join(chunks), "replaced": replaced})
    return results


def _seal_revision(credential_revision: str, settings: ResolvedPrivacyConfig) -> str:
    """Combine the Credential revision with the settings that change seal results."""
    fingerprint = hashlib.sha256(
        json.dumps(
            {"never_mask": sorted(set(settings.never_mask)), "custom_rules": settings.custom_rules},
            sort_keys=True,
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()[:16]
    return f"{credential_revision}-{fingerprint}"
