# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Python interface to the native fixed-rule PrivacyExtractor."""

import base64
import binascii
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from openviking.pyagfs import get_privacy_extractor_class
from openviking_cli.exceptions import InvalidArgumentError

MAX_TEXT_CHARS = 4 * 1024 * 1024
MAX_MATCHES = 100_000
_AUTH_VALUE = re.compile(r"(?i)(?:Bearer|Basic)[ \t]+([A-Za-z0-9_./+=~*-]{8,})")
_TOKEN_VALUE = re.compile(r"[A-Za-z0-9_./+~!@#$%^&*()?\\|'\"-]{8,}={0,2}")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY)-----\r?\n"
    r"[A-Za-z0-9+/=\r\n]{32,}-----END (?P=kind)-----\s*"
)


def is_credential_value(value: str) -> bool:
    """Conservatively allow token-shaped strings, rather than words or prose.

    This guards automatic whole-field capture and Session substring matching.
    Explicitly registered credentials may still contain arbitrary strings.
    """
    if _AUTH_VALUE.fullmatch(value):
        return True
    if _PRIVATE_KEY.fullmatch(value):
        return True
    if "://" in value:
        try:
            url = urlsplit(value)
            return not any(char.isspace() for char in value) and bool(
                url.username and url.password and url.hostname
            )
        except ValueError:
            return False
    if not _TOKEN_VALUE.fullmatch(value) or value.startswith(("'", '"')):
        return False
    if re.fullmatch(r"[a-fA-F0-9]{32,}", value):
        return True
    if value.replace("_", "").isalpha():
        if any(start == 0 and end == len(value) for start, end, _ in _extractor().extract(value)):
            return True
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
            user, separator, password = decoded.partition(":")
            if user and separator and password and not any(char.isspace() for char in decoded):
                return True
        except (binascii.Error, ValueError):
            pass
        return len(value) >= 24 and len(set(value)) >= 12
    return any(char.isalpha() for char in value)


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
