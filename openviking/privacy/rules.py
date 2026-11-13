# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Python interface to native credential format detection."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any

from openviking.privacy.sanitizer import DEFAULT_MATCHER_CACHE_TTL_SECONDS
from openviking.pyagfs import get_privacy_extractor_class
from openviking_cli.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from openviking.server.user_config import ResolvedPrivacyConfig


@dataclass(frozen=True)
class PrivacyMatch:
    start: int
    end: int
    detector_id: str


DEFAULT_EXTRACTOR_CACHE_MAX_BYTES = 64 * 1024 * 1024
_extractor_cache_max_bytes = int(
    os.environ.get(
        "OPENVIKING_PRIVACY_EXTRACTOR_CACHE_MAX_BYTES", DEFAULT_EXTRACTOR_CACHE_MAX_BYTES
    )
)
_extractor_limits = {
    name: int(os.environ[env])
    for name, env in (
        ("max_rules", "OPENVIKING_PRIVACY_MAX_CUSTOM_RULES"),
        ("max_source_bytes", "OPENVIKING_PRIVACY_MAX_RULES_SOURCE_BYTES"),
        ("max_compiled_bytes", "OPENVIKING_PRIVACY_MAX_RULES_COMPILED_BYTES"),
    )
    if env in os.environ
}
if _extractor_cache_max_bytes < 0 or any(value <= 0 for value in _extractor_limits.values()):
    raise ValueError("Privacy rule limits must be positive; extractor cache bytes may be zero")

_extractor_cache: dict[str, tuple[float, Any, int]] = {}
_extractor_cache_bytes = 0
_extractor_cache_lock = Lock()


def _extractor(custom_rules_json: str = "[]", *, cache: bool = True):
    global _extractor_cache_bytes

    now = time.monotonic()
    cached = _extractor_cache.get(custom_rules_json)
    if cached is not None and now - cached[0] < DEFAULT_MATCHER_CACHE_TTL_SECONDS:
        return cached[1]
    with _extractor_cache_lock:
        now = time.monotonic()
        cached = _extractor_cache.get(custom_rules_json)
        if cached is not None and now - cached[0] < DEFAULT_MATCHER_CACHE_TTL_SECONDS:
            return cached[1]
        extractor = get_privacy_extractor_class()(custom_rules_json, **_extractor_limits)
        if not cache:
            return extractor
        size = extractor.reserved_bytes + sys.getsizeof(custom_rules_json)
        if size > _extractor_cache_max_bytes:
            return extractor
        # Creation order is also expiry order because hits do not renew the TTL.
        while _extractor_cache:
            key = next(iter(_extractor_cache))
            created, _, previous_size = _extractor_cache[key]
            if (
                now - created < DEFAULT_MATCHER_CACHE_TTL_SECONDS
                and _extractor_cache_bytes + size <= _extractor_cache_max_bytes
            ):
                break
            del _extractor_cache[key]
            _extractor_cache_bytes -= previous_size
        _extractor_cache[custom_rules_json] = (time.monotonic(), extractor, size)
        _extractor_cache_bytes += size
        return extractor


def validate_custom_rules(rules: list[dict]) -> None:
    if rules:
        _extractor(json.dumps(rules, sort_keys=True), cache=False)


def prepare_privacy_extractor(
    *, settings: ResolvedPrivacyConfig | None = None
) -> Callable[[str], list[PrivacyMatch]]:
    """Prepare format detection for one batch of texts."""
    try:
        rules = (
            json.dumps(settings.custom_rules, sort_keys=True)
            if settings and settings.custom_rules
            else "[]"
        )
        extractor = _extractor(rules).clone_for_search()
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from None
    never_mask = settings.never_mask if settings else []

    def find(content: str) -> list[PrivacyMatch]:
        try:
            return [PrivacyMatch(*match) for match in extractor.extract(content, never_mask)]
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None

    return find


def find_privacy_matches(
    content: str, *, settings: ResolvedPrivacyConfig | None = None
) -> list[PrivacyMatch]:
    """Discover sensitive spans using Unicode character offsets."""
    return prepare_privacy_extractor(settings=settings)(content)


def is_sensitive_value(value: str, *, settings: ResolvedPrivacyConfig | None = None) -> bool:
    """Whether the complete value matches a supported credential format."""
    matches = find_privacy_matches(value, settings=settings)
    return len(matches) == 1 and matches[0].start == 0 and matches[0].end == len(value)
