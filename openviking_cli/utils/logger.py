# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Logging utilities for OpenViking.
"""

import logging
import sys
from typing import Optional


def get_logger(
    name: str = "openviking",
    format_string: Optional[str] = None,
) -> logging.Logger:
    """
    Get a configured logger.

    Args:
        name: Logger name
        format_string: Custom format string (overrides config)

    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        try:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            log_level_str = config.log_level.upper()
            log_format = config.log_format
            log_output = config.log_output
        except Exception:
            log_level_str = "INFO"
            log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            log_output = "stdout"

        level = getattr(logging, log_level_str, logging.INFO)

        if log_output == "stdout":
            handler = logging.StreamHandler(sys.stdout)
        elif log_output == "stderr":
            handler = logging.StreamHandler(sys.stderr)
        else:
            handler = logging.FileHandler(log_output)

        if format_string is None:
            format_string = log_format

        formatter = logging.Formatter(format_string)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False

        logger.setLevel(level)

    return logger


# Default logger instance
default_logger = get_logger()
