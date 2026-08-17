# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bootstrap script for OpenViking HTTP Server."""

import argparse
import asyncio
import errno
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Optional

import uvicorn

from openviking.server.app import (
    WORKER_BOT_API_URL_ENV,
    WORKER_WITH_BOT_ENV,
    create_app,
)
from openviking.server.config import get_server_url_from_server_data, load_server_config
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_OV_CONF,
    DEFAULT_OVCLI_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
)
from openviking_cli.utils.logger import configure_uvicorn_logging


@dataclass
class BotProcess:
    process: subprocess.Popen
    log_file: Optional[object] = None


def _get_version() -> str:
    try:
        from openviking import __version__

        return __version__
    except ImportError:
        return "unknown"


VIKINGBOT_DEFAULT_HOST = "127.0.0.1"
VIKINGBOT_DEFAULT_PORT = 18790


def _abort_if_port_in_use(port: int, label: str) -> None:
    """Exit with a clear message if anything is already listening on ``port``.

    Without this, ``--with-bot`` would spawn a vikingbot subprocess that
    silently fails to bind while a stale process keeps serving traffic —
    the operator believes they upgraded but the old binary still answers.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            in_use = True
        except (ConnectionRefusedError, socket.timeout, OSError):
            in_use = False
    if in_use:
        print(
            f"Error: {label} port {port} is already in use.\n"
            f"  A previous process is still bound — refusing to start a duplicate.\n"
            f"  Identify it:  lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
            f"  Kill it, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)


def _normalize_host_arg(host: Optional[str]) -> Optional[str]:
    """Normalize special CLI host values."""
    if host is None:
        return None
    if host.strip().lower() == "all":
        return None
    return host


# Bind errors a retry can plausibly clear: the port is held by a process
# that may exit, or — on Windows — by a socket in another login session
# (WSAEACCES maps to EACCES). Anything else, e.g. EADDRNOTAVAIL for a host
# not configured on this machine, is a configuration error that no retry
# budget can fix, so it propagates immediately.
_RETRYABLE_BIND_ERRNOS = frozenset({errno.EADDRINUSE, errno.EACCES})

# Errors meaning "this machine has no usable IPv6 stack": the wildcard
# bind then degrades to IPv4-only instead of failing startup.
_IPV6_UNAVAILABLE_ERRNOS = frozenset({errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL})


def _new_listen_socket(family: int) -> socket.socket:
    """Create a listen socket with the platform-appropriate options."""
    sock = socket.socket(family=family, type=socket.SOCK_STREAM)
    if sys.platform != "win32":
        # POSIX: survive TIME_WAIT leftovers from prior runs; a live
        # listener still fails the bind, which drives the retry loop.
        # Windows: deliberately off — SO_REUSEADDR there allows binding
        # over a live listener (silent port hijack) instead of detecting it.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock


def _bind_explicit_listen_socket(host: str, port: int) -> socket.socket:
    """Bind one explicitly configured address (uvicorn ``bind_socket`` parity)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = _new_listen_socket(family)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def _bind_wildcard_listen_sockets(port: int) -> list[socket.socket]:
    """Bind both ``::`` and ``0.0.0.0`` — asyncio ``AI_PASSIVE`` parity.

    The pre-retry code path (``loop.create_server(host=None)``) resolved the
    wildcard with ``AI_PASSIVE`` and bound every returned family, so
    ``--host all`` accepted IPv6 connections; uvicorn's ``bind_socket``
    binds IPv4 only. Machines without an IPv6 stack degrade to IPv4-only
    rather than failing startup. A partial failure closes the sockets
    bound so far so the retry loop always starts from a clean slate.
    """
    socks: list[socket.socket] = []
    try:
        sock6 = _new_listen_socket(socket.AF_INET6)
    except OSError as exc:
        if exc.errno not in _IPV6_UNAVAILABLE_ERRNOS:
            raise
        sock6 = None
    if sock6 is not None:
        try:
            sock6.bind(("::", port))
        except OSError as exc:
            sock6.close()
            if exc.errno not in _IPV6_UNAVAILABLE_ERRNOS:
                raise
        else:
            socks.append(sock6)
    sock4 = _new_listen_socket(socket.AF_INET)
    try:
        sock4.bind(("0.0.0.0", port))
    except OSError:
        sock4.close()
        for sock in socks:
            sock.close()
        raise
    socks.append(sock4)
    return socks


