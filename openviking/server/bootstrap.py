# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bootstrap script for OpenViking HTTP Server."""

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Iterator, NoReturn, Optional

import uvicorn

from openviking.server.app import (
    WORKER_BOT_API_URL_ENV,
    WORKER_HOST_ENV,
    WORKER_PORT_ENV,
    WORKER_WITH_BOT_ENV,
    create_app,
)
from openviking.server.config import get_server_url_from_server_data, load_server_config
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.config_loader import expand_config_environment, resolve_config_path
from openviking_cli.utils.config.consts import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_OV_CONF,
    DEFAULT_OVCLI_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
)
from openviking_cli.utils.logger import (
    configure_server_logging,
    configure_uvicorn_logging,
    get_logger,
)

logger = get_logger(__name__)


@dataclass
class BotProcess:
    process: subprocess.Popen
    log_file: Optional[object] = None


@dataclass
class ExpiryPublisherProcess:
    process: subprocess.Popen
    stopping: threading.Event = field(default_factory=threading.Event)
    monitor_thread: Optional[threading.Thread] = None


def _raise_sigterm_as_system_exit(signum: int, _frame: FrameType | None) -> NoReturn:
    raise SystemExit(128 + signum)


@contextmanager
def _sigterm_runs_finally() -> Iterator[None]:
    """Make Uvicorn's replayed SIGTERM unwind through managed-process cleanup."""
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGTERM, _raise_sigterm_as_system_exit)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


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
        logger.critical("port unavailable component=%s port=%d", label, port)
        sys.exit(1)


def _normalize_host_arg(host: Optional[str]) -> Optional[str]:
    """Normalize special CLI host values."""
    if host is None:
        return None
    if host.strip().lower() == "all":
        return None
    return host


def _resolve_default_bot_log_dir(config_path: Optional[str]) -> str:
    """Resolve default bot log directory from current ov.conf storage.workspace."""
    default_storage = DEFAULT_CONFIG_DIR / "data"
    default_log_dir = default_storage / "bot" / "logs"

    resolved_path = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if resolved_path is None:
        return str(default_log_dir)

    try:
        with open(resolved_path, "r", encoding="utf-8-sig") as f:
            raw = expand_config_environment(f.read())
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


def _wait_for_expiry_publisher_ready(
    process: subprocess.Popen,
    status_path: Path,
    *,
    timeout_seconds: float = 60.0,
) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"Expiry publisher exited before becoming ready (code {returncode})")
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            status = {}
        if status.get("status") == "failed":
            raise RuntimeError(status.get("error") or "Expiry publisher initialization failed")
        if status.get("status") == "ready":
            return
        time.sleep(0.1)
    raise TimeoutError("Expiry publisher readiness timed out")


def _monitor_expiry_publisher(managed: ExpiryPublisherProcess) -> None:
    returncode = managed.process.wait()
    if managed.stopping.is_set():
        return
    logger.critical("expiry publisher exited; stopping server exit_code=%d", returncode)
    os.kill(os.getpid(), signal.SIGTERM)


def _start_expiry_publisher(config_path: Path) -> ExpiryPublisherProcess:
    startup_directory = tempfile.TemporaryDirectory(prefix="openviking-expiry-startup-")
    status_path = Path(startup_directory.name) / "status.json"
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "openviking_cli.expiry_bootstrap",
                "--config",
                str(config_path),
                "--startup-status",
                str(status_path),
            ],
            env=os.environ.copy(),
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            ),
        )
        _wait_for_expiry_publisher_ready(process, status_path)
        managed = ExpiryPublisherProcess(process)
        managed.monitor_thread = threading.Thread(
            target=_monitor_expiry_publisher,
            args=(managed,),
            name="openviking-expiry-publisher-monitor",
            daemon=True,
        )
        managed.monitor_thread.start()
        logger.info("expiry publisher started child_pid=%d", process.pid)
        return managed
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        startup_directory.cleanup()


