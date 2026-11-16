# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP routing, forwarding and response observation for one gateway request."""

import asyncio
import json
import logging
import time
from dataclasses import replace
from typing import NamedTuple
from urllib.parse import quote, unquote_plus

import aiohttp
import async_timeout
import orjson
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError

from openviking.core.peer_id import normalize_peer_id

from .capture import CapturePipeline
from .client import PEER_HEADER
from .models import KEY_PLACEHOLDER, Policy, validation_detail
from .observe import GatewayError, RequestRecord, degrade, elapsed_ms, error_code
from .protocols import HEARTBEAT_SECONDS, SSEDecoder, heartbeat, parse_body
from .storage import digest
from .tool_executor import ToolExecutor
from .tool_loop import HiddenToolLoop, ToolLoopError
from .tool_protocols import ResponseCapture, tool_protocol
from .tool_protocols.common import SummaryError
from .vendors import ARK_VENDORS, apply_vendor

logger = logging.getLogger(__name__)
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
PATH_PROTOCOL = {
    "/v1/messages": "anthropic",
    "/v1/messages/count_tokens": "anthropic",
    "/v1/chat/completions": "chat",
    "/v1/responses": "responses",
}
PRIMARY_PATH = {
    "anthropic": "/v1/messages",
    "chat": "/v1/chat/completions",
    "responses": "/v1/responses",
}


async def open_upstream(http, method, url, wait, total=None, **kwargs):
    """Start a model call that waits at most ``wait`` seconds at a time on the upstream.

    The reply must start within ``wait`` (connecting, sending the body and receiving the
    headers), and its body may then pause for at most ``wait`` between reads, so a reply that
    keeps arriving is never cut. ``total``, when given, also bounds the whole call.
    """
    # aiohttp's sock_read covers the headers and every body read but starts only after the
    # request body is written; the outer bound covers connecting and sending the body.
    timeout = aiohttp.ClientTimeout(total=total, sock_read=wait)
    async with async_timeout.timeout(wait):
        return await http.request(method, url, timeout=timeout, **kwargs)


class _ClosingStreamingResponse(StreamingResponse):
    """A stream that runs ``cleanup`` however it ends, the client leaving included."""

    def __init__(self, content, *, cleanup, close_upstream=None, **kwargs):
        super().__init__(content, **kwargs)
        self._close_upstream = close_upstream
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                if self._close_upstream:
                    self._close_upstream()
            finally:
                try:
                    if close := getattr(self.body_iterator, "aclose", None):
                        await close()
                finally:
                    await self._cleanup()


class _FinishingResponse(Response):
    """A reply that runs ``cleanup`` once it was sent, or failed to be."""

    def __init__(self, content, *, cleanup, **kwargs):
        super().__init__(content, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._cleanup()


def filtered_headers(headers, request=False):
    blocked = HOP_HEADERS | {
        part.strip().lower() for part in headers.get("connection", "").split(",")
    }
    if request:
        blocked |= {
            "host",
            "content-length",
            "authorization",
            "x-api-key",
            "cookie",
            "accept-encoding",
        }
    # Neither a client nor a provider may set the gateway's own headers on the other side.
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in blocked and not k.lower().startswith("x-openviking-")
    }


def query_names(pairs):
    return {unquote_plus(pair.partition("=")[0]) for pair in pairs}


def upstream_url(upstream, path, query=""):
    """The upstream address for a client path; ``{api_key}`` stays unfilled."""
    base, _, stored = upstream["base_url"].partition("?")
    base = base.rstrip("/")
    if not upstream.get("append_endpoint_path", True):
        primary = PRIMARY_PATH[upstream["protocol"]]
        if path != primary and not path.startswith(primary + "/"):
            raise GatewayError(
                "upstream_endpoint_mismatch",
                f"This upstream is an exact endpoint for {primary} only",
            )
        path = path[len(primary) :]
    elif upstream["protocol"] != "anthropic":
        path = path.removeprefix("/v1")
    pairs = [pair for pair in stored.split("&") if pair]
    names = query_names(pairs)
    pairs += [pair for pair in query.split("&") if pair and not query_names([pair]) & names]
    return base + path + ("?" + "&".join(pairs) if pairs else "")


