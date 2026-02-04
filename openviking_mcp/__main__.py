# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking MCP Server entry point.

Run with: python -m openviking_mcp
"""

import asyncio
import logging
import sys

from .config import get_config
from .server import mcp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)


def main():
    """Main entry point for OpenViking MCP Server."""
    try:
        config = get_config()
        is_valid, error = config.validate()

        if not is_valid:
            logger.error(f"Configuration validation failed: {error}")
            logger.error("Please set OPENVIKING_CONFIG_FILE environment variable")
            sys.exit(1)

        logger.info(f"Using config file: {config.config_file}")
        logger.info(f"Data directory: {config.data_dir}")
        logger.info("Starting OpenViking MCP Server...")

        mcp.run(transport="stdio")

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
