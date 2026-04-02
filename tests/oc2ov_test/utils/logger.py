"""
日志配置工具
"""

import logging
import logging.config
import os

from config.settings import LOGGING_CONFIG, TEST_CONFIG


def setup_logger():
    """
    配置日志系统
    """
    log_dir = TEST_CONFIG["log_dir"]
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    logging.config.dictConfig(LOGGING_CONFIG)
    return logging.getLogger(__name__)
