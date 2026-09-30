"""Autostart of a local openviking-server and its port diagnostics."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from .endpoint import _is_local_openviking_url, _normalize_openviking_url
from .host import get_hermes_home
from .log import get_logger

if TYPE_CHECKING:
    from .deps import Deps


def default_deps():
    """The current default ``Deps``, looked up at call time: ``deps`` imports this module."""
    from .deps import default_deps as current

    return current()


logger = get_logger()


_LOCAL_OPENVIKING_AUTOSTART_TIMEOUT = 60.0
_LOCAL_OPENVIKING_PROBE_TIMEOUT = 2.0  # loopback connect budget; only guards against a wedged listener
_LOCAL_SERVER_STARTED = "started"
_LOCAL_SERVER_OCCUPIED = "occupied"
_LOCAL_SERVER_FAILED = "failed"
_OPENVIKING_SERVER_LOG_RELATIVE_PATH = Path("logs") / "openviking-server.log"


def _local_openviking_bind(endpoint: str) -> tuple[str, int]:
    parsed = urlparse(_normalize_openviking_url(endpoint))
    return parsed.hostname or "127.0.0.1", parsed.port or 1933


def _local_openviking_port_is_open(host: str, port: int) -> bool:
    """Pre-spawn guard: a successful connect proves a listener owns the port (so a
    second openviking-server would lose the data-dir lock); says nothing about health."""
    try:
        with socket.create_connection((host, port), timeout=_LOCAL_OPENVIKING_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _describe_local_port_listener(host: str, port: int) -> str:
    """Best-effort process identity for an occupied local TCP port."""
    try:
        import psutil

        accepted_hosts = {"0.0.0.0", "::", "::0", host.lower()}  # wildcard binds + the probed host
        if host.lower() == "localhost":
            accepted_hosts.update({"127.0.0.1", "::1"})
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            listener_host = str(conn.laddr.ip if hasattr(conn.laddr, "ip") else conn.laddr[0]).lower()
            listener_port = int(conn.laddr.port if hasattr(conn.laddr, "port") else conn.laddr[1])
            if listener_port != port or listener_host not in accepted_hosts:
                continue
            if conn.pid is None:
                break
            try:
                process_name = psutil.Process(conn.pid).name()
            except (psutil.Error, OSError):
                process_name = "unknown process"
            process_name = re.sub(r"[^\w .+-]", "?", str(process_name))[:80]
            return f"{process_name or 'unknown process'} (PID {conn.pid})"
    except Exception:
        logger.debug("Could not identify the process listening on %s:%s", host, port, exc_info=True)
    return "an unidentified process"


def _local_listener_suffix(endpoint: str) -> str:
    if not _is_local_openviking_url(endpoint):
        return ""
    host, port = _local_openviking_bind(endpoint)  # cannot raise: _is_local_openviking_url already normalized it
    if not _local_openviking_port_is_open(host, port):
        return ""
    return f" The listener on {host}:{port} is {_describe_local_port_listener(host, port)}."


def _start_local_openviking_server(endpoint: str) -> tuple[str, str]:
    try:
        host, port = _local_openviking_bind(endpoint)
    except ValueError as e:
        return _LOCAL_SERVER_FAILED, f"Could not parse local OpenViking URL: {e}"
    # A client-side health timeout can fire while the server is fine; spawning on
    # that alone yields a child that dies on DataDirectoryLocked every cooldown.
    # An occupied port only prevents spawning — it never proves the listener is OpenViking.
    if _local_openviking_port_is_open(host, port):
        return _LOCAL_SERVER_OCCUPIED, (
            f"Port {host}:{port} is occupied by {_describe_local_port_listener(host, port)}. Hermes did not start "
            "openviking-server because the listener has not passed OpenViking's /health check."
        )
    server_cmd = shutil.which("openviking-server")
    if not server_cmd:
        return _LOCAL_SERVER_FAILED, "openviking-server was not found on PATH. Start it manually, then retry."
    log_path = get_hermes_home() / _OPENVIKING_SERVER_LOG_RELATIVE_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Strip PYTHONPATH: the Desktop backend puts the Hermes venv on it, which
        # would shadow openviking-server's own site-packages (and on Windows lock
        # the Hermes venv's .pyd files, breaking `hermes update`).
        # Do not let the server child inherit this process's PYTHONPATH. If inherited, openviking-server
        # would import aiohttp and friends from the Hermes venv instead of its own (its venv's site-packages
        # are shadowed because PYTHONPATH precedes them) — and on Windows the loaded DLLs then lock the
        # Hermes venv, aborting `hermes update` with access-denied on .pyd files. (#78153)
        child_env = os.environ.copy()
        child_env.pop("PYTHONPATH", None)
        with log_path.open("ab") as log_file:
            subprocess.Popen([server_cmd, "--host", host, "--port", str(port)], stdout=log_file, stderr=log_file,
                             stdin=subprocess.DEVNULL, start_new_session=True, env=child_env)
    except Exception as e:
        return _LOCAL_SERVER_FAILED, f"Could not start openviking-server: {e}"
    return _LOCAL_SERVER_STARTED, f"Started openviking-server on {host}:{port} in the background. Logs: {log_path}"


def _wait_for_openviking_health(endpoint: str, *, timeout_seconds: float = 15.0, should_stop=None,
                                deps: Optional["Deps"] = None) -> bool:
    deps = deps if deps is not None else default_deps()
    deadline = deps.monotonic() + timeout_seconds
    while deps.monotonic() < deadline:
        # Bail promptly on teardown so the daemon waiter can be join()ed at shutdown
        # (a worker alive at interpreter exit aborts CPython in Py_FinalizeEx).
        if should_stop is not None and should_stop():
            return False
        if deps.validate_reachability(endpoint)[0]:
            return True
        deps.sleep(0.5)
    return False