def _acquire_listen_sockets(
    host: Optional[str],
    port: int,
    max_attempts: int,
    initial_delay: float,
    backoff_factor: float,
    label: str,
) -> Optional[list[socket.socket]]:
    """Bind ``(host, port)`` for listening, retrying while it is occupied.

    Returns the bound socket(s) on success, or ``None`` after
    ``max_attempts + 1`` failed tries. They are meant to be handed straight
    to ``uvicorn.Server.run(sockets=[...])`` so the bind is never released
    between acquisition and serving (no check-then-bind race window).
    An explicit host binds a single socket with uvicorn ``bind_socket``
    semantics (IPv6 family when the host contains ``:``); a wildcard host
    (``None``) binds both families as ``loop.create_server(host=None)``
    did. Only occupancy-class errors are retried; other bind errors (e.g.
    ``EADDRNOTAVAIL`` when the configured host does not exist on this
    machine) propagate to the caller immediately.
    """
    for attempt in range(max(0, max_attempts) + 1):
        try:
            if host:
                socks = [_bind_explicit_listen_socket(host, port)]
            else:
                socks = _bind_wildcard_listen_sockets(port)
        except OSError as exc:
            if exc.errno not in _RETRYABLE_BIND_ERRNOS:
                raise
            if attempt >= max_attempts:
                return None
            delay = initial_delay * (backoff_factor**attempt)
            print(
                f"{label}: port {port} still in use "
                f"(attempt {attempt + 1}/{max_attempts + 1}), "
                f"retrying in {delay:.1f}s...",
                file=sys.stderr,
            )
            time.sleep(delay)
        else:
            if attempt > 0:
                print(f"{label}: port {port} acquired after {attempt + 1} attempt(s)")
            return socks
    return None  # pragma: no cover - loop always returns or exhausts


def _abort_port_acquisition_failed(port: int, max_attempts: int) -> NoReturn:
    """Exit with a clear message when the listen port cannot be acquired."""
    print(
        f"Error: OpenViking server port {port} is still in use after "
        f"{max_attempts + 1} bind attempt(s).\n"
        f"  A stale or duplicate process is likely holding it.\n"
        f"  Identify it:  lsof -nP -iTCP:{port} -sTCP:LISTEN   (Linux/macOS)\n"
        f"                netstat -ano | findstr :{port}        (Windows)\n"
        f"  On Windows, an empty result can mean the port sits in an OS\n"
        f"  excluded range (bind denied, nothing listening):\n"
        f"                netsh interface ipv4 show excludedportrange protocol=tcp\n"
        f"  Kill the holder or move the port, or raise\n"
        f"  server.bind_retry_max_attempts in ov.conf.",
        file=sys.stderr,
    )
    sys.exit(1)


def _serve_single_process(app, config, listen_socks: list[socket.socket]) -> None:
    """Serve ``app`` on the pre-bound ``listen_socks`` (no re-bind race)."""
    server_config = uvicorn.Config(
        app,
        host=config.host,
        port=config.port,
        timeout_keep_alive=config.timeout_keep_alive,
        log_config=None,
    )
    # uvicorn's Server.run() -> Config.load() loads the app itself.
    uvicorn.Server(server_config).run(sockets=listen_socks)


def _resolve_default_bot_log_dir(config_path: Optional[str]) -> str:
    """Resolve default bot log directory from current ov.conf storage.workspace."""
    default_storage = DEFAULT_CONFIG_DIR / "data"
    default_log_dir = default_storage / "bot" / "logs"

    resolved_path = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if resolved_path is None:
        return str(default_log_dir)

    try:
        with open(resolved_path, "r", encoding="utf-8-sig") as f:
            raw = os.path.expandvars(f.read())
        data = json.loads(raw)
        storage = data.get("storage", {})
        workspace = storage.get("workspace") if isinstance(storage, dict) else None
        if not workspace:
            return str(default_log_dir)
        return str(Path(workspace).expanduser().resolve() / "bot" / "logs")
    except Exception:
        return str(default_log_dir)


