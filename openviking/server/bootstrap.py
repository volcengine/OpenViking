# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bootstrap script for OpenViking HTTP Server."""

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

import uvicorn

from openviking.server.app import create_app
from openviking.server.config import load_server_config
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
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


def _normalize_host_arg(host: Optional[str]) -> Optional[str]:
    """Normalize special CLI host values."""
    if host is None:
        return None
    if host.strip().lower() == "all":
        return None
    return host


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
        action="store_true",
        help="Also start vikingbot gateway after server starts",
    )
    parser.add_argument(
        "--with-bot",
        action="store_true",
        dest="with_bot",
        help="Enable Bot API proxy to Vikingbot (requires Vikingbot running)",
    )
    parser.add_argument(
        "--bot-url",
        default="http://localhost:18790",
        dest="bot_url",
        help="Vikingbot OpenAPIChannel URL (default: http://localhost:18790)",
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
        default=os.path.expanduser("~/.openviking/data/bot/logs"),
        help="Directory to store vikingbot log files",
    )

    args = parser.parse_args()

    # Set OPENVIKING_CONFIG_FILE environment variable if --config is provided
    # This allows OpenVikingConfigSingleton to load from the specified config file
    if args.config is not None:
        os.environ[OPENVIKING_CONFIG_ENV] = args.config

    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    # Load server config from ov.conf
    try:
        config = load_server_config(args.config)
        OpenVikingConfigSingleton.initialize(config_path=args.config)
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)

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
    if args.bot_url:
        config.bot_api_url = args.bot_url

    # Configure logging for Uvicorn
    configure_uvicorn_logging()

    bot_process: Optional[BotProcess] = None
    if config.with_bot:
        print(f"Bot API proxy enabled, forwarding to {config.bot_api_url}")
        # Determine if bot logging should be enabled
        enable_bot_logging = args.enable_bot_logging
        if enable_bot_logging is None:
            enable_bot_logging = args.with_bot
        # Start vikingbot gateway if --with-bot is set
        bot_process = _start_vikingbot_gateway(enable_bot_logging, args.bot_log_dir)

    # Create and run server app
    app = create_app(config)
    workers_info = f" (workers: {config.workers})" if config.workers > 1 else ""
    print(f"OpenViking HTTP Server is running on {config.host}:{config.port}{workers_info}")

    try:
        workers = config.workers
        if workers > 1:
            # Multi-worker mode requires an import string so each worker
            # can independently import the application.  We stash the
            # resolved config path in an env-var so that the factory can
            # pick it up (ServerConfig already reads OPENVIKING_CONFIG_FILE).
            uvicorn.run(
                "openviking.server.app:create_app",
                factory=True,
                host=config.host,
                port=config.port,
                workers=workers,
                log_config=None,
            )
        else:
            uvicorn.run(app, host=config.host, port=config.port, log_config=None)
    finally:
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


def _start_vikingbot_gateway(enable_logging: bool, log_dir: str) -> Optional[BotProcess]:
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
                [python_cmd, "-m", "vikingbot", "--help"], capture_output=True, timeout=5
            )
            if result.returncode == 0:
                vikingbot_cmd = [python_cmd, "-m", "vikingbot", "gateway"]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if vikingbot_cmd is None:
        print("Warning: vikingbot not found. Please install vikingbot first.")
        print("  uv pip install -e '.[bot,dev]'")
        return None

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
