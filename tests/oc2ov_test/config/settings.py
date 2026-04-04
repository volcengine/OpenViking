"""
项目配置文件
"""

import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# OpenClaw 服务配置
OPENCLAW_CONFIG = {
    "url": "http://127.0.0.1:18789/v1/responses",
    "auth_token": "Bearer afc2a221-b8be-4daf-98d6-1e8a2b1cf975",
    "agent_id": "main",
    "model": "custom-ark-cn-beijing-volces-com/ep-20260318110255-66jjc",
    "timeout": 120,
}

# 测试配置
TEST_CONFIG = {
    "wait_time": 10,
    "log_dir": os.path.join(BASE_DIR, "logs"),
    "report_dir": os.path.join(BASE_DIR, "reports"),
}

# 日志配置
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"},
        "detailed": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard", "level": "INFO"},
        "file": {
            "class": "logging.FileHandler",
            "filename": os.path.join(TEST_CONFIG["log_dir"], "test_run.log"),
            "formatter": "detailed",
            "level": "DEBUG",
            "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "DEBUG"},
}
