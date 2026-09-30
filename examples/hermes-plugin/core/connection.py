"""Connection settings, profile environment, client lifecycle, autostart and commit scopes."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Set

from .deps import _rest_client
from .endpoint import _is_local_openviking_url, _normalize_openviking_url, _OpenVikingEndpointError
from .host import _get_launch_hermes_home, get_secret, spawn_context_thread
from .http import _resolve_user_space, _VikingClient
from .local_server import (
    _LOCAL_OPENVIKING_AUTOSTART_TIMEOUT,
    _LOCAL_SERVER_STARTED,
    _start_local_openviking_server,
    _wait_for_openviking_health,
)
from .log import get_logger
from .ovcli import _ovcli_values_for
from .settings import _CONNECTION_KEYS, _DEFAULT_AGENT, _DEFAULT_ENDPOINT, _clean_config_value

logger = get_logger()


# After a refresh fails for an unchanged config, skip re-probing for this long so a
# down server doesn't cost every access a 3s probe + warning under _client_refresh_lock.
_FAILED_CONFIG_RETRY_COOLDOWN_SECONDS = 30.0
_RETRY_LATER = (
    "OpenViking memory is temporarily unavailable; Hermes will retry on a later access or when the config changes."
)
_FIX_ENDPOINT = "OpenViking memory is temporarily unavailable; correct the endpoint and reload the configuration."
_HTTPX_MISSING = "httpx not installed — OpenViking plugin disabled"


def _load_hermes_openviking_config(hermes_home: Optional[str] = None, *, env: Optional[dict] = None) -> dict:
    try:
        from .host import load_config_readonly
        if hermes_home:
            from .host import (
                reset_hermes_home_override,
                reset_secret_scope,
                set_hermes_home_override,
                set_secret_scope,
            )

            if env is None:
                env = _profile_openviking_env(hermes_home)
            home_token = set_hermes_home_override(hermes_home)
            scope_token = set_secret_scope(env, profile_home=hermes_home)
            try:
                config = load_config_readonly()
            finally:
                reset_secret_scope(scope_token)
                reset_hermes_home_override(home_token)
        else:
            config = load_config_readonly()
        memory_config = config.get("memory", {}) if isinstance(config, dict) else {}
        provider_config = memory_config.get("openviking", {}) if isinstance(memory_config, dict) else {}
        return dict(provider_config) if isinstance(provider_config, dict) else {}
    except Exception:
        return {}


def _profile_openviking_env(hermes_home: Optional[str]) -> Optional[dict]:
    """Read this provider's home, including Hermes-managed external secret sources."""
    if not hermes_home:
        return None
    try:
        from .host import (
            build_profile_secret_scope,
            hydrate_profile_secret_sources,
            is_multiplex_active,
        )

        hydrate_profile_secret_sources(hermes_home)
        env = build_profile_secret_scope(Path(hermes_home))
        # A routed provider must never inherit launch process credentials.
        if _get_launch_hermes_home().resolve() == Path(hermes_home).resolve():
            if is_multiplex_active():
                try:
                    from .host import launch_profile_policy
                except ImportError:  # older Hermes without launch-profile hosting
                    pass
                else:
                    # launch_secret_scope captures live os.environ when no snapshot
                    # exists. The messaging gateway has no snapshot, so only use
                    # the frozen one created by multi-profile host activation.
                    if getattr(launch_profile_policy, "_snapshot", None) is not None:
                        env = launch_profile_policy.launch_secret_scope(hermes_home)
            else:
                for key, value in os.environ.items():
                    if key.startswith("OPENVIKING_"):
                        env.setdefault(key, value)
        return env
    except Exception as exc:
        logger.warning("OpenViking could not load profile secrets for %s (%s).", hermes_home, type(exc).__name__)
        return {}  # A failed profile read must not borrow another profile's credentials.


