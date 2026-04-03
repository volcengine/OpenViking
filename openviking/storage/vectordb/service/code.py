# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from enum import Enum


class ErrorCode(Enum):
    NO_ERROR = 0
    INVALID_PARAM = 1000001
    PROJECT_NOT_EXIST = 1000002
    COLLECTION_NOT_EXIST = 1000003
    INDEX_NOT_EXIST = 1000003
    COLLECTION_EXIST = 1000003
    INDEX_EXIST = 1000003
    INTERNAL_ERR = 1000004
