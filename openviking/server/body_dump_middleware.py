# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP request/response body dump middleware for trace debugging.

Attaches the request and response bodies as attributes on the active OpenTelemetry
root span so they can be inspected in trace UIs (Jaeger, Tempo, etc.). The middleware
must run inside the trace span context — register it before the http_observability
middleware in ``create_app``.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request
from starlette.responses import Response

try:
    from opentelemetry import trace as otel_trace
except ImportError:  # pragma: no cover - OTel optional
    otel_trace = None

# Skip body capture for content types that are binary, streamed, or otherwise
# pointless to materialize as a span attribute.
_SKIP_CONTENT_TYPE_PREFIXES = (
    "multipart/form-data",
    "application/octet-stream",
    "text/event-stream",
    "audio/",
    "video/",
    "image/",
)


def _should_skip(content_type: str | None) -> bool:
    if not content_type:
        return False
    ct = content_type.lower()
    return any(ct.startswith(p) for p in _SKIP_CONTENT_TYPE_PREFIXES)


def _truncate(data: bytes, max_bytes: int) -> str:
    total = len(data)
    head = data[:max_bytes]
    text = head.decode("utf-8", errors="replace")
    if total > max_bytes:
        return f"{text}…[+{total - max_bytes}B truncated, total {total}B]"
    return text


def _set_span_attr(key: str, value: object) -> None:
    if otel_trace is None:
        return
    try:
        span = otel_trace.get_current_span()
        if span is None or not span.is_recording():
            return
        span.set_attribute(key, value)
    except Exception:
        # Body dump must never break the request path.
        pass


def create_dump_http_body_middleware(
    max_bytes: int = 4096,
) -> Callable[[Request, Callable], Awaitable[Response]]:
    """Build a body-dump middleware bound to ``max_bytes``.

    The middleware skips streaming/binary content types and truncates payloads to
    keep span attributes bounded.
    """

    async def middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        req_ct = request.headers.get("content-type", "")
        if not _should_skip(req_ct):
            try:
                body = await request.body()
                if body:
                    _set_span_attr("http.request.body", _truncate(body, max_bytes))
                    _set_span_attr("http.request.body.size", len(body))
                if req_ct:
                    _set_span_attr("http.request.content_type", req_ct)
            except Exception:
                pass

        response = await call_next(request)

        resp_ct = response.headers.get("content-type", "")
        if _should_skip(resp_ct):
            return response

        # Once we start iterating ``response.body_iterator`` we own the bytes;
        # capture failures must not silently truncate the response sent to the
        # client, so we always rebuild a Response from whatever we've collected.
        chunks: list[bytes] = []
        try:
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            body_bytes = b"".join(chunks)
            if body_bytes:
                _set_span_attr("http.response.body", _truncate(body_bytes, max_bytes))
                _set_span_attr("http.response.body.size", len(body_bytes))
            if resp_ct:
                _set_span_attr("http.response.content_type", resp_ct)
        except Exception:
            body_bytes = b"".join(chunks)
            _set_span_attr("http.response.body.capture_error", True)

        try:
            new_headers = {
                k: v for k, v in response.headers.items() if k.lower() != "content-length"
            }
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=new_headers,
                media_type=response.media_type,
            )
        except Exception:
            # Fall back to the original response object as a last resort. Its
            # body_iterator is exhausted at this point, so this only fires if
            # the rebuild path itself is broken.
            return response

    return middleware