def _resolve_connection_settings(provider_config: Optional[dict] = None, *, env: Optional[dict] = None) -> dict:
    """Layering: env -> linked ovcli profile -> config.yaml -> built-in default.
    An env account/user (even empty) is authoritative; the secret api_key never
    comes from config.yaml. Every env read goes through the profile secret scope:
    under multiplexing ``os.environ`` is the DEFAULT profile's .env, and a raw read
    would spend its key and tenant on behalf of a secondary profile."""
    provider_config = dict(provider_config or {})
    ovcli_values = _ovcli_values_for(provider_config, env=env)

    def layered(key: str, default: str = "", *, env_authoritative: bool = False) -> str:
        value = get_secret(f"OPENVIKING_{key.upper()}") if env is None else env.get(f"OPENVIKING_{key.upper()}")
        if value is not None:
            value = value.strip()
            if env_authoritative:
                return value
        return value or ovcli_values.get(key) or _clean_config_value(provider_config.get(key)) or default

    api_key_env = get_secret("OPENVIKING_API_KEY") if env is None else env.get("OPENVIKING_API_KEY")
    api_key = api_key_env.strip() if api_key_env is not None else ovcli_values.get("api_key", "")
    account = layered("account", env_authoritative=True)
    user = layered("user", env_authoritative=True)
    account, user = account or "default", user or "default"
    return {
        "endpoint": _normalize_openviking_url(layered("endpoint", _DEFAULT_ENDPOINT)),
        "api_key": api_key,
        "account": account,
        "user": user,
        "agent": layered("agent", _DEFAULT_AGENT),
    }


def _emit_runtime(message: str, callback=None, *, kind: str = "warning") -> None:
    """Log (warning/info by ``kind``) and forward to the CLI callback when one is wired."""
    (logger.warning if kind == "warning" else logger.info)("%s", message)
    if callback:
        try:
            callback(message)
        except Exception:
            logger.debug("OpenViking runtime %s callback failed", kind, exc_info=True)


def _runtime_openviking_timeout_message(endpoint: str) -> str:
    return (
        f"Local OpenViking server at {endpoint} is not reachable. Tried to start openviking-server, but it did not "
        f"become reachable within {_LOCAL_OPENVIKING_AUTOSTART_TIMEOUT:.0f} seconds. {_RETRY_LATER}"
    )


@dataclass(frozen=True)
class ConnectionSnapshot:
    """One resolved connection: endpoint URL, API key and identity.

    ``agent`` is also the actor peer (``X-OpenViking-Actor-Peer``). The two
    fingerprints reproduce, byte for byte, the pending-marker ``connection_key``
    of ``_capture_commit_scope`` and the mirror registry ``connection`` of
    ``mirror._connection_fingerprint``. Neither stores the key itself.
    """

    url: Any
    key: Any
    account: Any
    user: Any
    agent: Any

    @property
    def peer(self) -> Any:
        return self.agent

    @classmethod
    def from_client(cls, client: Any) -> "ConnectionSnapshot":
        return cls(*(getattr(client, name, None)
                     for name in ("_endpoint", "_api_key", "_account", "_user", "_agent")))

    def as_tuple(self) -> tuple:
        return (self.url, self.key, self.account, self.user, self.agent)

    def commit_key(self) -> str:
        return hashlib.sha256(json.dumps(self.as_tuple()).encode()).hexdigest()

    def mirror_connection(self) -> str:
        identity = [str(self.url or "").rstrip("/"), *(str(value or "") for value in self.as_tuple()[1:])]
        encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass
class _CommitScope:
    """One connection generation. Workers retain it across a config reload."""

    client: Any
    connection_key: str
    marker_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    committed: Set[str] = field(default_factory=set)
    pending: Set[str] = field(default_factory=set)
    finalizing: Set[str] = field(default_factory=set)


class ConnectionMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    def _start_runtime_openviking_waiter(self, *, endpoint: str, status_callback=None, warning_callback=None) -> None:
        # Caller holds _runtime_start_lock and reserved ownership via _runtime_start_pending.
        if self._runtime_start_thread and self._runtime_start_thread.is_alive():
            return
        self._runtime_start_thread = spawn_context_thread(
            lambda: self._finish_runtime_openviking_start(endpoint=endpoint, status_callback=status_callback, warning_callback=warning_callback),
            name="openviking-runtime-start")
        self._runtime_start_thread.start()

    def _settings_tuple(self, endpoint: Optional[str] = None) -> tuple:
        return (endpoint or self._endpoint, self._api_key, self._account, self._user, self._agent)

    def _build_client(self, endpoint: Optional[str] = None) -> _VikingClient:
        endpoint, api_key, account, user, agent = self._settings_tuple(endpoint)
        return _rest_client(self._deps, endpoint, api_key, account=account, user=user, agent=agent)

    def _publish_client(self, client: _VikingClient, endpoint: str) -> None:
        with self._session_state_lock:
            self._client = client
            self._conn_snapshot = self._settings_tuple(endpoint)
            self._failed_refresh = None

    def _capture_commit_scope(self) -> _CommitScope:
        with self._session_state_lock:
            if self._commit_scope is None or self._commit_scope.client is not self._client:
                snapshot = getattr(self._client, "_conn_snapshot", None)
                if not isinstance(snapshot, tuple):
                    snapshot = self._conn_snapshot or self._settings_tuple()
                # Recovery may use only the matching endpoint and identity. Do
                # not persist credentials, or use the same marker after A -> B -> A.
                key = hashlib.sha256(json.dumps(snapshot).encode()).hexdigest()
                if self._commit_scope is not None:
                    self._turn_count = 0
                self._commit_scope = _CommitScope(self._client, key)
            return self._commit_scope

    def _finish_runtime_openviking_start(self, *, endpoint: Optional[str] = None, status_callback=None, warning_callback=None) -> None:
        endpoint = endpoint or self._endpoint

        def stale() -> bool:
            return self._shutting_down or self._endpoint != endpoint

        if not _wait_for_openviking_health(endpoint, timeout_seconds=_LOCAL_OPENVIKING_AUTOSTART_TIMEOUT, should_stop=stale,
                                           deps=self._deps):
            if not stale():
                _emit_runtime(_runtime_openviking_timeout_message(endpoint), warning_callback)
            return

        with self._client_refresh_lock:
            if stale():
                return
            try:
                client = self._build_client(endpoint)
                healthy = client.health()
                if stale():
                    return
                warning_message = "" if healthy else f"OpenViking server at {endpoint} is still not reachable after auto-start. {_RETRY_LATER}"
                if healthy:
                    self._publish_client(client, endpoint)
            except ImportError:
                logger.warning(_HTTPX_MISSING)
                return
            except Exception as e:
                warning_message = f"OpenViking server at {endpoint} could not be attached after auto-start: {e}. {_RETRY_LATER}"

        if warning_message:
            _emit_runtime(warning_message, warning_callback)
            return
        # Attached: recover orphaned sessions outside the refresh lock (network I/O), then announce.
        self._recover_pending_sessions()
        _emit_runtime(f"Local OpenViking server at {endpoint} is reachable; OpenViking memory is active for later turns.", status_callback, kind="status")

    def _handle_runtime_openviking_unreachable(self, *, status_callback=None, warning_callback=None) -> None:
        endpoint = self._endpoint
        self._client = None
        if not _is_local_openviking_url(endpoint):
            _emit_runtime(f"Remote OpenViking server at {endpoint} is not reachable. {_RETRY_LATER} "
                          "Check the configured endpoint and network connectivity.", warning_callback)
            return

        with self._runtime_start_lock:
            if self._shutting_down or self._runtime_start_pending or (self._runtime_start_thread and self._runtime_start_thread.is_alive()):
                return
            self._runtime_start_pending = True
            start_state, start_message = _start_local_openviking_server(endpoint)
            if start_state != _LOCAL_SERVER_STARTED:
                self._runtime_start_pending = False

        if start_state != _LOCAL_SERVER_STARTED:
            _emit_runtime(f"Local OpenViking server at {endpoint} is not reachable. {start_message} {_RETRY_LATER}", warning_callback)
            return
        _emit_runtime(f"{start_message} OpenViking memory is starting in the background and will attach when ready.", status_callback, kind="status")
        with self._runtime_start_lock:
            self._runtime_start_pending = False
            if not self._shutting_down:
                self._start_runtime_openviking_waiter(endpoint=endpoint, status_callback=status_callback, warning_callback=warning_callback)

    def _ensure_client(self) -> Optional["_VikingClient"]:
        """Active client, rebuilt if the resolved config changed.

        ``/reload`` only refreshes ``os.environ``; the provider instance is not
        re-initialized, so re-resolve settings on every access and rebuild +
        health-check only when a value changed (hot path: one tuple compare).

        ``/reload`` only refreshes ``os.environ`` — the existing provider instance is not re-initialized —
        so OPENVIKING_* values added to ``~/.hermes/.env`` after startup never reach the live client and
        tools keep running against stale auth until the user restarts hermes (#21130).
        """
        if not self._env_refresh_enabled:
            return self._client  # no baseline yet: keep whatever the caller wired up
        with self._client_refresh_lock:
            return self._ensure_client_locked()

    def _profile_config_and_env(self) -> tuple[dict, Optional[dict]]:
        home = self._hermes_home if self._hermes_home_bound else None
        env = _profile_openviking_env(home)
        return _load_hermes_openviking_config(home, env=env), env

    def _resolve_bound_connection_settings(self) -> dict:
        config, env = self._profile_config_and_env()
        return _resolve_connection_settings(config, env=env)

    def _in_cooldown(self, failed_key) -> bool:
        failed = self._failed_refresh
        return failed is not None and failed[0] == failed_key and self._deps.monotonic() - failed[1] < _FAILED_CONFIG_RETRY_COOLDOWN_SECONDS

    def _ensure_client_locked(self) -> Optional["_VikingClient"]:
        """Resolve and publish one client/config state under the refresh lock."""
        if self._shutting_down:
            self._client = None
            return None
        try:
            settings = self._resolve_bound_connection_settings()
        except _OpenVikingEndpointError as exc:
            failed_key = ("invalid-endpoint", str(exc))
            if not self._in_cooldown(failed_key):
                logger.warning("%s %s", exc, _FIX_ENDPOINT)
            self._failed_refresh = (failed_key, self._deps.monotonic())
            self._client = None
            return None
        settings_key = tuple(settings[k] for k in _CONNECTION_KEYS)
        if settings_key == self._settings_tuple():
            if self._client is not None:
                return self._client
            with self._runtime_start_lock:
                if self._runtime_start_pending or (self._runtime_start_thread and self._runtime_start_thread.is_alive()):
                    return self._client
            # Last attempt at this exact config failed: skip the 3s probe until the
            # cooldown elapses or the resolved config changes.
            if self._in_cooldown(settings_key):
                return None

        self._endpoint, self._api_key, self._account, self._user, self._agent = settings_key
        try:
            client = self._build_client()
        except ImportError:
            logger.warning(_HTTPX_MISSING)
            self._client = None
            return None

        health_state, health_message = self._deps.health(client, settings_key[0])
        if health_state == "healthy":
            self._publish_client(client, settings_key[0])
            return self._client
        self._failed_refresh = (settings_key, self._deps.monotonic())
        if health_state == "responded":
            logger.warning(
                "%s OpenViking memory is temporarily unavailable; Hermes will retry on a later access (after cooldown) or when the config changes.",
                health_message,
            )
        else:
            self._handle_runtime_openviking_unreachable()
        self._client = None
        return None

    def _new_client(self) -> _VikingClient:
        """Clone the published identity, including defaults resolved by the client.

        Re-reading empty account/user defaults from the environment could borrow
        the next connection's identity before that connection is published.
        """
        snapshot = getattr(self._client, "_conn_snapshot", None)
        if not isinstance(snapshot, tuple):
            snapshot = self._conn_snapshot or self._settings_tuple()
        endpoint, api_key, account, user, agent = snapshot
        return _rest_client(self._deps, endpoint, api_key, account=account, user=user, agent=agent)

    def _user_space(self, client=None, *, timeout: Optional[float] = None, require_confirmed: bool = False) -> str:
        """Resolve the user space, caching only a confirmed connection identity.

        Use the client's snapshot even when a reload has replaced the active connection.
        Clients with the same resolved settings share the cache; unbound clients do not.
        """
        active = client if client is not None else getattr(self, "_client", None)
        snapshot = getattr(active, "_conn_snapshot", None)
        cached = getattr(self, "_user_space_cache", None)
        if snapshot is not None and cached is not None and cached[0] == snapshot:
            return cached[1]
        if active is not None and (resolved := _resolve_user_space(active, timeout=timeout)):
            if snapshot is not None:
                self._user_space_cache = (snapshot, resolved)
            return resolved
        if require_confirmed:
            raise RuntimeError(
                "OpenViking server did not confirm the current user identity; "
                "leaving OpenViking unchanged"
            )
        return str(getattr(active, "_user", "") or getattr(self, "_user", "") or "default").strip() or "default"
