# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""C++ logging initialization for vectordb engine."""

import sys
import threading

_cpp_logging_initialized = False
_cpp_logging_lock = threading.Lock()


def _convert_python_format_to_spdlog(py_format: str) -> str:
    """
    Convert Python logging format string to spdlog pattern string.

    Args:
        py_format: Python logging format string (e.g., "%(asctime)s - %(name)s - %(message)s")

    Returns:
        spdlog compatible format string
    """
    mapping = {
        "%(asctime)s": "%Y-%m-%d %H:%M:%S,%e",
        "%(levelname)s": "%l",
        "%(levelname)-8s": "%-8l",
        "%(name)s": "%n",
        "%(message)s": "%v",
        "%(process)d": "%P",
        "%(thread)d": "%t",
        "%(threadName)s": "%t",
        "%(filename)s": "%s",
        "%(lineno)d": "%#",
        "%(module)s": "%s",
        "%%": "%",
    }

    spd_format = py_format
    for py_key, spd_val in mapping.items():
        spd_format = spd_format.replace(py_key, spd_val)

    return spd_format


def init_cpp_logging():
    """Initialize C++ logging with configuration from OpenVikingConfig. Thread-safe."""
    global _cpp_logging_initialized

    with _cpp_logging_lock:
        if _cpp_logging_initialized:
            return

        try:
            from openviking.storage.vectordb.engine import init_logging
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()

            log_level = config.log_level.upper() if config.log_level else "INFO"
            log_output = config.log_output if config.log_output else "stdout"

            py_log_format = (
                config.log_format
                if config.log_format
                else "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            spd_log_format = _convert_python_format_to_spdlog(py_log_format)

            init_logging(log_level, log_output, spd_log_format)
            _cpp_logging_initialized = True
        except ImportError:
            pass
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to initialize C++ logging: {e}\n")
