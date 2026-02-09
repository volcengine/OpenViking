# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Bootstrap script for OpenViking HTTP Server."""

import argparse
import os

import uvicorn

from openviking.server.app import create_app
from openviking.server.config import ServerConfig, load_server_config


def main():
    """Main entry point for openviking-server command."""
    parser = argparse.ArgumentParser(
        description="OpenViking HTTP Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
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
        "--path",
        type=str,
        default=None,
        help="Storage path for embedded mode",
    )
    parser.add_argument(
        "--vectordb-url",
        type=str,
        default=None,
        help="VectorDB service URL for service mode",
    )
    parser.add_argument(
        "--agfs-url",
        type=str,
        default=None,
        help="AGFS service URL for service mode",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key for authentication (if not set, authentication is disabled)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file",
    )

    args = parser.parse_args()

    # Set OPENVIKING_CONFIG_FILE environment variable if --config is provided
    # This allows OpenVikingConfig to load from the specified config file
    if args.config is not None:
        os.environ["OPENVIKING_CONFIG_FILE"] = args.config

    # Load config from file and environment
    config = load_server_config(args.config)

    # Override with command line arguments
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    if args.path is not None:
        config.storage_path = args.path
    if args.vectordb_url is not None:
        config.vectordb_url = args.vectordb_url
    if args.agfs_url is not None:
        config.agfs_url = args.agfs_url
    if args.api_key is not None:
        config.api_key = args.api_key

    # Create and run app
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
