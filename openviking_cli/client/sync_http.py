# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility shim for the legacy sync HTTP client import path."""

from openviking_cli.client._http_compat import SyncHTTPClient

__all__ = ["SyncHTTPClient"]
