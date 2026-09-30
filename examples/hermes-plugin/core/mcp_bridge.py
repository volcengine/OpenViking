"""OpenViking MCP tools over short-lived sessions of the official Python SDK.

Every operation opens its own session: connect, ``initialize``, one request, close.
The coroutine runs on a one-shot daemon thread under a deadline, so a caller on a
Hermes agent thread never shares an event loop with anything else and never waits
longer than the deadline. The server's ``/mcp`` endpoint is stateless, so a fresh
session costs one extra ``initialize`` round trip.

Nothing here imports Hermes or the plugin package. The endpoint, the request
header builder, the thread spawner (Hermes passes ``spawn_context_thread`` so the
caller's profile context follows the call), the clock and, in tests, the session
factory are injected through :class:`McpConnection`. ``mcp`` and ``httpx2`` are
imported only inside the call path; importing this module starts no thread,
registers nothing and opens no socket.

Result conversion follows the pi bridge (``examples/pi-coding-agent-extension/lib/
mcp-result.mjs``), except that Hermes tool results are plain strings: image and
audio blocks become one-line notes.
"""

from __future__ import annotations

import asyncio
import builtins
import itertools
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncContextManager, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("plugins.memory.openviking.mcp")

HANDSHAKE_TIMEOUT_SECONDS = 3.0
CALL_TIMEOUT_SECONDS = 15.0
MAX_RESULT_BYTES = 50 * 1024
MAX_RESULT_LINES = 2000
TRUNCATION_HINT = (
    "\n[OpenViking] Output truncated. Request fewer items or use a narrower URI, offset or limit."
)
ROOT_KEY_HINT = (
    "Hint: OpenViking /mcp rejects the root API key. Configure a user API key "
    "(or an account admin key) for this profile."
)

# Tools that can be retried once after a transport failure. Kept explicit on
# purpose: server annotations are not trusted (the server marks `search` as
# destructive), and anything missing here is treated as a write.
READ_ONLY_TOOLS = frozenset(
    {"find", "search", "read", "list", "tree", "grep", "glob", "health", "list_watches"}
)

_THREAD_PREFIX = "openviking-mcp"
# How long the caller waits past the deadline for the worker's own cancellation
# to report back before abandoning the thread.
_ABANDON_GRACE_SECONDS = 0.25
# A read-only retry is skipped when less than this much budget remains.
_MIN_RETRY_BUDGET_SECONDS = 0.25
_MAX_LIST_PAGES = 20
_thread_ids = itertools.count(1)
_log_filter_lock = threading.Lock()
_log_filter_installed = False
# Plugin requires Python 3.11+, but the repository's lint target is 3.10.
_EXCEPTION_GROUPS = getattr(builtins, "BaseExceptionGroup", ())

# session_factory(url, headers, on_http_status, timeout) -> async context manager
# yielding a session with:
#   async initialize() -> Any
#   async list_tools(cursor: Optional[str]) -> (list of tool dicts, next cursor or None)
#   async call_tool(name: str, arguments: dict) -> MCP CallToolResult as a camelCase dict
# `on_http_status(status)` must be called for every HTTP response with status >= 400.
SessionFactory = Callable[
    [str, Dict[str, str], Callable[[int], None], float], AsyncContextManager[Any]
]


def _default_spawn(
    target: Callable[..., Any],
    *,
    name: str,
    daemon: bool = True,
    args: tuple = (),
    kwargs: Optional[Dict[str, Any]] = None,
) -> threading.Thread:
    """Unstarted thread with the signature of Hermes ``spawn_context_thread``."""
    return threading.Thread(target=target, name=name, daemon=daemon, args=args, kwargs=kwargs)


@dataclass(frozen=True)
class McpConnection:
    """Where and how to reach OpenViking ``/mcp``; everything host-specific is injected.

    ``headers`` is called on the caller's thread for every operation, so it sees
    the caller's profile context and current credentials.
    """

    url: str
    headers: Callable[[], Mapping[str, str]]
    spawn: Callable[..., threading.Thread] = _default_spawn
    clock: Callable[[], float] = time.monotonic
    session_factory: Optional[SessionFactory] = None
    handshake_timeout: float = HANDSHAKE_TIMEOUT_SECONDS
    call_timeout: float = CALL_TIMEOUT_SECONDS
    client_name: str = "openviking-memory-hermes"
    client_version: str = "0.0.0"


