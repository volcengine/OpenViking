"""OpenViking REST client, error formatting, timeout classification and identity probe."""

from __future__ import annotations

import mimetypes
import re
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .host import get_secret
from .log import get_logger
from .settings import _DEFAULT_AGENT


def default_deps():
    """The current default ``Deps``, looked up at call time: ``deps`` imports this module."""
    from .deps import default_deps as current

    return current()


logger = get_logger()




def _read_plugin_version() -> str:
    """The plugin's own version from plugin.yaml; ``0.0.0`` when it cannot be read."""
    with suppress(OSError, UnicodeDecodeError):
        text = (Path(__file__).resolve().parent.parent / "plugin.yaml").read_text(encoding="utf-8")
        match = re.search(r"^version:\s*[\"']?([^\s\"']+)", text, flags=re.MULTILINE)
        if match:
            return match.group(1)
    return "0.0.0"


def build_user_agent(plugin: str, version: str) -> str:
    """``openviking-memory-<plugin>/<version>``; an empty version falls back to 0.0.0."""
    return f"openviking-memory-{plugin}/{version or '0.0.0'}"


@lru_cache(maxsize=1)
def plugin_version() -> str:
    """The plugin version, read from plugin.yaml on first use, never at import time."""
    return _read_plugin_version()


def _openviking_user_agent() -> str:
    return build_user_agent("hermes", plugin_version())


def build_openviking_headers(*, api_key: str = "", account: str = "", user: str = "",
                             trusted_identity: bool = False, identity_override: bool | None = None,
                             actor_peer_id: str = "", user_agent: str = "",
                             content_type: bool = True) -> dict:
    """Headers for one OpenViking request, shared by the REST client and the MCP bridge.

    The key goes out only as ``Authorization: Bearer``. Account and user headers are
    sent only for a trusted server, or when ``identity_override`` asks for them on
    this request. Empty values are omitted.
    """
    headers = {"Content-Type": "application/json"} if content_type else {}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    send_identity = trusted_identity if identity_override is None else identity_override
    if send_identity:
        if account:
            headers["X-OpenViking-Account"] = account
        if user:
            headers["X-OpenViking-User"] = user
    if actor_peer_id:
        headers["X-OpenViking-Actor-Peer"] = actor_peer_id
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


def is_retryable_failure(status: Optional[int], error: Any = None) -> bool:
    """Whether a failed OpenViking call is worth retrying (contract ``retryable.json``).

    ``status`` 0 or None means no HTTP response arrived. 408, 429 and 5xx retry; 409
    retries only when the error body carries ``details.retryable is True``.
    """
    if not status:
        return True
    if status in (408, 429) or 500 <= status <= 599:
        return True
    if status == 409 and isinstance(error, dict):
        details = error.get("details")
        return isinstance(details, dict) and details.get("retryable") is True
    return False

_IDENTITY_UNSET = object()
_TIMEOUT = 30.0
# Identity probe states; "modern" and "legacy" are the two identified ones.
_OPENVIKING_IDENTIFIED_STATES = frozenset({"modern", "legacy"})


class _OpenVikingHTTPError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _sanitize_openviking_error_message(message: str, status_code: Optional[int] = None) -> str:
    text = (message or "").strip()
    status = f"HTTP {status_code}" if status_code else "HTTP error"
    if re.search(r"^\s*<(!doctype|html|head|body)\b", text, flags=re.IGNORECASE):
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()
            title = title.split("|", 1)[1].strip() if "|" in title else title
            title = title.split(":", 1)[1].strip() if status_code and title.startswith(f"{status_code}:") else title
            if title:
                return f"{status}: {title}"
        return f"{status}: OpenViking endpoint returned an HTML error page."
    if len(text) > 300:
        return text[:297].rstrip() + "..."
    return text or status


def _status_code_from_error(error: Exception) -> Optional[int]:
    if isinstance(error, _OpenVikingHTTPError):
        return error.status_code
    return getattr(getattr(error, "response", None), "status_code", None)


def _format_openviking_exception(error: Exception) -> str:
    return _sanitize_openviking_error_message(str(error), _status_code_from_error(error))


def _get_httpx():
    """Lazy import httpx."""
    try:
        import httpx
        return httpx
    except ImportError:
        return None


def _is_timeout_error(error: BaseException) -> bool:
    """Identify socket and HTTP transport timeouts in query recall."""
    if isinstance(error, TimeoutError):
        return True
    try:
        from httpx import TimeoutException
    except ImportError:
        return False
    return isinstance(error, TimeoutException)