def _resolve_cli_config_for_bot(config_path: Optional[str]) -> Optional[str]:
    """Resolve which ovcli.conf the vikingbot child process should use."""
    explicit_cli_config = os.environ.get(OPENVIKING_CLI_CONFIG_ENV)
    if explicit_cli_config:
        return explicit_cli_config

    resolved_ov_conf = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if resolved_ov_conf is not None:
        colocated_cli_config = Path(resolved_ov_conf).resolve().parent / DEFAULT_OVCLI_CONF
        if colocated_cli_config.exists():
            return str(colocated_cli_config)

    default_cli_config = DEFAULT_CONFIG_DIR / DEFAULT_OVCLI_CONF
    if default_cli_config.exists():
        return str(default_cli_config)

    return None


def main():
    """Main entry point for openviking-server command."""
    parser = argparse.ArgumentParser(
        description="OpenViking HTTP Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"openviking-server {_get_version()}",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to bind to",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind to",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to ov.conf config file",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of uvicorn worker processes (default: 1, or server.workers in ov.conf)",
    )
    parser.add_argument(
        "--bot",
        "--with-bot",
        action="store_true",
        dest="with_bot",
        help="Enable Bot API proxy to Vikingbot (requires Vikingbot running)",
    )
    parser.add_argument(
        "--bot-port",
        type=int,
        default=VIKINGBOT_DEFAULT_PORT,
        dest="bot_port",
        help=f"Vikingbot gateway port (default: {VIKINGBOT_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--enable-bot-logging",
        action="store_true",
        dest="enable_bot_logging",
        default=None,
        help="Enable logging vikingbot output to files (default: True when --with-bot is used)",
    )
    parser.add_argument(
        "--disable-bot-logging",
        action="store_false",
        dest="enable_bot_logging",
        help="Disable logging vikingbot output to files",
    )
    parser.add_argument(
        "--bot-log-dir",
        type=str,
        default=None,
        help="Directory to store vikingbot log files (default: {storage.workspace or ~/.openviking/data}/bot/logs)",
    )

    args = parser.parse_args()

    # Set OPENVIKING_CONFIG_FILE environment variable if --config is provided
    # This allows OpenVikingConfigSingleton to load from the specified config file
    if args.config is not None:
        os.environ[OPENVIKING_CONFIG_ENV] = args.config

    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    # Load server config from ov.conf
    try:
        resolved_config_path = resolve_config_path(
            args.config,
            OPENVIKING_CONFIG_ENV,
            DEFAULT_OV_CONF,
        )
        config = load_server_config(args.config)
        OpenVikingConfigSingleton.initialize(config_path=args.config)
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    # Configure logging early so that all subsequent steps have proper logging
    configure_uvicorn_logging()

    # 🔍 Authentication health check - CRITICAL: will exit if check fails
    try:
        from openviking.server.auth.health_check import run_startup_health_check_or_exit
        asyncio.run(run_startup_health_check_or_exit(config))
    except Exception as e:
        # Don't fail startup if health check itself has issues
        print(f"Warning: Authentication health check failed to run: {e}", file=sys.stderr)
        print("Continuing startup anyway...", file=sys.stderr)

    # Ensure Ollama is running if configured
    try:
        from openviking_cli.utils.ollama import detect_ollama_in_config, ensure_ollama_for_server

        ov_config = OpenVikingConfigSingleton.get_instance()
        uses_ollama, ollama_host, ollama_port = detect_ollama_in_config(ov_config)
        if uses_ollama:
            result = ensure_ollama_for_server(ollama_host, ollama_port)
            if result.success:
                print(f"Ollama is running at {ollama_host}:{ollama_port}")
            else:
                print(
                    f"Warning: Ollama not available at {ollama_host}:{ollama_port}. "
                    f"Embedding/VLM may fail. ({result.message})",
                    file=sys.stderr,
                )
                if result.stderr_output:
                    print(f"  Ollama stderr: {result.stderr_output}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Ollama pre-flight check failed: {e}", file=sys.stderr)

    # Override with command line arguments
    if args.host is not None:
        config.host = _normalize_host_arg(args.host)
    if args.port is not None:
        config.port = args.port
    if args.workers is not None:
        config.workers = args.workers
    if args.with_bot:
        config.with_bot = True

    bot_process: Optional[BotProcess] = None
    if config.with_bot:
        bot_port = args.bot_port
        config.bot_api_url = f"http://{VIKINGBOT_DEFAULT_HOST}:{bot_port}"
        _abort_if_port_in_use(bot_port, "vikingbot gateway")
        print(f"Bot API proxy enabled, forwarding to {config.bot_api_url}")
        # Determine if bot logging should be enabled
        enable_bot_logging = args.enable_bot_logging
        if enable_bot_logging is None:
            # Reaching this block means bot integration is enabled, either by
            # ``--with-bot`` or by ``server.with_bot`` in ov.conf.  Default
            # logging must not depend only on which activation surface was
            # used, otherwise config-enabled gateways silently lose their
            # child-process diagnostics.
            enable_bot_logging = True
        bot_log_dir = args.bot_log_dir or _resolve_default_bot_log_dir(args.config)
        # Start vikingbot gateway if --with-bot is set
        bot_process = _start_vikingbot_gateway(
            enable_bot_logging,
            bot_log_dir,
            bot_port,
            config_path=args.config,
            managed_server_url=get_server_url_from_server_data(config),
        )
        if bot_process is None:
            print(
                "Error: --with-bot was requested, but VikingBot could not be started.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Create and run server app
    app = create_app(
        config,
        config_path=(
            str(resolved_config_path) if resolved_config_path is not None else args.config
        ),
    )
    workers_info = f" (workers: {config.workers})" if config.workers > 1 else ""

    listen_socks: Optional[list[socket.socket]] = None
    try:
        # Port-binding recovery: acquire the listen socket(s) up-front with
        # retry + exponential backoff, then serve on them. Without this, a
        # stale or concurrently-restarting process holding the port makes
        # uvicorn exit(1) with EADDRINUSE on the first try (e.g. watchdog
        # restart races). Both failure modes here — the abort exit below and
        # a raised non-retryable bind error — pass through the finally
        # below, which owns stopping the gateway child.
        listen_socks = _acquire_listen_sockets(
            config.host,
            config.port,
            max_attempts=config.bind_retry_max_attempts,
            initial_delay=config.bind_retry_initial_delay_seconds,
            backoff_factor=config.bind_retry_backoff_factor,
            label="OpenViking HTTP server",
        )
        if listen_socks is None:
            _abort_port_acquisition_failed(config.port, config.bind_retry_max_attempts)

        print(
            f"OpenViking HTTP Server is running on {config.host}:{config.port}{workers_info}"
        )

        workers = config.workers
        if workers > 1:
            # Multi-worker mode requires an import string so each worker
            # can independently import the application.  We stash the
            # resolved config path in an env-var so that the factory can
            # pick it up (ServerConfig already reads OPENVIKING_CONFIG_FILE).
            # The multiprocess supervisor binds its own sockets; the acquire
            # loop above has already waited out any stale holder, so close
            # the probe sockets and hand the bind to uvicorn.
            for sock in listen_socks:
                sock.close()
            os.environ[WORKER_WITH_BOT_ENV] = "1" if config.with_bot else "0"
            os.environ[WORKER_BOT_API_URL_ENV] = config.bot_api_url
            uvicorn.run(
                "openviking.server.app:create_worker_app",
                factory=True,
                host=config.host,
                port=config.port,
                workers=workers,
                timeout_keep_alive=config.timeout_keep_alive,
                log_config=None,
            )
        else:
            _serve_single_process(app, config, listen_socks)
    finally:
        # uvicorn closes sockets it was given, but be defensive against
        # early exits. Double-close is a no-op for closed Python sockets.
        if listen_socks is not None:
            for sock in listen_socks:
                if sock.fileno() != -1:
                    sock.close()
        # Cleanup vikingbot process on shutdown
        if bot_process is not None:
            _stop_vikingbot_gateway(bot_process)


def _handle_vikingbot_failure(output: str, returncode: int) -> None:
    """Handle vikingbot startup failure and provide helpful error messages."""
    print(f"\nError: vikingbot gateway exited early (code {returncode})", file=sys.stderr)

    # Check for common dependency errors
    if "ModuleNotFoundError" in output:
        print("\nMissing dependencies detected!", file=sys.stderr)
        print(
            "\nTo use --with-bot, you need to install openviking with bot dependencies:",
            file=sys.stderr,
        )
        print('  pip install "openviking[bot]"', file=sys.stderr)
        print("  # Or for development:", file=sys.stderr)
        print('  uv pip install -e ".[bot,dev]"', file=sys.stderr)

    if output:
        print(f"\nDetailed error:\n{output}", file=sys.stderr)


def _start_vikingbot_gateway(
    enable_logging: bool,
    log_dir: str,
    port: int = VIKINGBOT_DEFAULT_PORT,
    config_path: Optional[str] = None,
    managed_server_url: Optional[str] = None,
) -> Optional[BotProcess]:
    """Start vikingbot gateway as a subprocess."""
    print("Starting vikingbot gateway...")

    # Check if vikingbot is available
    vikingbot_cmd = None
    if shutil.which("vikingbot"):
        vikingbot_cmd = ["vikingbot", "gateway"]
    else:
        # Try python -m vikingbot
        python_cmd = sys.executable
        try:
            result = subprocess.run(
                [python_cmd, "-m", "vikingbot", "--help"], capture_output=True, timeout=15
            )
            if result.returncode == 0:
                vikingbot_cmd = [python_cmd, "-m", "vikingbot", "gateway"]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if vikingbot_cmd is None:
        print("Warning: vikingbot not found. Please install vikingbot first.")
        print("  uv pip install -e '.[bot,dev]'")
        return None

    vikingbot_cmd.extend(["--host", VIKINGBOT_DEFAULT_HOST, "--port", str(port)])

    # Prepare logging
    log_file = None
    stdout_handler = subprocess.PIPE
    stderr_handler = subprocess.PIPE
    log_file_path = None

    if enable_logging:
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_filename = "vikingbot.log"
            log_file_path = os.path.join(log_dir, log_filename)
            log_file = open(log_file_path, "a")
            stdout_handler = log_file
            stderr_handler = log_file
            print(f"Vikingbot logs will be written to: {log_file_path}")
        except Exception as e:
            print(f"Warning: Failed to setup bot logging: {e}")
            if log_file:
                log_file.close()
                log_file = None
            stdout_handler = subprocess.PIPE
            stderr_handler = subprocess.PIPE

    # Start vikingbot gateway process
    try:
        # Set environment to ensure it uses the same Python environment
        env = os.environ.copy()
        cli_config_path = _resolve_cli_config_for_bot(config_path)
        if cli_config_path is not None:
            env[OPENVIKING_CLI_CONFIG_ENV] = cli_config_path
        env["VIKINGBOT_WITH_OPENVIKING_SERVER"] = "1"
        if managed_server_url:
            env["VIKINGBOT_MANAGED_OV_SERVER_URL"] = managed_server_url

        process = subprocess.Popen(
            vikingbot_cmd,
            stdout=stdout_handler,
            stderr=stderr_handler,
            text=True,
            env=env,
        )

        # Wait a moment to check if it started successfully
        time.sleep(2)
        if process.poll() is not None:
            # Process exited early
            if log_file:
                log_file.close()
                if log_file_path:
                    with open(log_file_path, "r") as f:
                        output = f.read()
                    _handle_vikingbot_failure(output, process.returncode)
            else:
                stdout, stderr = process.communicate(timeout=1)
                _handle_vikingbot_failure(stderr, process.returncode)
            sys.exit(1)

        print(f"Vikingbot gateway started (PID: {process.pid})")

        return BotProcess(process=process, log_file=log_file)

    except Exception as e:
        if log_file:
            log_file.close()
        print(f"Warning: Failed to start vikingbot gateway: {e}")
        return None


def _stop_vikingbot_gateway(bot_process: BotProcess) -> None:
    """Stop the vikingbot gateway subprocess."""
    if bot_process is None:
        return

    print(f"\nStopping vikingbot gateway (PID: {bot_process.process.pid})...")

    try:
        # Try graceful termination first
        bot_process.process.terminate()
        try:
            bot_process.process.wait(timeout=5)
            print("Vikingbot gateway stopped gracefully.")
        except subprocess.TimeoutExpired:
            # Force kill if it doesn't stop in time
            bot_process.process.kill()
            bot_process.process.wait()
            print("Vikingbot gateway force killed.")
    except Exception as e:
        print(f"Error stopping vikingbot gateway: {e}")
    finally:
        # Close the log file if it exists
        if bot_process.log_file is not None:
            try:
                bot_process.log_file.close()
            except Exception as e:
                print(f"Error closing bot log file: {e}")


if __name__ == "__main__":
    main()