def upstream_key(upstream, incoming):
    key = (
        upstream.get("api_key", "")
        if upstream.get("auth_mode") != "passthrough"
        else incoming.get("x-openviking-upstream-key", "")
    )
    if key.startswith("sk-ant-oat"):
        raise GatewayError(
            "oauth_credential_unsupported",
            "Claude subscription OAuth credentials are not supported; use an API key",
        )
    if not key:
        raise GatewayError("upstream_key_missing", "Upstream API key is missing")
    return key


def context_error(detail, status=400, reason="context_override_invalid"):
    """An ``X-OpenViking-Context`` error, in the management API's ``{reason, detail}`` shape."""
    return GatewayError(reason, detail, status=status)


def context_override(policy, header):
    """The stored policy with the fields of an ``X-OpenViking-Context`` header applied."""
    if not policy.get("client_overrides", True):
        raise context_error(
            "This key's context profile does not accept X-OpenViking-Context.",
            403,
            "context_override_not_allowed",
        )
    try:
        override = json.loads(header)
    except (ValueError, RecursionError):
        raise context_error("X-OpenViking-Context is not valid JSON")
    if not isinstance(override, dict):
        raise context_error("X-OpenViking-Context must be a JSON object")
    # The profile's name and its switch are not per-conversation settings.
    if blocked := override.keys() & {"name", "client_overrides"}:
        raise context_error("X-OpenViking-Context cannot set " + ", ".join(sorted(blocked)))
    try:
        merged = Policy.model_validate({**policy, **override})
        saved = Policy.model_validate(policy)
    except ValidationError as error:
        raise context_error(validation_detail(error))
    # Tool access only narrows: the header cannot offer a tool the profile withholds.
    merged.gateway_tools = merged.gateway_tools and saved.gateway_tools
    merged.disabled_tools = list(dict.fromkeys([*saved.disabled_tools, *merged.disabled_tools]))
    return {**policy, **merged.model_dump()}, sorted(override)


def request_peer(headers, credential):
    """The peer of an ``X-OpenViking-Actor-Peer`` header, else the key's default peer.

    A blank header falls back to the default, so a client cannot drop a key's peer.
    """
    header = headers.get(PEER_HEADER)
    try:
        peer = normalize_peer_id(header)
    except ValueError:
        raise context_error(
            PEER_HEADER + " must be one path segment of letters, digits, '_', '.', '-' "
            "and at most one '@'.",
            reason="peer_invalid",
        ) from None
    return peer or credential.get("default_peer_id")


def fill_key(url, key):
    return url.replace(KEY_PLACEHOLDER, quote(key, safe=""))


def upstream_headers(upstream, incoming):
    headers = filtered_headers(incoming, request=True)
    headers.update(upstream.get("headers", {}))
    key = upstream_key(upstream, incoming)
    name = upstream.get("key_header", "authorization")
    # Drop configured copies of the auth headers set below.
    if KEY_PLACEHOLDER in upstream.get("base_url", ""):
        own, auth = {"authorization", "x-api-key", "api-key"}, {}
    elif name == "authorization":
        own, auth = {"authorization"}, {"Authorization": "Bearer " + key}
        # Anthropic-compatible services accept either header; some require Bearer.
        if upstream["protocol"] == "anthropic":
            own, auth = own | {"x-api-key"}, {**auth, "x-api-key": key}
    else:
        own, auth = {"authorization", name}, {name: key}
    headers = {k: v for k, v in headers.items() if k.lower() not in own}
    headers.update(auth)
    headers["accept-encoding"] = "identity"
    return headers


def check_coding_plan(upstream):
    if upstream.get("coding_plan") and not upstream.get("allow_coding_plan"):
        raise GatewayError(
            "coding_plan_disabled", "Coding Plan upstreams are disabled; configure a model API key"
        )


def matches(upstream, protocol, model):
    return (
        upstream.get("enabled", True)
        and (
            upstream["protocol"] == protocol
            if protocol
            else upstream.get("append_endpoint_path", True)
        )
        and (
            not model
            or not upstream.get("models")
            or model in upstream["models"]
            or model in upstream.get("aliases", {})
        )
    )


class Binding(NamedTuple):
    policy: dict | None
    # Disabled ones included; callers skip them.
    upstreams: list[dict]
    # Empty allows every model.
    models: list[str]


