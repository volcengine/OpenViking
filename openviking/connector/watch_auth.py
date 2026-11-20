# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Connector Watch credential markers without service-layer dependencies."""

from typing import Any

WATCH_AUTH_PROVIDER = "connector_encrypted"
WATCH_PLAINTEXT_AUTH_PROVIDER = "connector_plaintext"


def is_watch_auth_state(auth_state: Any) -> bool:
    return isinstance(auth_state, dict) and auth_state.get("provider") in {
        WATCH_AUTH_PROVIDER,
        WATCH_PLAINTEXT_AUTH_PROVIDER,
    }