def _stop_expiry_publisher(managed: ExpiryPublisherProcess) -> None:
    managed.stopping.set()
    process = managed.process
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if managed.monitor_thread is not None:
        managed.monitor_thread.join(timeout=1)


def main():
    """Run the CLI with one recovery attempt for remote restart startup failures."""
    from openviking.server.restart import RestartRecovery

    recovery = RestartRecovery()
    try:
        _main(recovery)
    except (Exception, SystemExit) as exc:
        if not isinstance(exc, SystemExit) or exc.code not in (None, 0):
            recovery.rollback()
        raise


def _main(recovery):
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
    resolved_config_path: Path | None = None
    ov_config = None
    try:
        resolved_config_path = resolve_config_path(
            args.config,
            OPENVIKING_CONFIG_ENV,
            DEFAULT_OV_CONF,
        )
        config = load_server_config(args.config)
        ov_config = OpenVikingConfigSingleton.initialize(config_path=args.config)
    except (FileNotFoundError, ValueError) as e:
        if isinstance(e, ValueError):
            logger.critical(
                "server configuration invalid error_type=%s; validate with openviking-server doctor",
                type(e).__name__,
            )
            logger.info("configuration reference: docs configuration guide")
        else:
            logger.critical("server configuration missing error_type=%s", type(e).__name__)
        sys.exit(1)

    # Configure logging early so that all subsequent steps have proper logging
    configure_server_logging()
    configure_uvicorn_logging()

    # 🔍 Authentication health check - CRITICAL: will exit if check fails
    try:
        from openviking.server.auth.health_check import run_startup_health_check_or_exit

        asyncio.run(run_startup_health_check_or_exit(config))
    except Exception as e:
        # Don't fail startup if health check itself has issues
        logger.info(
            "authentication health check unavailable; continuing error_type=%s degraded=true",
            type(e).__name__,
            extra={"degraded": True},
        )
        logger.debug("startup proceeding after health check fallback")

    # Ensure Ollama is running if configured
    try:
        from openviking_cli.utils.ollama import detect_ollama_in_config, ensure_ollama_for_server

        if ov_config is None:
            ov_config = OpenVikingConfigSingleton.get_instance()
        uses_ollama, ollama_host, ollama_port = detect_ollama_in_config(ov_config)
        if uses_ollama:
            result = ensure_ollama_for_server(ollama_host, ollama_port)
            if result.success:
                logger.info("ollama available")
            else:
                logger.info(
                    "ollama unavailable; model requests may fail degraded=true",
                    extra={"degraded": True},
                )
                if result.stderr_output:
                    logger.debug("ollama diagnostic output omitted")
    except Exception as e:
        logger.info(
            "ollama preflight unavailable; continuing error_type=%s degraded=true",
            type(e).__name__,
            extra={"degraded": True},
        )

    # Override with command line arguments
    if args.host is not None:
        config.host = _normalize_host_arg(args.host)
    if args.port is not None:
        config.port = args.port
    if args.workers is not None:
        config.workers = args.workers
    if args.with_bot:
        config.with_bot = True

    if recovery.pending and config.workers > 1:
        raise ValueError("Remote restart recovery requires a single worker")

    bot_process: Optional[BotProcess] = None
    if config.with_bot:
        import secrets

        # Shared only by this server and its managed child, never returned to Studio.
        os.environ["OPENVIKING_BOT_STUDIO_TOKEN"] = secrets.token_urlsafe(32)
        bot_port = args.bot_port
        config.bot_api_url = f"http://{VIKINGBOT_DEFAULT_HOST}:{bot_port}"
        _abort_if_port_in_use(bot_port, "vikingbot gateway")
        logger.info("bot api proxy enabled")
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
            logger.critical("bot dependencies unavailable; install openviking bot extras")
            sys.exit(1)

    expiry_publisher_process: Optional[ExpiryPublisherProcess] = None
    restart_requested = False
    try:
        with _sigterm_runs_finally():
            idle = getattr(getattr(ov_config, "memory", None), "session_auto_commit", None)
            compile_api = getattr(ov_config, "compile_api", None)
            expiry = getattr(ov_config, "expiry", None)
            uses_redis = (
                (idle is not None and idle.enabled and idle.idle_backend == "redis")
                or (compile_api is not None and compile_api.scheduler_enabled)
                or (config.ttl_adapter is not None and config.ttl_adapter.writer_enabled)
            )
            if uses_redis and expiry is not None and expiry.publisher_separate_process:
                if resolved_config_path is None:
                    raise RuntimeError(
                        "Cannot start expiry publisher without a resolved ov.conf path"
                    )
                expiry_publisher_process = _start_expiry_publisher(resolved_config_path)

            # Create and run server app only after managed dependencies are ready.
            app = create_app(
                config,
                server_overrides={
                    key: getattr(config, key)
                    for key in ("host", "port", "workers", "with_bot")
                    if getattr(args, key) is not None and (key != "with_bot" or args.with_bot)
                },
                config_path=(
                    str(resolved_config_path) if resolved_config_path is not None else args.config
                ),
            )
            logger.info("server started workers=%d", config.workers)

            workers = config.workers
            if workers > 1:
                # Multi-worker mode requires an import string so each worker
                # can independently import the application.  We stash the
                # resolved config path in an env-var so that the factory can
                # pick it up (ServerConfig already reads OPENVIKING_CONFIG_FILE).
                os.environ[WORKER_HOST_ENV] = config.host or ""
                os.environ[WORKER_PORT_ENV] = str(config.port)
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
                    access_log=False,
                )
            else:
                restart_requested = _run_restartable_server(
                    app,
                    recovery=recovery,
                    host=config.host,
                    port=config.port,
                    timeout_keep_alive=config.timeout_keep_alive,
                    log_config=None,
                    access_log=False,
                )
    finally:
        if expiry_publisher_process is not None:
            _stop_expiry_publisher(expiry_publisher_process)
        if bot_process is not None:
            _stop_vikingbot_gateway(bot_process)
    if restart_requested:
        # Preserve the interpreter, CLI arguments, cwd and environment. Replacing
        # this process also keeps its PID and relationship to systemd/Docker.
        if resolved_config_path is not None:
            os.environ[OPENVIKING_CONFIG_ENV] = str(resolved_config_path)
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])


