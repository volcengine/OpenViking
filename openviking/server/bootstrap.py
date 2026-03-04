# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Bootstrap script for OpenViking HTTP Server."""

import argparse
import os
import subprocess
import sys
import time

import uvicorn

from openviking.server.app import create_app
from openviking.server.config import load_server_config
from openviking_cli.utils.logger import configure_uvicorn_logging


def _get_version() -> str:
    try:
        from openviking import __version__

        return __version__
    except ImportError:
        return "unknown"


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

    args = parser.parse_args()

    # Set OPENVIKING_CONFIG_FILE environment variable if --config is provided
    # This allows OpenVikingConfigSingleton to load from the specified config file
    if args.config is not None:
        os.environ["OPENVIKING_CONFIG_FILE"] = args.config

    # Load server config from ov.conf
    config = load_server_config(args.config)

    # Override with command line arguments
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    if args.with_bot:
        config.with_bot = True
    if args.bot_url:
        config.bot_api_url = args.bot_url

    # Configure logging for Uvicorn
    configure_uvicorn_logging()

    # Create and run app
    app = create_app(config)
    print(f"OpenViking HTTP Server is running on {config.host}:{config.port}")
    if config.with_bot:
        print(f"Bot API proxy enabled, forwarding to {config.bot_api_url}")

    # Start vikingbot gateway if --with-bot is set
    bot_process = None
    if args.with_bot:
        bot_process = _start_vikingbot_gateway()

    try:
        uvicorn.run(app, host=config.host, port=config.port, log_config=None)
    finally:
        # Cleanup vikingbot process on shutdown
        if bot_process is not None:
            _stop_vikingbot_gateway(bot_process)


def _start_vikingbot_gateway() -> subprocess.Popen:
    """Start vikingbot gateway as a subprocess."""
    print("Starting vikingbot gateway...")

    # Check if vikingbot is available
    vikingbot_cmd = None
    if subprocess.run(["which", "vikingbot"], capture_output=True).returncode == 0:
        vikingbot_cmd = ["vikingbot", "gateway"]
    else:
        # Try python -m vikingbot
        python_cmd = sys.executable
        try:
            result = subprocess.run(
                [python_cmd, "-m", "vikingbot", "--help"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                vikingbot_cmd = [python_cmd, "-m", "vikingbot", "gateway"]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if vikingbot_cmd is None:
        print("Warning: vikingbot not found. Please install vikingbot first.")
        print("  cd bot && uv pip install -e '.[dev]'")
        return None

    # Start vikingbot gateway process
    try:
        # Set environment to ensure it uses the same Python environment
        env = os.environ.copy()

        process = subprocess.Popen(
            vikingbot_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # Wait a moment to check if it started successfully
        time.sleep(2)
        if process.poll() is not None:
            # Process exited early
            stdout, stderr = process.communicate(timeout=1)
            print(f"Warning: vikingbot gateway exited early (code {process.returncode})")
            if stderr:
                print(f"Error: {stderr[:500]}")
            return None

        print(f"Vikingbot gateway started (PID: {process.pid})")
        return process

    except Exception as e:
        print(f"Warning: Failed to start vikingbot gateway: {e}")
        return None


def _stop_vikingbot_gateway(process: subprocess.Popen) -> None:
    """Stop the vikingbot gateway subprocess."""
    if process is None:
        return

    print(f"\nStopping vikingbot gateway (PID: {process.pid})...")

    try:
        # Try graceful termination first
        process.terminate()
        try:
            process.wait(timeout=5)
            print("Vikingbot gateway stopped gracefully.")
        except subprocess.TimeoutExpired:
            # Force kill if it doesn't stop in time
            process.kill()
            process.wait()
            print("Vikingbot gateway force killed.")
    except Exception as e:
        print(f"Error stopping vikingbot gateway: {e}")


if __name__ == "__main__":
    main()