class _VikingClient:
    """Thin HTTP client for the OpenViking REST API (httpx, no SDK dependency)."""

    def __init__(self, endpoint: str, api_key: str = "",
                 account: Optional[str] | object = _IDENTITY_UNSET,
                 user: Optional[str] | object = _IDENTITY_UNSET,
                 agent: Optional[str] | object = _IDENTITY_UNSET, transport: Any = None):
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        # Account/user are local/trusted-mode tenant identity. API-key requests
        # omit these headers unless OpenViking explicitly asks for them (retry).
        # Tenant identity is a profile .env value: scope-read so a multiplexed
        # secondary never writes into the default profile's tenant.
        self._account = (get_secret("OPENVIKING_ACCOUNT", "") if account is _IDENTITY_UNSET or account is None else account) or "default"
        self._user = (get_secret("OPENVIKING_USER", "") if user is _IDENTITY_UNSET or user is None else user) or "default"
        self._agent = (get_secret("OPENVIKING_AGENT", "") or _DEFAULT_AGENT) if agent is _IDENTITY_UNSET or agent is None else agent
        # Every client owns its resolved identity, including clients retained across reloads.
        self._conn_snapshot = (self._endpoint, self._api_key, self._account, self._user, self._agent)
        # ``transport`` has httpx's get/post/delete; without one, the default Deps supplies it.
        self._httpx = transport if transport is not None else default_deps().transport()
        if self._httpx is None:
            raise ImportError("httpx is required for OpenViking: pip install httpx")

    def _headers(self, *, include_tenant: bool | None = None) -> dict:
        # Without a key the server is a local/trusted one; with a key, identity headers
        # go out only when the trusted-identity retry asks for them.
        return build_openviking_headers(
            api_key=self._api_key, account=self._account, user=self._user,
            trusted_identity=not bool(self._api_key), identity_override=include_tenant,
            actor_peer_id=self._agent, user_agent=_openviking_user_agent())

    @staticmethod
    def _needs_trusted_identity_retry(exc: Exception) -> bool:
        """Trusted mode asks for X-OpenViking-Account/User with wording that varies across
        versions; match the shape, but keep deliberate API-key denials (non-400) non-retriable."""
        message = str(exc)
        if "Trusted mode requests must include" not in message:
            return False
        if "X-OpenViking-Account" not in message and "X-OpenViking-User" not in message:
            return False
        return getattr(exc, "status_code", None) in (None, 400)

    def _multipart_headers(self, *, include_tenant: bool | None = None) -> dict:
        headers = self._headers(include_tenant=include_tenant)
        headers.pop("Content-Type", None)
        return headers

    def _send_with_trusted_identity_retry(self, send, *, multipart: bool = False) -> dict:
        build = self._multipart_headers if multipart else self._headers
        try:
            return self._parse_response(send(build()))
        except Exception as exc:
            if not self._api_key or not self._needs_trusted_identity_retry(exc):
                raise
            return self._parse_response(send(build(include_tenant=True)))

    def _parse_response(self, resp) -> dict:
        data = None
        with suppress(Exception):
            data = resp.json()
        error = data.get("error") if isinstance(data, dict) else None
        if resp.status_code >= 400:
            message = _sanitize_openviking_error_message(getattr(resp, "text", ""), resp.status_code)
            if isinstance(error, dict):
                details = error.get("details")
                raise _OpenVikingHTTPError(f"{error.get('code', 'HTTP_ERROR')}: {error.get('message', message)}", resp.status_code,
                                           retryable=isinstance(details, dict) and details.get("retryable") is True)
            if isinstance(data, dict) and data.get("status") == "error":
                raise _OpenVikingHTTPError(str(data), resp.status_code)
            raise _OpenVikingHTTPError(message or f"HTTP {resp.status_code}", resp.status_code)
        if isinstance(data, dict) and data.get("status") == "error":
            if isinstance(error, dict):
                raise RuntimeError(f"{error.get('code', 'OPENVIKING_ERROR')}: {error.get('message', '')}")
            raise RuntimeError(str(data))
        return {} if data is None else data

    def _request(self, method: str, path: str, kwargs: dict) -> dict:
        timeout = kwargs.pop("timeout", _TIMEOUT)
        fn = getattr(self._httpx, method)
        return self._send_with_trusted_identity_retry(lambda headers: fn(f"{self._endpoint}{path}", headers=headers, timeout=timeout, **kwargs))

    def get(self, path: str, **kwargs) -> dict:
        return self._request("get", path, kwargs)

    def post(self, path: str, payload: dict = None, **kwargs) -> dict:
        return self._request("post", path, {**kwargs, "json": payload or {}})

    def delete(self, path: str, **kwargs) -> dict:
        return self._request("delete", path, kwargs)

    def upload_temp_file(self, file_path: Path) -> str:
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

        def _send(headers):
            with file_path.open("rb") as f:
                return self._httpx.post(f"{self._endpoint}/api/v1/resources/temp_upload",
                                        files={"file": (file_path.name, f, mime_type)}, headers=headers, timeout=_TIMEOUT)

        temp_file_id = self._send_with_trusted_identity_retry(_send, multipart=True).get("result", {}).get("temp_file_id", "")
        if not temp_file_id:
            raise RuntimeError("OpenViking temp upload did not return temp_file_id")
        return temp_file_id

    def health(self) -> bool:
        with suppress(Exception):
            return _probe_openviking_identity(self)[0] in _OPENVIKING_IDENTIFIED_STATES
        return False

    def _anonymous_json(self, path: str) -> dict:
        """Probe server identity without disclosing credentials or tenant IDs."""
        return self._parse_response(self._httpx.get(f"{self._endpoint}{path}", headers={"Accept": "application/json"}, timeout=3.0))

    def health_payload(self) -> dict:
        """``GET /health``, anonymous first so credentials never reach an unknown host.
        Hosted OpenViking requires auth on /health: when an API key is configured and the
        anonymous call gets 401/403, retry once with the key (no tenant headers).

        Prefer an anonymous probe so credentials are never sent to an unknown host during identity checks.
        See #78410.
        """
        try:
            return self._anonymous_json("/health")
        except _OpenVikingHTTPError as exc:
            if not self._api_key or _status_code_from_error(exc) not in {401, 403}:
                raise
            return self._parse_response(self._httpx.get(f"{self._endpoint}/health", headers=self._headers(include_tenant=False), timeout=3.0))

    def openapi_payload(self) -> dict:
        return self._anonymous_json("/openapi.json")

    def validate_auth(self) -> dict:  # authenticated access, no mutation
        return self.get("/api/v1/system/status")

    def validate_root_access(self) -> dict:  # ROOT access via a read-only admin endpoint
        return self.get("/api/v1/admin/accounts")


