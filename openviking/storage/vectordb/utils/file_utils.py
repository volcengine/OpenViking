# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import logging


def touch_file(file_path: str) -> None:
    try:
        with open(file_path, "a"):
            pass
    except Exception as e:
        logging.error("touch file failed: {}, file_path: {}".format(e, file_path))
