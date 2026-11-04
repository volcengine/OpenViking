# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Python interface to native credential format detection."""

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
    detector_id: str


@lru_cache(maxsize=1)
def _extractor():
    return get_privacy_extractor_class()()


def find_privacy_matches(content: str) -> list[PrivacyMatch]:
    """Discover sensitive spans using Unicode character offsets."""
    if len(content) > MAX_TEXT_CHARS:
        raise InvalidArgumentError("Privacy extraction text limit exceeded")
    try:
        return [PrivacyMatch(*match) for match in _extractor().extract(content)]
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from None


def is_sensitive_value(value: str) -> bool:
    """Whether the complete value matches a supported credential format."""
    matches = find_privacy_matches(value)
    return len(matches) == 1 and matches[0].start == 0 and matches[0].end == len(value)
