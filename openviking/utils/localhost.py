# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for raw localhost configuration values."""

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_localhost(host: str) -> bool:
    """Return whether a raw host value names a supported loopback address."""
    return host.lower() in _LOCALHOST_HOSTS
