# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Python interface to the native fixed-rule PrivacyExtractor."""

from dataclasses import dataclass
from functools import lru_cache

from openviking.pyagfs import get_privacy_extractor_class
from openviking_cli.exceptions import InvalidArgumentError

MAX_TEXT_CHARS = 4 * 1024 * 1024
MAX_MATCHES = 100_000


@dataclass(frozen=True)
class PrivacyMatch:
    start: int
    end: int
    field_name: str


@lru_cache(maxsize=1)
def _extractor():
    return get_privacy_extractor_class()()


def is_sensitive_key(key: str) -> bool:
    return _extractor().is_sensitive_key(key)


def find_privacy_matches(content: str, *, field_name: str | None = None) -> list[PrivacyMatch]:
    """Discover sensitive spans using Unicode character offsets."""
    if len(content) > MAX_TEXT_CHARS:
        raise InvalidArgumentError("Privacy extraction text limit exceeded")
    try:
        return [PrivacyMatch(*match) for match in _extractor().extract(content, field_name)]
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from None
