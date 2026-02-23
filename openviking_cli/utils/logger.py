# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Logging utilities for OpenViking.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Tuple


def _load_log_config() -> Tuple[str, str, str, Optional[Any]]:
    config = None
    try:
        from openviking_cli.utils.config import get_openviking_config

        config = get_openviking_config()
        log_level_str = config.log.level.upper()
        log_format = config.log.format
        log_output = config.log.output

        if log_output == "file":
            workspace_path = Path(config.storage.workspace).resolve()
            log_dir = workspace_path / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_output = str(log_dir / "openviking.log")
    except Exception:
        log_level_str = "INFO"
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        log_output = "stdout"

    return log_level_str, log_format, log_output, config


def _create_log_handler(log_output: str, config: Optional[Any]) -> logging.Handler:
    # Prevent creating a file literally named "file"
    if log_output == "file":
        log_output = "stdout"

    if log_output == "stdout":
        return logging.StreamHandler(sys.stdout)
    elif log_output == "stderr":
        return logging.StreamHandler(sys.stderr)
    else:
        if config is not None:
            try:
                log_rotation = config.log.rotation
                if log_rotation:
                    log_rotation_days = config.log.rotation_days
                    log_rotation_interval = config.log.rotation_interval

                    if log_rotation_interval == "midnight":
                        when = "midnight"
                        interval = 1
                    else:
                        when = log_rotation_interval
                        interval = 1

                    return TimedRotatingFileHandler(
                        log_output,
                        when=when,
                        interval=interval,
                        backupCount=log_rotation_days,
                        encoding="utf-8",
                    )
                else:
                    return logging.FileHandler(log_output, encoding="utf-8")
            except Exception:
                return logging.FileHandler(log_output, encoding="utf-8")
        else:
            return logging.FileHandler(log_output, encoding="utf-8")


def get_logger(
    name: str = "openviking",
    format_string: Optional[str] = None,
) -> logging.Logger:
    logger = logging.getLogger(name)

    if not logger.handlers:
        log_level_str, log_format, log_output, config = _load_log_config()
        level = getattr(logging, log_level_str, logging.INFO)
        handler = _create_log_handler(log_output, config)

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


def configure_uvicorn_logging() -> None:
    """Configure Uvicorn loggers to use OpenViking's logging configuration.

    This function configures the 'uvicorn', 'uvicorn.error', and 'uvicorn.access'
    loggers to use the same handlers and format as our openviking loggers.
    """
    log_level_str, log_format, log_output, config = _load_log_config()
    level = getattr(logging, log_level_str, logging.INFO)
    handler = _create_log_handler(log_output, config)
    formatter = logging.Formatter(log_format)
    handler.setFormatter(formatter)

    # Configure all Uvicorn loggers
    uvicorn_logger_names = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for logger_name in uvicorn_logger_names:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
