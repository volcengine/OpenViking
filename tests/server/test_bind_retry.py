# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Port-binding recovery for the OpenViking HTTP server.

``bootstrap._acquire_listen_sockets`` binds the listen port up-front with
retry + exponential backoff and the bound sockets are handed to uvicorn, so
a stale or concurrently-restarting process holding the port no longer
crashes the server on the first EADDRINUSE.
"""

from __future__ import annotations

import errno
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import openviking.server.bootstrap as bootstrap
from openviking.server.config import ServerConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port(host: str = "127.0.0.1") -> int:
    """Reserve then release a port so tests never collide on a fixed one."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family=family) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _occupy(port: int, host: str = "127.0.0.1") -> socket.socket:
    sock = socket.socket(family=socket.AF_INET)
    sock.bind((host, port))
    sock.listen(1)
    return sock


def _patch_sleep(monkeypatch, delays: list[float]) -> None:
    """Record sleep calls without actually sleeping (scoped to bootstrap)."""
    monkeypatch.setattr(bootstrap, "time", SimpleNamespace(sleep=delays.append))


def _main_battery(monkeypatch, config: ServerConfig) -> dict:
    """Monkeypatch everything bootstrap.main() needs except bind acquisition."""
    captured: dict = {}

    monkeypatch.setattr(bootstrap, "load_server_config", lambda config_path: config)
    monkeypatch.setattr(bootstrap, "create_app", lambda config, **kwargs: "app")
    monkeypatch.setattr(bootstrap, "configure_uvicorn_logging", lambda: None)
    monkeypatch.setattr(
        bootstrap.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            host=None,
            port=None,
            config=None,
            workers=None,
            bot=False,
            with_bot=False,
            bot_port=bootstrap.VIKINGBOT_DEFAULT_PORT,
            bot_url="http://localhost:18790",
            enable_bot_logging=None,
            bot_log_dir="/tmp/bot-logs",
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config",
        lambda config: (False, "127.0.0.1", 11434),
    )

    def fake_config(app, **kwargs):
        captured.update({"config_kwargs": {"app": app, **kwargs}})
        return SimpleNamespace(load_app=lambda: None)

    monkeypatch.setattr(bootstrap.uvicorn, "Config", fake_config)

    def fake_server_run(self, sockets=None):
        # Capture the bound address NOW: main() closes the socket in its
        # finally block after serve() returns, and getsockname() on a closed
        # socket raises.
        captured.update(
            {
                "sockets": sockets,
                "bound_port": sockets[0].getsockname()[1] if sockets else None,
            }
        )

    monkeypatch.setattr(bootstrap.uvicorn.Server, "run", fake_server_run)
    return captured


# ---------------------------------------------------------------------------
# _acquire_listen_socket
# ---------------------------------------------------------------------------


def test_acquire_returns_bound_socket_when_port_free():
    port = _free_port()

    socks = bootstrap._acquire_listen_sockets("127.0.0.1", port, 0, 0.01, 2.0, "test")

    assert socks is not None
    assert len(socks) == 1  # explicit host binds exactly one family
    assert socks[0].getsockname()[1] == port
    socks[0].close()


def test_acquire_retries_until_port_released():
    port = _free_port()
    blocker = _occupy(port)

    def release_soon():
        time.sleep(0.05)
        blocker.close()

    threading.Thread(target=release_soon, daemon=True).start()

    # Real (tiny) delays: cumulative 0.01+0.02+0.04+... comfortably covers
    # the 0.05s hold while staying fast when the port frees early.
    socks = bootstrap._acquire_listen_sockets("127.0.0.1", port, 8, 0.01, 2.0, "test")

    assert socks is not None
    assert socks[0].getsockname()[1] == port
    socks[0].close()


def test_acquire_returns_none_after_exhausting_attempts(monkeypatch, capsys):
    port = _free_port()
    blocker = _occupy(port)
    delays: list[float] = []
    _patch_sleep(monkeypatch, delays)

    socks = bootstrap._acquire_listen_sockets("127.0.0.1", port, 2, 0.01, 2.0, "test")

    assert socks is None
    # 3 total tries -> 2 sleeps between them, exponential backoff.
    assert delays == [0.01, 0.02]
    err = capsys.readouterr().err
    assert "still in use" in err
    assert "attempt 1/3" in err
    blocker.close()


