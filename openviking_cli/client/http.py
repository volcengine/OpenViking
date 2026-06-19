# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility shim for the legacy HTTP client import path."""

from openviking_sdk.client import ERROR_CODE_TO_EXCEPTION, AsyncHTTPClient

__all__ = ["AsyncHTTPClient", "ERROR_CODE_TO_EXCEPTION"]
