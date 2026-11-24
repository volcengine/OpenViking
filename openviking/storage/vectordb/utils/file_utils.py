# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def touch_file(file_path: str) -> None:
    try:
        with open(file_path, "a"):
            pass
    except Exception as e:
        logger.error("touch_file failed error_type=%s", type(e).__name__)
