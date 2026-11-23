# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""C++ logging initialization for vectordb engine."""

import threading

_cpp_logging_initialized = False
_cpp_logging_lock = threading.Lock()


def _convert_python_format_to_spdlog(py_format: str) -> str:
    """Native logging implements the common format directly."""
    from openviking.observability.log_safety import DEFAULT_LOG_FORMAT

    if py_format != DEFAULT_LOG_FORMAT:
        raise ValueError("C++ logging requires the common cross-runtime format")
    return py_format


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

            log_level = config.log.level.upper() if config.log.level else "INFO"
            log_output = config.log.output if config.log.output else "stdout"

            # If log_output is "file", convert it to the actual file path
            if log_output == "file":
                from pathlib import Path

                workspace_path = Path(config.storage.workspace).resolve()
                log_dir = workspace_path / "log"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_output = str(log_dir / "openviking.log")

            py_log_format = config.log.format if config.log.format else _default_log_format()
            spd_log_format = _convert_python_format_to_spdlog(py_log_format)

            init_logging(log_level, log_output, spd_log_format)
            _cpp_logging_initialized = True
        except ImportError:
            pass
        except Exception as e:
            from openviking_cli.utils.logger import get_logger

            get_logger(__name__).error(
                "c++ logging initialization failed error_type=%s", type(e).__name__
            )
            raise


def _default_log_format() -> str:
    from openviking.observability.log_safety import DEFAULT_LOG_FORMAT

    return DEFAULT_LOG_FORMAT