def _run_restartable_server(app, recovery=None, **kwargs) -> bool:
    from openviking.server.restart import RestartController

    # RequestIdMiddleware owns the single request-completion record.
    kwargs.setdefault("access_log", False)
    server = uvicorn.Server(uvicorn.Config(app, **kwargs))
    controller = RestartController(lambda: setattr(server, "should_exit", True), recovery)
    app.state.restart_controller = controller
    if recovery is not None:
        startup = server.startup

        async def startup_with_recovery(*args, **kwargs):
            await startup(*args, **kwargs)
            if server.started:
                recovery.ready()

        server.startup = startup_with_recovery
    server.run()
    if recovery is not None and recovery.pending:
        raise RuntimeError("Server failed to complete startup")
    return controller.requested


def _handle_vikingbot_failure(output: str, returncode: int) -> None:
    """Handle vikingbot startup failure and provide helpful error messages."""
    logger.critical("bot exited during startup exit_code=%d", returncode)

    # Check for common dependency errors
    if "ModuleNotFoundError" in output:
        logger.critical("bot dependencies unavailable; install openviking bot extras")

    if output:
        logger.debug("bot diagnostic output omitted")


def _wait_for_bot_ready(process, status_path: Path) -> None:
    """A running child is not ready until its sandbox and HTTP server are ready."""
    started = time.monotonic()
    timeout = 900
    while time.monotonic() - started < timeout:
        if process.poll() is not None:
            raise RuntimeError(
                f"VikingBot exited before becoming ready (code {process.returncode})."
            )
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            status = {}
        # The per-launch status path identifies this gateway. Windows launchers
        # can have a different PID from the Python process reporting readiness.
        timeout = max(timeout, int(status.get("timeout", timeout)))
        if status.get("status") == "failed":
            raise RuntimeError(status.get("error") or "VikingBot initialization failed")
        if status.get("status") == "ready":
            return
        time.sleep(0.1)
    raise TimeoutError("VikingBot readiness timed out; check Docker/image pulls and the Bot log.")