@dataclass(frozen=True)
class ToolResult:
    """One tool call as Hermes sees it.

    ``uncertain`` means a write may or may not have been applied: the request was
    sent but no answer arrived. ``http_status`` is set when an HTTP error status
    was observed.
    """

    text: str
    is_error: bool = False
    http_status: Optional[int] = None
    uncertain: bool = False
    truncated: bool = False


class McpBridgeError(RuntimeError):
    """``list_tools`` failed; ``http_status`` is set when the server answered with one."""

    def __init__(self, message: str, *, http_status: Optional[int] = None):
        super().__init__(message)
        self.http_status = http_status


class _HandshakeTimeout(TimeoutError):
    """``initialize`` did not finish within the handshake budget."""


@dataclass
class _Progress:
    """What one operation reached; written by the worker, read by the caller afterwards."""

    stage: str = "connect"
    http_status: Optional[int] = None
    attempts: int = 0
    result: Any = None
    received: bool = False


# --------------------------------------------------------------------------- results


def _as_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _join_text(blocks: List[Any]) -> str:
    return "\n".join(
        str(block.get("text") if block.get("text") is not None else "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )


def content_to_text(result: Any) -> str:
    """Flatten an MCP ``tools/call`` result into one string.

    Text blocks are kept verbatim; every other block becomes one readable line so
    the model still learns what came back.
    """
    result = _as_dict(result)
    if not isinstance(result, dict):
        result = {}
    blocks = result.get("content")
    blocks = [_as_dict(block) for block in blocks] if isinstance(blocks, list) else []
    out: List[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append(str(block.get("text") if block.get("text") is not None else ""))
        elif kind in ("image", "audio"):
            mime = block.get("mimeType") or block.get("mime_type") or "unknown type"
            out.append(f"[{kind} content omitted ({mime})]")
        elif kind == "resource":
            resource = block.get("resource") if isinstance(block.get("resource"), dict) else {}
            inline = f"\n{resource['text']}" if isinstance(resource.get("text"), str) else ""
            mime = f" ({resource['mimeType']})" if resource.get("mimeType") else ""
            out.append(f"[resource {resource.get('uri') or '?'}{mime}]{inline}")
        elif kind == "resource_link":
            label = f" — {block['name']}" if block.get("name") else ""
            out.append(f"[resource link {block.get('uri') or '?'}{label}]")
        else:
            out.append(f"[unsupported MCP content block: {kind if kind is not None else '?'}]")

    # structuredContent is appended only when it says something the upstream text
    # does not. FastMCP echoes {"result": "<the same text>"} beside the text block
    # for every tool annotated `-> str`, so compare against the upstream text, never
    # against the notes synthesised above.
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        text = _join_text(blocks).strip()
        serialized = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
        duplicate = (
            isinstance(structured.get("result"), str) and structured["result"].strip() == text
        ) or serialized.strip() == text
        if not duplicate and serialized:
            out.append(serialized)
    return "\n".join(out)


def bound_text(text: str) -> Tuple[str, bool]:
    """Cap ``text`` at MAX_RESULT_BYTES UTF-8 bytes and MAX_RESULT_LINES lines.

    The truncation notice counts against both limits.
    """
    lines = text.split("\n")
    if len(text.encode("utf-8")) <= MAX_RESULT_BYTES and len(lines) <= MAX_RESULT_LINES:
        return text, False
    kept = "\n".join(lines[: MAX_RESULT_LINES - 1]).encode("utf-8")
    limit = MAX_RESULT_BYTES - len(TRUNCATION_HINT.encode("utf-8"))
    # Dropping a partial trailing character keeps the output valid UTF-8.
    return kept[:limit].decode("utf-8", errors="ignore") + TRUNCATION_HINT, True


def to_tool_result(tool: str, result: Any) -> ToolResult:
    """Convert an MCP ``tools/call`` result, bounding its size."""
    data = _as_dict(result)
    is_error = bool(data.get("isError")) if isinstance(data, dict) else False
    text, truncated = bound_text(content_to_text(data))
    if is_error and not text.strip():
        text = f"OpenViking {tool} failed"
    return ToolResult(text=text, is_error=is_error, truncated=truncated)


# ---------------------------------------------------------------- error classification


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, _EXCEPTION_GROUPS) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _is_protocol_error(exc: BaseException) -> bool:
    """A JSON-RPC error answer: the server processed and rejected the request."""
    try:
        from mcp.shared.exceptions import MCPError
    except Exception:  # pragma: no cover - mcp missing entirely
        return False
    return isinstance(exc, MCPError)


def _status_of(exc: BaseException) -> Optional[int]:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _is_transport_failure(exc: BaseException, status: Optional[int]) -> bool:
    """Connection-level failure after which a read-only call may be retried once."""
    if status is not None:
        return status >= 500
    # The overall deadline arrives as cancellation, never as an exception here, so
    # a TimeoutError seen by the retry loop is a handshake or socket timeout.
    return not _is_protocol_error(exc)


def _describe(exc: BaseException, status: Optional[int]) -> str:
    if isinstance(exc, TimeoutError):
        message = str(exc) or "timed out"
    else:
        message = str(exc).strip() or type(exc).__name__
        if not _is_protocol_error(exc) and type(exc).__name__ not in message:
            message = f"{type(exc).__name__}: {message}"
    return f"HTTP {status}: {message}" if status is not None else message


def _with_hint(text: str, status: Optional[int]) -> str:
    return f"{text}\n{ROOT_KEY_HINT}" if status == 403 else text


# ---------------------------------------------------------------- worker thread


class _CancelledStreamFilter(logging.Filter):
    """Drop the SDK's traceback for a response that arrives after a deadline closed its reader."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not str(record.threadName or "").startswith(_THREAD_PREFIX):
            return True
        exc = record.exc_info[1] if record.exc_info else None
        return not (
            exc is not None and type(exc).__name__ in ("ClosedResourceError", "BrokenResourceError")
        )


def _install_log_filter() -> None:
    global _log_filter_installed
    with _log_filter_lock:
        if not _log_filter_installed:
            logging.getLogger("mcp.client.streamable_http").addFilter(_CancelledStreamFilter())
            _log_filter_installed = True


def _quiet_loop_errors(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
    # Cancelling a streamed response can leave an HTTP async generator that fails
    # to close at loop shutdown. It leaks nothing; keep it out of stderr.
    if "asynchronous generator" in str(context.get("message", "")):
        logger.debug("OpenViking MCP: %s", context.get("message"))
        return
    loop.default_exception_handler(context)


async def _run_with_deadline(work: Callable[[], Any], timeout: float) -> Any:
    asyncio.get_running_loop().set_exception_handler(_quiet_loop_errors)
    return await asyncio.wait_for(work(), timeout)


_ABANDONED = object()


def _run_on_thread(conn: McpConnection, work: Callable[[], Any], timeout: float, label: str):
    """Run ``work()`` on a one-shot daemon thread; return (value, error).

    ``value`` is ``_ABANDONED`` when the worker did not report back in time. The
    thread is then left to finish on its own; it is never joined.
    """
    box: Dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            box["value"] = asyncio.run(_run_with_deadline(work, timeout))
        except BaseException as exc:  # reported to the caller, never raised on the thread
            box["error"] = exc
        finally:
            done.set()

    _install_log_filter()
    thread = conn.spawn(target, name=f"{_THREAD_PREFIX}-{label}-{next(_thread_ids)}", daemon=True)
    thread.start()
    if not done.wait(max(0.0, timeout) + _ABANDON_GRACE_SECONDS):
        logger.debug(
            "OpenViking MCP %s missed its %.1fs deadline; abandoning thread", label, timeout
        )
        return _ABANDONED, None
    return box.get("value"), box.get("error")


# ---------------------------------------------------------------- default session


@lru_cache(maxsize=1)
def _quiet_session_class():
    from mcp import ClientSession

    class _Session(ClientSession):
        # The bridge flattens content to text and never reads structured output, so
        # skip output-schema validation: it lists tools again when the tool is not
        # cached, and it would turn an applied write into an error.
        async def validate_tool_result(self, name, result):
            return None

    return _Session


class _SdkSession:
    """Adapts ``mcp.ClientSession`` to the plain-dict session protocol."""

    def __init__(self, session: Any, types: Any):
        self._session = session
        self._types = types

    async def initialize(self) -> Any:
        return await self._session.initialize()

    async def list_tools(self, cursor: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params = self._types.PaginatedRequestParams(cursor=cursor) if cursor else None
        listed = await self._session.list_tools(params=params)
        return [_as_dict(tool) for tool in listed.tools], listed.next_cursor

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return _as_dict(await self._session.call_tool(name, arguments))


def _sdk_session_factory(client_name: str, client_version: str) -> SessionFactory:
    @asynccontextmanager
    async def factory(url, headers, on_http_status, timeout):
        import httpx2
        from mcp import types
        from mcp.client.streamable_http import streamable_http_client

        async def record(response):
            if response.status_code >= 400:
                on_http_status(response.status_code)

        info = types.Implementation(name=client_name, version=client_version)
        # The SDK never closes a caller-provided HTTP client, so this block owns it.
        async with httpx2.AsyncClient(
            headers=dict(headers),
            timeout=httpx2.Timeout(max(timeout, 0.1)),
            event_hooks={"response": [record]},
        ) as http:
            async with streamable_http_client(url, http_client=http) as (read, write):
                async with _quiet_session_class()(read, write, client_info=info) as session:
                    yield _SdkSession(session, types)

    return factory


# ---------------------------------------------------------------- public API


def _build_headers(conn: McpConnection) -> Dict[str, str]:
    return {str(k): str(v) for k, v in dict(conn.headers() or {}).items()}


def _normalize_tool(tool: Any) -> Optional[Dict[str, Any]]:
    tool = _as_dict(tool)
    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
        return None
    schema = tool.get("inputSchema")
    return {
        "name": tool["name"],
        "description": tool.get("description") if isinstance(tool.get("description"), str) else "",
        "inputSchema": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
    }


def list_tools(conn: McpConnection, *, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """The server's tool catalog as ``{name, description, inputSchema}`` dicts.

    One budget (default ``conn.handshake_timeout``) covers connecting,
    ``initialize`` and every ``tools/list`` page. Raises :class:`McpBridgeError`.
    """
    budget = conn.handshake_timeout if timeout is None else float(timeout)
    try:
        headers = _build_headers(conn)
    except Exception as exc:
        raise McpBridgeError(f"OpenViking MCP request headers unavailable: {exc}") from exc
    factory = conn.session_factory or _sdk_session_factory(conn.client_name, conn.client_version)
    progress = _Progress()

    def on_status(status: int) -> None:
        progress.http_status = status

    async def work() -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        async with factory(conn.url, headers, on_status, budget) as session:
            progress.stage = "initialize"
            await session.initialize()
            progress.stage = "tools/list"
            cursor: Optional[str] = None
            for _ in range(_MAX_LIST_PAGES):
                page, cursor = await session.list_tools(cursor)
                tools.extend(t for t in map(_normalize_tool, page or []) if t is not None)
                if not cursor:
                    break
        return tools

    value, error = _run_on_thread(conn, work, budget, "list")
    if value is _ABANDONED and error is None:
        error = TimeoutError()
    if error is not None:
        leaf = _leaf(error)
        status = _status_of(leaf) or progress.http_status
        if isinstance(leaf, TimeoutError):
            message = f"timed out after {budget:g}s during {progress.stage}"
            message = f"HTTP {status}: {message}" if status is not None else message
        else:
            message = _describe(leaf, status)
        text = _with_hint(f"OpenViking MCP tools/list failed: {message}", status)
        raise McpBridgeError(text, http_status=status) from leaf
    return value


def call_tool(
    conn: McpConnection,
    name: str,
    arguments: Optional[Mapping[str, Any]] = None,
    *,
    timeout: Optional[float] = None,
) -> ToolResult:
    """Call server tool ``name`` (unprefixed) and return a :class:`ToolResult`; never raises.

    One budget (default ``conn.call_timeout``) covers connecting, ``initialize``
    and the call; ``initialize`` alone is also capped at ``conn.handshake_timeout``.
    A failed call is never replayed, except that a tool in :data:`READ_ONLY_TOOLS`
    gets one fresh session after a transport failure when budget remains. A write
    that was sent without an answer comes back with ``uncertain=True``.
    """
    budget = conn.call_timeout if timeout is None else float(timeout)
    read_only = name in READ_ONLY_TOOLS
    try:
        headers = _build_headers(conn)
    except Exception as exc:
        return ToolResult(
            f"OpenViking {name} failed: request headers unavailable: {exc}", is_error=True
        )
    args = dict(arguments or {})
    factory = conn.session_factory or _sdk_session_factory(conn.client_name, conn.client_version)
    deadline = conn.clock() + budget
    progress = _Progress()

    def on_status(status: int) -> None:
        progress.http_status = status

    def remaining() -> float:
        return max(deadline - conn.clock(), 0.0)

    async def attempt() -> None:
        progress.stage = "connect"
        progress.http_status = None
        progress.attempts += 1
        try:
            async with factory(conn.url, headers, on_status, remaining()) as session:
                progress.stage = "initialize"
                handshake = min(conn.handshake_timeout, remaining())
                try:
                    await asyncio.wait_for(session.initialize(), handshake)
                except TimeoutError as exc:
                    raise _HandshakeTimeout(
                        f"MCP initialize timed out after {handshake:g}s"
                    ) from exc
                progress.stage = "call"
                progress.result = await session.call_tool(name, args)
                progress.received = True
        except Exception:
            # A result that arrived before closing failed is still the answer.
            if not progress.received:
                raise
            logger.debug("OpenViking MCP %s: closing the session failed", name, exc_info=True)

    async def work() -> None:
        while True:
            try:
                await attempt()
                return
            except Exception as exc:
                leaf = _leaf(exc)
                status = _status_of(leaf) or progress.http_status
                if (
                    read_only
                    and progress.attempts < 2
                    and _is_transport_failure(leaf, status)
                    and remaining() > _MIN_RETRY_BUDGET_SECONDS
                ):
                    logger.debug("OpenViking MCP %s: retrying once after %r", name, leaf)
                    continue
                raise

    _, error = _run_on_thread(conn, work, budget, name)
    if progress.received:
        return to_tool_result(name, progress.result)
    if error is None:
        # Abandoned: the worker neither answered nor reported its own timeout.
        error = TimeoutError()
    leaf = _leaf(error)
    status = _status_of(leaf) or progress.http_status
    if isinstance(leaf, TimeoutError) and not isinstance(leaf, _HandshakeTimeout):
        reason = f"timed out after {budget:g}s"
        reason = f"HTTP {status}: {reason}" if status is not None else reason
    else:
        reason = _describe(leaf, status)
    # A write may have been applied only when it failed after being sent and no
    # HTTP 4xx or JSON-RPC error says the server rejected it. The SDK reports an
    # HTTP 5xx as a JSON-RPC error too, so the status decides when there is one.
    rejected = status < 500 if status is not None else _is_protocol_error(leaf)
    uncertain = not read_only and progress.stage == "call" and not rejected
    if uncertain:
        text = (
            f"OpenViking {name} outcome unknown: {reason}. The request may already have been "
            "applied on the server; check the current state before calling it again."
        )
    else:
        text = f"OpenViking {name} failed: {reason}"
    return ToolResult(
        _with_hint(text, status), is_error=True, http_status=status, uncertain=uncertain
    )
