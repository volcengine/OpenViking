# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lossless pages of an already-authorized visible text representation."""

import json
from collections.abc import Callable

from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.exceptions import InvalidArgumentError

MAX_PAGE_BYTES = 1024 * 1024


def fit_read_page(
    text: str,
    uri: str,
    version: str,
    start: int,
    max_tokens: int,
    cursor_for_offset: Callable[[int], str],
) -> str:
    """Budget the actual JSON text, including escapes and continuation metadata.

    The caller resolves and authorizes the source before computing its version.
    A page never normalizes line endings or splits a Unicode code point.
    """
    if type(max_tokens) is not int or max_tokens <= 0:
        raise InvalidArgumentError("max_tokens must be a positive integer")
    if type(start) is not int or not 0 <= start <= len(text):
        raise InvalidArgumentError("Invalid read cursor position; restart the read")

    def render(end: int) -> str:
        return json.dumps(
            {
                "uri": uri,
                "content": text[start:end],
                "version": version,
                "has_more": end < len(text),
                "next_cursor": cursor_for_offset(end) if end < len(text) else None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def fits(page: str) -> bool:
        return (
            len(page.encode("utf-8")) <= MAX_PAGE_BYTES and estimate_text_tokens(page) <= max_tokens
        )

    # A final page has less metadata. Test it separately from the monotonic
    # non-final-page search, and do not serialize an unbounded remaining source.
    if len(text) - start <= MAX_PAGE_BYTES:
        final = render(len(text))
        if fits(final):
            return final
    low, high = start, min(len(text) - 1, start + MAX_PAGE_BYTES)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(render(middle)):
            low = middle
        else:
            high = middle - 1
    if low == start:
        raise InvalidArgumentError("max_tokens cannot fit read-page metadata and source text")
    result = render(low)
    if not fits(result):
        raise InvalidArgumentError("max_tokens cannot fit the read page")
    return result


def build_read_page(
    text: str,
    uri: str,
    scope: tuple[str, ...],
    max_tokens: int,
    cursor: str | None = None,
    *,
    signing_key: bytes,
) -> str:
    """Bind continuation to one visible representation, caller, URI and budget.

    Cursors do not authorize reads. Every request must obtain text through the
    normal authorized visible-read path before calling this function.
    """
    import base64
    import binascii
    import hashlib
    import hmac
    import struct

    from openviking_cli.exceptions import FailedPreconditionError

    if type(max_tokens) is not int or max_tokens <= 0:
        raise InvalidArgumentError("max_tokens must be a positive integer")
    if len(signing_key) < 32:
        raise FailedPreconditionError("Bounded read requires a configured shared read cursor key")
    context = json.dumps([uri, scope, max_tokens], separators=(",", ":")).encode()
    version = hashlib.sha256(text.encode("utf-8")).digest()
    start = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) != 98:
                raise ValueError("invalid cursor size")
            raw = base64.b64decode(cursor + "==", altchars=b"-_", validate=True)
            payload, signature = raw[:41], raw[41:]
            if len(raw) != 73 or not hmac.compare_digest(
                signature, hmac.digest(signing_key, context + payload, "sha256")
            ):
                raise ValueError("invalid cursor signature")
            protocol, previous_version, start = struct.unpack("!B32sQ", payload)
            if protocol != 1 or start == 0:
                raise ValueError("invalid cursor position")
        except (ValueError, TypeError, binascii.Error, struct.error) as exc:
            raise InvalidArgumentError("Invalid read cursor; restart the read") from exc
        if previous_version != version:
            raise FailedPreconditionError("Source text changed; restart the read without a cursor")
        if start >= len(text):
            raise InvalidArgumentError("Invalid read cursor position; restart the read")

    def encode(end: int) -> str:
        payload = struct.pack("!B32sQ", 1, version, end)
        return (
            base64.urlsafe_b64encode(
                payload + hmac.digest(signing_key, context + payload, "sha256")
            )
            .decode()
            .rstrip("=")
        )

    return fit_read_page(text, uri, version.hex(), start, max_tokens, encode)