def test_acquire_zero_attempts_is_fail_fast(monkeypatch):
    port = _free_port()
    blocker = _occupy(port)
    delays: list[float] = []
    _patch_sleep(monkeypatch, delays)

    socks = bootstrap._acquire_listen_sockets("127.0.0.1", port, 0, 0.01, 2.0, "test")

    assert socks is None
    assert delays == []
    blocker.close()


def test_acquire_backoff_sequence(monkeypatch):
    port = _free_port()
    blocker = _occupy(port)
    delays: list[float] = []
    _patch_sleep(monkeypatch, delays)

    socks = bootstrap._acquire_listen_sockets("127.0.0.1", port, 3, 0.5, 2.0, "test")

    assert socks is None
    assert delays == [0.5, 1.0, 2.0]
    blocker.close()


def test_acquire_wildcard_host_binds_both_families():
    port = _free_port()

    socks = bootstrap._acquire_listen_sockets(None, port, 0, 0.01, 2.0, "test")

    assert socks is not None
    addrs = {sock.getsockname()[0] for sock in socks}
    # Wildcard keeps asyncio AI_PASSIVE parity: v4 always, v6 when the
    # machine has an IPv6 stack.
    assert "0.0.0.0" in addrs
    assert addrs <= {"0.0.0.0", "::"}
    for sock in socks:
        sock.close()


def test_acquire_wildcard_degrades_to_ipv4_when_ipv6_unavailable(monkeypatch):
    port = _free_port()
    real_socket = bootstrap.socket.socket

    def fake_socket(family=socket.AF_INET, type=socket.SOCK_STREAM, *args, **kwargs):
        if family == socket.AF_INET6:
            raise OSError(errno.EAFNOSUPPORT, "address family not supported")
        return real_socket(family=family, type=type)

    monkeypatch.setattr(bootstrap.socket, "socket", fake_socket)

    socks = bootstrap._acquire_listen_sockets(None, port, 0, 0.01, 2.0, "test")

    assert socks is not None
    assert [sock.getsockname()[0] for sock in socks] == ["0.0.0.0"]
    socks[0].close()


def test_acquire_propagates_config_errors_without_retry(monkeypatch):
    delays: list[float] = []
    _patch_sleep(monkeypatch, delays)

    class _UnbindableSocket(socket.socket):
        def bind(self, addr):
            raise OSError(errno.EADDRNOTAVAIL, "address not available")

    monkeypatch.setattr(bootstrap.socket, "socket", _UnbindableSocket)

    # EADDRNOTAVAIL means the host is not on this machine — no retry
    # budget can clear it, so it must propagate immediately.
    with pytest.raises(OSError) as excinfo:
        bootstrap._acquire_listen_sockets("198.51.100.1", 1933, 5, 0.01, 2.0, "test")

    assert excinfo.value.errno == errno.EADDRNOTAVAIL
    assert delays == []


def test_acquire_so_reuseaddr_matches_platform_semantics():
    port = _free_port()

    socks = bootstrap._acquire_listen_sockets("127.0.0.1", port, 0, 0.01, 2.0, "test")

    assert socks is not None
    enabled = bool(socks[0].getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR))
    # POSIX keeps TIME_WAIT-safe restarts; Windows must detect live listeners.
    assert enabled == (sys.platform != "win32")
    socks[0].close()


# ---------------------------------------------------------------------------
# ServerConfig wiring
# ---------------------------------------------------------------------------


def test_server_config_bind_retry_defaults():
    config = ServerConfig()

    assert config.bind_retry_max_attempts == 5
    assert config.bind_retry_initial_delay_seconds == 1.0
    assert config.bind_retry_backoff_factor == 2.0


def test_server_config_bind_retry_validation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ServerConfig(bind_retry_max_attempts=-1)
    with pytest.raises(ValidationError):
        ServerConfig(bind_retry_initial_delay_seconds=0)
    with pytest.raises(ValidationError):
        ServerConfig(bind_retry_backoff_factor=0.5)


# ---------------------------------------------------------------------------
# bootstrap.main() integration
# ---------------------------------------------------------------------------


def test_main_exits_with_clear_message_when_port_stays_occupied(monkeypatch, capsys):
    port = _free_port()
    blocker = _occupy(port)
    config = ServerConfig(host="127.0.0.1", port=port, bind_retry_max_attempts=0)
    _main_battery(monkeypatch, config)

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main()

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert f"port {port} is still in use after 1 bind attempt(s)" in err
    assert "bind_retry_max_attempts" in err
    blocker.close()