async def default_policy(management, config, account):
    """Use the account's default profile, or the deployment's when none is stored."""
    policy = await management.get(account, "policies", "default")
    return policy or config.default_profile.model_dump()


async def resolve_binding(management, config, credential):
    """The context profile, upstreams and models a credential's requests use."""
    account = credential["account"]
    upstreams = await management.list(account, "upstreams")
    if credential.get("source") == "openviking":
        # An OpenViking key stores no binding: it uses the account's defaults.
        return Binding(
            await default_policy(management, config, account),
            upstreams or [{**u.model_dump(), "id": i} for i, u in config.default_upstreams.items()],
            [],
        )
    return Binding(
        await management.get(account, "policies", credential["policy_id"]),
        [u for u in upstreams if u["id"] in credential["upstream_ids"]],
        credential["models"],
    )


class ProxyRequest:
    def __init__(self, app, request, path, credential, record=None):
        self.app, self.request, self.credential = app, request, credential
        self.config, self.store, self.management = (
            app.state.config,
            app.state.store,
            app.state.management,
        )
        self.path = "/" + path
        for prefix in ("/api/v3/", "/api/compatible/v1/"):
            if self.path.startswith(prefix):
                self.path = "/v1/" + self.path.removeprefix(prefix)
                break
        self.protocol = PATH_PROTOCOL.get(self.path)
        self.prepared = None
        self.peer = None
        self.record = record or RequestRecord(app, request)
        # The record under construction: what the request writes however it ends.
        self.metrics = self.record.metrics
        self.metrics.setdefault("kind", "passthrough")

    async def run(self):
        if self.request.headers.get("upgrade", "").lower() == "websocket":
            self.record.fail(426, "websocket_unsupported")
            return Response(status_code=426, headers={"Upgrade": "HTTP/1.1"})
        raw = bytearray()
        with self.record.stage("read"):
            async for chunk in self.request.stream():
                raw.extend(chunk)
                if len(raw) > self.config.max_body_bytes:
                    raise GatewayError("body_too_large", "Request exceeds configured body limit")
            self.raw = bytes(raw)
            self.body = await asyncio.to_thread(parse_body, self.raw) if raw else None
        if isinstance(self.body, dict):
            self.metrics["stream"] = bool(self.body.get("stream"))
        original = self.body
        with self.record.stage("route"):
            await self.route()
        with self.record.stage("prepare"):
            await self.prepare()
        if self.body != original:
            self.raw = await asyncio.to_thread(orjson.dumps, self.body)
        self.target = self.url(self.upstream)
        self.headers = upstream_headers(self.upstream, self.request.headers)
        if self.raw:
            self.headers.setdefault("content-type", "application/json")
        self.started = time.monotonic()
        self.metrics["added_ms"] = elapsed_ms(self.record.started)
        self.record.attempted = True
        try:
            self.response = await open_upstream(
                self.app.state.http,
                self.request.method,
                self.target,
                self.config.upstream_timeout_seconds,
                data=self.raw or None,
                headers=self.headers,
                allow_redirects=False,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            self.metrics["duration_ms"] = elapsed_ms(self.started)
            raise GatewayError("upstream_unreachable", "Model upstream is unavailable") from None
        self.metrics.update(
            status=self.response.status,
            upstream_status=self.response.status,
            upstream_ttfb_ms=elapsed_ms(self.started),
            upstream_id=self.upstream["id"],
        )
        self.capture = ResponseCapture(self.protocol or self.upstream["protocol"])
        # Only the tool loop rewrites a reply, so the recall notice goes through it too.
        if (
            self.prepared
            and (self.prepared.tools_active or self.prepared.reply_lead)
            and self.response.status < 300
            and not self.path.endswith("count_tokens")
        ):
            return await self.tool_response()
        return await self.observe_response()

    async def route(self):
        routing = self.body
        if routing is None and self.raw:
            # Unsafe numbers/duplicate keys can still be forwarded byte for byte;
            # the model ACL must also apply on this uncommon fallback path.
            try:
                routing = json.loads(self.raw)
            except (ValueError, RecursionError):
                pass
        self.model = routing.get("model", "") if isinstance(routing, dict) else ""
        binding = await resolve_binding(self.management, self.config, self.credential)
        # Calls on a stored response are pinned to its upstream and carry no model.
        if binding.models and self.raw and not self.path.startswith("/v1/responses/"):
            if self.request.headers.get("content-encoding", "identity").lower() != "identity":
                raise GatewayError(
                    "compressed_body_unsupported", "Compressed request bodies are not supported"
                )
            if not self.model or not isinstance(self.model, str):
                raise GatewayError("model_missing", "Request model is missing")
        if binding.models and self.model and self.model not in binding.models:
            raise GatewayError("model_not_allowed", "Model is not allowed by this key")
        self.metrics["model"] = self.model
        self.policy = binding.policy
        # The header is checked on every request that carries it, enhanced or not.
        header = self.request.headers.get("x-openviking-context")
        if header is not None and self.policy:
            self.policy, self.metrics["context_override"] = context_override(self.policy, header)
        # Like the context header, a peer is checked on every request, enhanced or not.
        self.peer = request_peer(self.request.headers, self.credential)
        if self.peer:
            self.metrics["peer"] = self.peer
        self.upstreams = binding.upstreams
        # A host in front of the gateway may pick the upstream, but only an allowed one.
        url = self.request.headers.get("x-openviking-upstream-url")
        if url is not None:
            self.upstreams = [u for u in self.upstreams if u["base_url"] == url.rstrip("/")]
            if not self.upstreams:
                raise GatewayError("upstream_url_not_allowed", "Upstream URL is not allowed")
        # A stored response is pinned below; an exact responses endpoint may hold it.
        stored_response = self.path.startswith("/v1/responses/")
        candidates = sorted(
            [
                u
                for u in self.upstreams
                if matches(u, self.protocol, self.model)
                or (stored_response and matches(u, "responses", self.model))
            ],
            key=lambda u: (-u["priority"], u["id"]),
        )
        if not candidates:
            raise GatewayError(
                "no_matching_upstream", "No allowed upstream matches this protocol and model"
            )
        self.upstream = candidates[0]
        if self.path.startswith("/v1/responses/"):
            identifier = digest(self.credential["id"] + self.path.split("/")[3])
            mapping = await self.management.get(self.credential["account"], "responses", identifier)
            if not mapping:
                raise GatewayError("response_unknown", "Unknown response for this key")
            self.upstream = next(
                (u for u in self.upstreams if u["id"] == mapping["upstream_id"]), None
            )
            if not self.upstream or not self.upstream.get("enabled", True):
                raise GatewayError(
                    "response_upstream_disallowed", "Response upstream is no longer allowed"
                )
        # Pinned upstream selection in the kernel is restricted to allowed models.
        self.candidates = candidates
        # A missing client key fails before the request freezes a session or calls OpenViking.
        # A conversation may stay pinned to a candidate with its own key; url() checks that one.
        if all(u.get("auth_mode") == "passthrough" for u in candidates):
            upstream_key(self.upstream, self.request.headers)

    async def prepare(self):
        if (
            self.protocol
            and self.body
            and tool_protocol(self.protocol).enhanced_supported(self.body)
            and not self.request.headers.get("content-encoding")
        ):
            if not self.policy:
                raise GatewayError("policy_unavailable", "Context policy is unavailable")
            self.body, self.prepared, metrics = await self.app.state.kernel.enhance(
                self.body,
                self.protocol,
                dict(self.request.headers),
                self.credential,
                self.upstream,
                self.policy,
                self.path.endswith("count_tokens"),
                self.candidates,
                self.summarize,
                peer=self.peer,
            )
            self.record.merge(metrics)
            if self.prepared:
                self.upstream = self.prepared.upstream
        elif self.raw and self.body is None:
            degrade(self.metrics, "unsafe_json")
        check_coding_plan(self.upstream)
        if self.body:
            self.body = await self.outgoing(self.body, self.upstream, self.prepared)
            if self.prepared:
                self.prepared.body = self.body
                self.record.merge(self.prepared.metrics)

    def url(self, upstream):
        key = upstream_key(upstream, self.request.headers)
        filled = {**upstream, "base_url": fill_key(upstream["base_url"], key)}
        return upstream_url(filled, self.path, self.request.url.query)

    async def outgoing(self, body, upstream, prepared):
        """Vendor fields and the model alias, for the client request and its summary."""
        body = await apply_vendor(body, upstream, prepared, self.store)
        mapped = upstream.get("aliases", {}).get(self.model)
        return {**body, "model": mapped} if mapped else body

    async def summarize(self, prepared, body):
        """Send the kernel's summary request to the session's upstream and parse the reply."""
        upstream = prepared.upstream
        try:
            check_coding_plan(upstream)
            headers = upstream_headers(upstream, self.request.headers)
            target = self.url(upstream)
        except HTTPException as error:
            raise SummaryError(f"summary_http_{error.status_code}") from error
        headers.setdefault("content-type", "application/json")
        body = await self.outgoing(body, upstream, prepared)
        self.record.attempted = True
        try:
            async with self.app.state.http.post(
                target,
                data=orjson.dumps(body),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.config.upstream_timeout_seconds),
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise SummaryError(f"summary_http_{response.status}")
                return orjson.loads(await response.read())
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise SummaryError("summary_unavailable") from error

    async def relay(self):
        """Record a finished reply before the client receives its end."""
        if not (self.capture.finished and self.prepared and self.response.status < 300):
            return
        try:
            await self.app.state.kernel.relayed(self.prepared, self.capture)
        except Exception:
            logger.exception("OpenViking Gateway reply bookkeeping failed")

    async def finish(self):
        """Book an answered request and write its record; called once per answered request."""
        try:
            self.metrics.update(self.capture.usage or {})
            if self.upstream.get("vendor") in ARK_VENDORS:
                self.metrics["cache_min_tokens"] = self.upstream.get("cache_min_tokens", 1024)
                self.metrics["cache_eligible"] = (
                    self.metrics.get("input_tokens", 0) >= self.metrics["cache_min_tokens"]
                )
            self.metrics["duration_ms"] = elapsed_ms(self.started)
            if self.metrics["status"] >= 300 and "error_reason" not in self.metrics:
                self.record.fail(self.metrics["status"], "upstream_error")
            # Each step fails on its own: the record is written whatever happens here.
            try:
                if (
                    self.response.status < 300
                    and self.prepared
                    and await self.management.get(
                        self.credential["account"], "keys", self.credential["id"]
                    )
                ):
                    await self.app.state.kernel.completed(
                        self.prepared, self.credential, self.capture
                    )
            except Exception:
                self.bookkeeping_failed("completed")
            try:
                if (
                    self.capture.response_id
                    and self.path == "/v1/responses"
                    and self.body
                    and self.body.get("store") is not False
                ):
                    await self.management.save(
                        self.credential["account"],
                        "responses",
                        digest(self.credential["id"] + self.capture.response_id),
                        {"upstream_id": self.upstream["id"], "user_id": self.credential["user_id"]},
                        ttl=self.config.response_ttl_seconds,
                    )
            except Exception:
                self.bookkeeping_failed("responses")
            if self.prepared:
                # Delivery may have failed or recovered while this request was in flight.
                # A worker row inserted between this read and the log below can still be
                # older than this row; if the read fails, the prepare-time fields are kept.
                try:
                    capture = await self.store.capture.get(
                        self.prepared.scope, self.prepared.session
                    )
                    CapturePipeline.metrics(
                        replace(self.prepared, capture=capture, metrics=self.metrics),
                        Policy.model_validate(self.prepared.root["policy"]),
                    )
                except Exception:
                    self.bookkeeping_failed("capture_status")
        finally:
            await self.record.emit()

    def bookkeeping_failed(self, step):
        logger.exception("OpenViking Gateway response bookkeeping failed: %s", step)
        self.metrics.setdefault("bookkeeping_failed", []).append(step)

    async def tool_response(self):
        response, prepared, capture = self.response, self.prepared, self.capture
        executor = ToolExecutor(
            self.app.state.viking,
            self.store,
            prepared,
            self.credential,
            self.config.public_url,
            self.config.max_body_bytes,
            self.config.mcp_file_links,
        )
        loop = HiddenToolLoop(prepared, executor, self.store, capture)
        headers = {
            k.lower(): v
            for k, v in filtered_headers(response.headers).items()
            if k.lower() not in {"content-length", "etag", "content-encoding"}
        }

        async def send(payload, timeout):
            # A total tool deadline also bounds the whole continuation, body included.
            return await open_upstream(
                self.app.state.http,
                "POST",
                self.target,
                self.config.upstream_timeout_seconds,
                total=timeout,
                data=orjson.dumps(payload),
                headers=self.headers,
                allow_redirects=False,
            )

        completed = False

        async def stream():
            nonlocal completed
            finished = False
            chunks = heartbeat(loop.run(response, send), HEARTBEAT_SECONDS)
            try:
                async for chunk in chunks:
                    if chunk is None:
                        yield b": keep-alive\n\n"
                        continue
                    await self.relay()
                    self.metrics.setdefault("first_chunk_ms", elapsed_ms(self.record.started))
                    yield chunk
                finished = True
                completed = True
            except (ToolLoopError, aiohttp.ClientError) as error:
                degrade(self.metrics, "hidden_tool_loop_failed")
                self.record.stream_failed(
                    response.status, getattr(error, "code", "upstream_unreachable"), error
                )
                yield loop.error(error)
            except Exception:
                self.record.stream_failed(response.status, "internal_error")
                raise
            finally:
                await chunks.aclose()
                if not finished:
                    capture.complete = False
                    self.record.stream_failed(response.status, "client_disconnected")
                self.record.merge(prepared.metrics)

        async def cleanup():
            if not completed:
                capture.complete = False
            await self.finish()

        if self.body.get("stream"):
            headers["content-type"] = "text/event-stream"
            self.record.deferred = True
            return _ClosingStreamingResponse(
                stream(), headers=headers, close_upstream=response.close, cleanup=cleanup
            )
        try:
            async for _ in loop.run(response, send):
                pass
        except (ToolLoopError, aiohttp.ClientError) as error:
            capture.complete = False
            degrade(self.metrics, "hidden_tool_loop_failed")
            if isinstance(error, ToolLoopError) and error.content is not None:
                self.metrics["upstream_status"] = error.status
                if code := error_code(error.content):
                    self.metrics["upstream_error_code"] = code
                self.record.fail(error.status, "upstream_error")
                failed = Response(
                    error.content, status_code=error.status, headers=filtered_headers(error.headers)
                )
            else:
                if isinstance(error, aiohttp.ClientError):
                    error = GatewayError("upstream_unreachable", "Model upstream is unavailable")
                failed = self.record.error_response(error)
            await self.finish()
            return failed
        self.record.merge(prepared.metrics)
        await self.relay()
        self.record.deferred = True
        return _FinishingResponse(
            orjson.dumps(loop.final),
            headers=headers,
            media_type="application/json",
            cleanup=self.finish,
        )

    async def observe_response(self):
        response, capture = self.response, self.capture
        headers = filtered_headers(response.headers)
        if "text/event-stream" in response.headers.get("content-type", ""):
            headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}

            async def stream():
                decoder = SSEDecoder()
                observe = not response.headers.get("content-encoding")
                finished = False
                chunks = heartbeat(response.content.iter_any(), HEARTBEAT_SECONDS)
                try:
                    async for chunk in chunks:
                        if chunk is None:
                            if observe and not decoder.buffer:
                                yield b": keep-alive\n\n"
                            continue
                        if observe:
                            try:
                                # A chunk inside one event completes none: no reply to reread.
                                if frames := decoder.feed(chunk):
                                    capture.events(d for d in map(decoder.data, frames) if d)
                            except Exception:
                                observe = False
                                capture.complete = False
                                degrade(self.metrics, "capture_parse_failure")
                            await self.relay()
                        self.metrics.setdefault("first_chunk_ms", elapsed_ms(self.record.started))
                        yield chunk
                    finished = True
                except Exception:
                    self.record.stream_failed(response.status, "internal_error")
                    raise
                finally:
                    await chunks.aclose()
                    response.close()
                    if not finished:
                        capture.complete = False
                        self.record.stream_failed(response.status, "client_disconnected")

            self.record.deferred = True
            return _ClosingStreamingResponse(
                stream(), status_code=response.status, headers=headers, cleanup=self.finish
            )
        try:
            content = await response.read()
            if response.status < 300 and not response.headers.get("content-encoding"):
                try:
                    capture.nonstream(orjson.loads(content))
                except (ValueError, TypeError, KeyError):
                    pass
            elif response.status >= 300 and (code := error_code(content)):
                self.metrics["upstream_error_code"] = code
        finally:
            response.close()
        await self.relay()
        self.record.deferred = True
        return _FinishingResponse(
            content, status_code=response.status, headers=headers, cleanup=self.finish
        )