def _start_vikingbot_gateway(
    enable_logging: bool,
    log_dir: str,
    port: int = VIKINGBOT_DEFAULT_PORT,
    config_path: Optional[str] = None,
    managed_server_url: Optional[str] = None,
) -> Optional[BotProcess]:
    """Start vikingbot gateway as a subprocess."""
    logger.info("bot starting")

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
        logger.critical("bot dependencies unavailable; install openviking bot extras")
        logger.debug("bot dependency installation guidance: install openviking bot extras")
        return None

    vikingbot_cmd.extend(["--host", VIKINGBOT_DEFAULT_HOST, "--port", str(port)])
    resolved_config = resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if resolved_config is not None:
        vikingbot_cmd.extend(["--config", str(resolved_config)])

    # Prepare logging
    log_file = None
    stdout_handler = None
    stderr_handler = None
    log_file_path = None

    if enable_logging:
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_filename = "vikingbot.log"
            log_file_path = os.path.join(log_dir, log_filename)
            log_file = open(log_file_path, "a")
            stdout_handler = log_file
            stderr_handler = log_file
            logger.info("bot output captured in separate log file")
        except Exception as e:
            logger.info(
                "bot file logging unavailable; using captured output error_type=%s degraded=true",
                type(e).__name__,
                extra={"degraded": True},
            )
            if log_file:
                log_file.close()
                log_file = None
            stdout_handler = None
            stderr_handler = None

    # Keep the handshake outside the sandbox workspace and unique to this child.
    startup_directory = tempfile.TemporaryDirectory(prefix="vikingbot-startup-")
    status_path = Path(startup_directory.name) / "status.json"
    process = None
    # Start vikingbot gateway process
    try:
        # Set environment to ensure it uses the same Python environment
        env = os.environ.copy()
        env["VIKINGBOT_STARTUP_STATUS"] = str(status_path)
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
            # Parent handles terminal signals and then shuts Gateway down once.
            # Avoid Ctrl+C cancelling child cleanup before its sandbox is released.
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

        _wait_for_bot_ready(process, status_path)

        logger.info("bot started child_pid=%d", process.pid)

        return BotProcess(process=process, log_file=log_file)

    except BaseException as e:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if log_file:
            log_file.close()
        logger.critical("bot startup failed error_type=%s", type(e).__name__)
        if log_file_path:
            logger.info("bot output captured in separate log file")
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    finally:
        startup_directory.cleanup()


def _stop_vikingbot_gateway(bot_process: BotProcess) -> None:
    """Stop the vikingbot gateway subprocess."""
    if bot_process is None:
        return

    logger.info("bot stopping child_pid=%d", bot_process.process.pid)

    try:
        # Try graceful termination first
        bot_process.process.terminate()
        try:
            bot_process.process.wait(timeout=30)
            logger.info("bot stopped")
        except subprocess.TimeoutExpired:
            # Force kill if it doesn't stop in time
            bot_process.process.kill()
            bot_process.process.wait()
            logger.info("bot shutdown timed out; process terminated")
    except Exception as e:
        logger.error("bot shutdown failed error_type=%s", type(e).__name__)
    finally:
        # Close the log file if it exists
        if bot_process.log_file is not None:
            try:
                bot_process.log_file.close()
            except Exception as e:
                logger.info("bot log close failed error_type=%s", type(e).__name__)


if __name__ == "__main__":
    main()
