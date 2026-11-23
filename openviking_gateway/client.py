# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking HTTP adapter. No imports from the server/service implementation."""

import asyncio
import hashlib
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

import aiohttp
from packaging.version import InvalidVersion, Version

from .cache import TTLCache

# Recall adds this peer's memories; other peers' still come back with a small score penalty
# unless the body sets peer_scope "actor". Tool calls carry it only under "actor". Without a
# peer there is nothing to narrow to, so "actor" sends what "all" does.
PEER_HEADER = "X-OpenViking-Actor-Peer"
# OpenViking's bounds for a session's automatic commits.
MIN_IDLE_SECONDS = 300
MAX_COMMIT_TOKENS = 1_000_000


@dataclass
class CallContext:
    """What OpenViking calls made while serving one gateway request carry."""

    request_id: str = ""
    # The OpenViking account whose breaker circuit the calls consult and feed.
    circuit: str | None = None
    # ``time.monotonic()`` by which the request's OpenViking calls must have answered.
    deadline: float | None = None


# Set per request; tasks the request starts copy it, background work has none.
CALL: ContextVar[CallContext | None] = ContextVar("openviking_call", default=None)


@contextmanager
def without_deadline():
    """Calls made inside keep the request's ID and circuit but not its OpenViking budget."""
    call = CALL.get()
    if call is None or call.deadline is None:
        yield
        return
    bound = CALL.set(replace(call, deadline=None))
    try:
        yield
    finally:
        CALL.reset(bound)


def peer_header(peer):
    return {PEER_HEADER: peer} if peer else {}


def call_headers():
    """``X-Request-ID`` of the gateway request an OpenViking call is made for."""
    call = CALL.get()
    return {"X-Request-ID": call.request_id} if call and call.request_id else {}


def peer_argument(peer):
    """Keyword arguments for a call made for a peer; none without one."""
    return {"peer": peer} if peer else {}


class VikingError(Exception):
    def __init__(self, reason, status=503):
        super().__init__(reason)
        self.reason, self.status = reason, status


def unanswered(error):
    """Whether a failed call says OpenViking is not answering, rather than refusing."""
    if error.reason in ("openviking_unavailable", "openviking_timeout"):
        return True
    return error.reason.startswith("openviking_http_") and (
        error.status >= 500 or error.status == 429
    )


class Breaker:
    """Per-account circuits: consecutive unanswered calls stop calls for a while."""

    def __init__(self, failures, seconds, capacity=4096):
        self.failures, self.seconds, self.capacity = failures, seconds, capacity
        self.circuits = OrderedDict()

    def allow(self, key) -> bool:
        circuit = self.circuits.get(key)
        if circuit is None or not circuit["opened_until"]:
            return True
        self.circuits.move_to_end(key)
        now = time.monotonic()
        # Open until then, after which one trial call at a time decides.
        if now < circuit["opened_until"] or now < circuit["trial"]:
            return False
        # A trial that never reports back, such as a cancelled one, expires as well.
        circuit["trial"] = now + self.seconds
        return True

    def record(self, key, answered: bool):
        if answered:
            self.circuits.pop(key, None)
            return
        circuit = self.circuits.setdefault(key, {"failures": 0, "opened_until": 0, "trial": 0})
        self.circuits.move_to_end(key)
        circuit["failures"] += 1
        if circuit["failures"] >= self.failures or circuit["opened_until"]:
            circuit["opened_until"] = time.monotonic() + self.seconds
            circuit["trial"] = 0
        if len(self.circuits) > self.capacity:
            self.circuits.popitem(last=False)

    def open_count(self) -> int:
        now = time.monotonic()
        return sum(now < circuit["opened_until"] for circuit in self.circuits.values())