def _resolve_user_space(client, *, timeout: Optional[float] = None,
                        raise_on_timeout: bool = False) -> Optional[str]:
    """Server-asserted current user for explicit-uid URIs; ``None`` when the probe fails or
    reports no user. Callers may fall back to a configured value for that one operation but
    must not cache an unverified identity — a later probe can succeed.
    Query recall can propagate timeouts to its bounded warning handler."""
    try:
        status = client.get("/api/v1/system/status", **({"timeout": timeout} if timeout is not None else {}))
    except Exception as exc:
        if raise_on_timeout and _is_timeout_error(exc):
            raise
        logger.debug("OpenViking user-space probe failed; using configured fallback", exc_info=True)
        return None
    return str(((status or {}).get("result") or {}).get("user") or "").strip() or None


def _probe_openviking_identity(client: _VikingClient) -> tuple[str, Any]:
    """Identify modern or legacy OpenViking before any authenticated request.
    -> ("modern" | "legacy" | "legacy-unverified" | "unhealthy" | "invalid", health).
    Modern = documented status/healthy/version contract; legacy = status-only (<= 0.2.6),
    which must be confirmed via the anonymous OpenAPI title."""
    health = client.health_payload()
    if isinstance(health, dict) and health.get("healthy") is False:
        return "unhealthy", health
    if not isinstance(health, dict) or health.get("status") != "ok":
        return "invalid", health
    if health.get("healthy") is True and isinstance(health.get("version"), str) and health["version"].strip():
        return "modern", health
    if "healthy" in health or "version" in health:
        return "invalid", health
    try:
        info = client.openapi_payload().get("info")
        verified = isinstance(info, dict) and info.get("title") == "OpenViking API"
    except Exception:
        logger.debug("Legacy OpenViking OpenAPI identity probe failed", exc_info=True)
        verified = False
    return ("legacy" if verified else "legacy-unverified"), health


class RestResultMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    @staticmethod
    def _unwrap_result(resp: Any) -> Any:
        """Return OpenViking payload body regardless of wrapped/unwrapped shape."""
        return resp.get("result") if isinstance(resp, dict) and "result" in resp else resp

    @classmethod
    def _extract_text_content(cls, resp: Any, *, strict: bool = False) -> str:
        """Text body from a content endpoint (plain string or {content|text} object);
        ``strict`` accepts only non-blank string fields."""
        result = cls._unwrap_result(resp)
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            if not strict:
                return str(result.get("content") or result.get("text") or "").strip()
            for key in ("content", "text"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""