def test_main_stops_vikingbot_when_port_stays_occupied(monkeypatch):
    port = _free_port()
    blocker = _occupy(port)
    config = ServerConfig(
        host="127.0.0.1", port=port, bind_retry_max_attempts=0, with_bot=True
    )
    _main_battery(monkeypatch, config)
    # Neutralize environment-dependent pre-flight checks so the test is
    # about the abort path alone.
    monkeypatch.setattr(bootstrap, "_abort_if_port_in_use", lambda port, label: None)
    monkeypatch.setattr(
        bootstrap, "get_server_url_from_server_data", lambda config: None
    )
    fake_bot = SimpleNamespace()
    monkeypatch.setattr(
        bootstrap, "_start_vikingbot_gateway", lambda *args, **kwargs: fake_bot
    )
    stopped: list[object] = []
    monkeypatch.setattr(
        bootstrap, "_stop_vikingbot_gateway", lambda proc: stopped.append(proc)
    )

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main()

    assert excinfo.value.code == 1
    # The gateway child must not outlive the failed server: an orphaned
    # bot keeps its port and blocks every later --with-bot restart.
    assert stopped == [fake_bot]
    blocker.close()


def test_main_stops_vikingbot_when_acquire_raises(monkeypatch):
    # Non-retryable bind errors (e.g. EADDRNOTAVAIL for a host that no
    # longer exists on this machine) propagate out of main() — the finally
    # must still stop the gateway child, or it holds port 18790 and blocks
    # every later --with-bot restart.
    config = ServerConfig(host="198.51.100.1", port=1933, with_bot=True)
    _main_battery(monkeypatch, config)
    monkeypatch.setattr(bootstrap, "_abort_if_port_in_use", lambda port, label: None)
    monkeypatch.setattr(
        bootstrap, "get_server_url_from_server_data", lambda config: None
    )
    fake_bot = SimpleNamespace()
    monkeypatch.setattr(
        bootstrap, "_start_vikingbot_gateway", lambda *args, **kwargs: fake_bot
    )
    stopped: list[object] = []
    monkeypatch.setattr(
        bootstrap, "_stop_vikingbot_gateway", lambda proc: stopped.append(proc)
    )

    def raising_acquire(*args, **kwargs):
        raise OSError(errno.EADDRNOTAVAIL, "address not available")

    monkeypatch.setattr(bootstrap, "_acquire_listen_sockets", raising_acquire)

    with pytest.raises(OSError) as excinfo:
        bootstrap.main()

    assert excinfo.value.errno == errno.EADDRNOTAVAIL
    assert stopped == [fake_bot]


def test_main_serves_on_prebound_socket_and_closes_it(monkeypatch):
    port = _free_port()
    config = ServerConfig(host="127.0.0.1", port=port, bind_retry_max_attempts=0)
    captured = _main_battery(monkeypatch, config)

    # _acquire_listen_sockets is NOT stubbed here: main() must really bind
    # the free port and hand the very same bound socket to uvicorn.
    bootstrap.main()

    served = captured["sockets"]
    assert served is not None and len(served) == 1
    # The socket uvicorn served on is the very one main() bound to our port.
    assert captured["bound_port"] == port
    assert captured["config_kwargs"]["host"] == "127.0.0.1"
    assert captured["config_kwargs"]["port"] == port
    # main()'s finally block defensively closes the socket.
    assert served[0].fileno() == -1


def test_main_multiworker_closes_probe_socket_and_delegates_to_uvicorn_run(
    monkeypatch,
):
    port = _free_port()
    config = ServerConfig(host="127.0.0.1", port=port, workers=2)
    closed: list[bool] = []

    class _ProbeSocket:
        def fileno(self) -> int:
            return -1  # already-closed marker so main()'s finally skips it

        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        bootstrap,
        "_acquire_listen_sockets",
        lambda host, port, **kwargs: [_ProbeSocket()],
    )
    captured: dict = {}
    monkeypatch.setattr(
        bootstrap.uvicorn,
        "run",
        lambda app, **kwargs: captured.update({"app": app, **kwargs}),
    )
    battery = _main_battery(monkeypatch, config)

    bootstrap.main()

    # Multi-worker path: uvicorn.run receives the import-string factory and
    # the pre-flight socket was closed before handing the bind to uvicorn.
    assert captured["app"] == "openviking.server.app:create_worker_app"
    assert captured["workers"] == 2
    assert captured["port"] == port
    assert closed == [True]
    # Single-process serving path must not have run.
    assert battery.get("sockets") is None