class VikingClient:
    def __init__(
        self,
        http: aiohttp.ClientSession,
        base_url: str,
        min_version: str,
        breaker_failures=0,
        breaker_seconds=30,
    ):
        self.http, self.base_url, self.min_version = http, base_url.rstrip("/"), min_version
        self.breaker = Breaker(breaker_failures, breaker_seconds) if breaker_failures else None
        self.unavailable_reason = ""
        self.identities = TTLCache(ttl=30, capacity=4096)
        # Every caller sees the same MCP tools; the last good list outlives failures.
        self.tools_cache, self.last_tools = TTLCache(ttl=300, capacity=1), None
        # Until a list has loaded once, a failed load is retried at most this often.
        self.tools_retry_at = 0.0

    def budget(self, timeout):
        """``timeout``, cut to what is left of the request's OpenViking budget."""
        call = CALL.get()
        if call is None or call.deadline is None:
            return timeout
        remaining = call.deadline - time.monotonic()
        if remaining < 0.05:
            raise VikingError("openviking_budget_exhausted")
        return min(timeout, remaining)

    @contextmanager
    def guarded(self, errors):
        """One call under the request's circuit; ``errors`` mean OpenViking did not answer."""
        context = CALL.get()
        circuit = context.circuit if context and self.breaker else None
        if circuit and not self.breaker.allow(circuit):
            raise VikingError("openviking_circuit_open")
        try:
            yield
        except VikingError as error:
            failure, cause = error, None
        # Before ClientError: aiohttp's timeouts are both.
        except asyncio.TimeoutError as error:
            failure, cause = VikingError("openviking_timeout"), error
        except errors as error:
            failure, cause = VikingError("openviking_unavailable"), error
        else:
            if circuit:
                self.breaker.record(circuit, True)
            return
        if circuit:
            self.breaker.record(circuit, not unanswered(failure))
        if cause is None:
            raise failure
        raise failure from cause

    async def request(self, method, path, key, body=None, timeout=30, peer=None):
        """``peer`` is the actor peer for recall; OpenViking's session routes ignore it."""
        with self.guarded((aiohttp.ClientError, ValueError)):
            async with self.http.request(
                method,
                self.base_url + path,
                json=body,
                headers={
                    "X-API-Key": key,
                    "Accept-Encoding": "identity",
                    **peer_header(peer),
                    **call_headers(),
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise VikingError(f"openviking_http_{response.status}", response.status)
                data = await response.json()
                if data.get("status") == "error":
                    raise VikingError("openviking_" + data.get("error", {}).get("code", "error"))
                return data.get("result", data)

    async def identify(self, credential=""):
        """Identify a caller; only successful identities are cached, for 30 seconds."""

        async def load():
            result = await self.request("GET", "/health", credential, timeout=self.budget(5))
            if result.get("auth_mode") == "trusted":
                raise VikingError("trusted_mode_unsupported", 403)
            if result.get("auth_mode") == "dev":
                result = {**result, "role": "root", "account_id": "default", "user_id": "default"}
            if result.get("role", "").lower() not in {"root", "admin", "user"} or not all(
                result.get(field) for field in ("account_id", "user_id")
            ):
                raise VikingError("openviking_identity_missing", 401)
            # OV answered, so recall and revocation checks may resume before the next probe.
            self.unavailable_reason = ""
            return {**result, "role": result["role"].lower()}

        return await self.identities.get(hashlib.sha256(credential.encode()).hexdigest(), load)

    async def health(self, key="", require_identity=True):
        if require_identity:
            result = await self.identify(key)
            if result["role"] == "root" and result.get("auth_mode") != "dev":
                raise VikingError("root_key_not_allowed", 403)
            try:
                if Version(result.get("version", "0")) < Version(self.min_version):
                    raise VikingError("openviking_version_mismatch")
            except InvalidVersion as error:
                raise VikingError("openviking_version_mismatch") from error
            return result
        try:
            result = await self.request("GET", "/health", key, timeout=5)
        except VikingError as error:
            if error.status == 401 and not key:
                return {"status": "ok", "auth_mode": "api_key", "requires_authentication": True}
            raise
        if result.get("auth_mode") == "trusted":
            raise VikingError("trusted_mode_unsupported", 403)
        return result

    async def recall(self, key, query, policy, exclude, budget, peer=None):
        if self.unavailable_reason:
            raise VikingError(self.unavailable_reason)
        return await self.request(
            "POST",
            "/api/v1/search/search",
            key,
            {
                "query": query,
                "mode": "context",
                "query_expansion": "off",
                "context_type": policy.context_types,
                "max_tokens": budget,
                "quotas": policy.quotas or None,
                "score_threshold": policy.score_threshold,
                "exclude_uris": exclude[-200:],
                "rewrite": False,
                **({"peer_scope": "actor"} if peer and policy.recall_peer_scope == "actor" else {}),
            },
            timeout=self.budget(policy.recall_timeout),
            peer=peer,
        )

    async def write(self, key, session, messages):
        return await self.request(
            "POST", f"/api/v1/sessions/{session}/messages/batch", key, {"messages": messages}
        )

    async def create_session(self, key, session, policy, auto_commit=False):
        """Create the session, or set an existing one's automatic commits the same way.

        With ``auto_commit`` OpenViking commits the session by itself: when the
        uncommitted messages pass the profile's ``commit_tokens``, keeping the latest
        ``keep_recent_messages`` live, and everything left once the session has been
        idle for ``idle_seconds``, which needs ``memory.session_auto_commit.enabled``
        on the server. Without it OpenViking's automatic commits are off and the
        gateway commits.
        """
        commits = {
            "pending_token_threshold": 0,
            "message_count_threshold": 0,
            "idle_timeout_seconds": 0,
        }
        if auto_commit:
            commits = {
                "pending_token_threshold": min(policy.commit_tokens, MAX_COMMIT_TOKENS),
                "message_count_threshold": 0,
                "idle_timeout_seconds": max(MIN_IDLE_SECONDS, round(policy.idle_seconds)),
                "keep_recent_count": policy.keep_recent_messages,
            }
        memory_policy = {}
        # The gateway owns summaries. From 0.5.0 working memory is off unless the user
        # turns it on, and a session policy would replace the user's whole policy.
        if not await self._working_memory_opt_in(key):
            memory_policy["working_memory"] = {"enabled": False}
        if policy.capture_memory_types is not None:
            memory_policy["memory_types"] = policy.capture_memory_types
        try:
            await self.request(
                "POST",
                "/api/v1/sessions",
                key,
                {
                    "session_id": session,
                    "auto_commit_policy": commits,
                    **({"memory_policy": memory_policy} if memory_policy else {}),
                },
            )
        except VikingError as error:
            if error.status != 409:
                raise
            # The session exists: an earlier gateway version or the other setting created it.
            await self.request(
                "PATCH",
                f"/api/v1/sessions/{session}/config",
                key,
                {"auto_commit_policy": commits},
            )

    async def commit(self, key, session, keep=0):
        body = {"keep_recent_count": keep}
        # The gateway owns summaries; leave the resolved extraction policy intact.
        if await self._working_memory_opt_in(key):
            body["enable_working_memory"] = False
        return await self.request(
            "POST",
            f"/api/v1/sessions/{session}/commit",
            key,
            body,
            timeout=45,
        )

    async def _working_memory_opt_in(self, key):
        """From 0.5.0 a commit writes working memory only where a policy asks for it."""
        return Version((await self.health(key))["version"]) >= Version("0.5.0")

    async def tools(self, key):
        async def load():
            try:
                result = await self.mcp("tools/list", key, timeout=self.budget(5))
                tools = result.get("tools") if isinstance(result, dict) else None
                if not isinstance(tools, list) or not all(
                    isinstance(tool, dict)
                    and isinstance(tool.get("name"), str)
                    and isinstance(tool.get("inputSchema"), dict)
                    for tool in tools
                ):
                    raise VikingError("openviking_mcp_invalid_catalog")
                self.last_tools = tools
            except VikingError as error:
                if self.last_tools is None:
                    # Only an OpenViking that did not answer blocks the load for every account.
                    if unanswered(error):
                        self.tools_retry_at = time.monotonic() + 30
                    raise
            return self.last_tools

        if self.last_tools is None and time.monotonic() < self.tools_retry_at:
            raise VikingError("openviking_unavailable")
        return await self.tools_cache.get("tools/list", load)

    async def mcp(self, method, key, params=None, timeout=120, peer=None):
        """Stateless Streamable HTTP MCP; accept both JSON and SSE responses."""
        from .protocols import SSEDecoder

        with self.guarded((aiohttp.ClientError, ValueError, KeyError)):
            async with self.http.post(
                self.base_url + "/mcp",
                headers={
                    "X-API-Key": key,
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                    "Accept-Encoding": "identity",
                    **peer_header(peer),
                    **call_headers(),
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params or {},
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise VikingError(f"openviking_http_{response.status}", response.status)
                if "text/event-stream" in response.headers.get("content-type", ""):
                    decoder = SSEDecoder()
                    async for chunk in response.content.iter_any():
                        for frame in decoder.feed(chunk):
                            value = decoder.data(frame)
                            if value and value.get("id") == 1:
                                if "error" in value:
                                    raise VikingError("openviking_mcp_error")
                                return value["result"]
                    raise VikingError("openviking_mcp_incomplete")
                value = await response.json()
                if "error" in value:
                    raise VikingError("openviking_mcp_error")
                return value["result"]

    async def upload(self, token, filename, content):
        data = aiohttp.FormData()
        data.add_field("file", content, filename=filename, content_type="application/octet-stream")
        try:
            async with self.http.post(
                self.base_url + "/api/v1/resources/temp_upload",
                params={"token": token},
                headers={"Accept-Encoding": "identity", **call_headers()},
                data=data,
                timeout=aiohttp.ClientTimeout(total=120),
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise VikingError("openviking_upload_failed", response.status)
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise VikingError("openviking_upload_unavailable") from error
