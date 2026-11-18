# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Connector Watch authentication markers shared with persistence backends.

Store code only needs to recognize the envelope provider. Keep that predicate
independent of the Connector delegate, its services, and runtime configuration.
"""

from typing import Any, Optional

CONNECTOR_ENCRYPTED_AUTH_PROVIDER = "connector_encrypted"
CONNECTOR_PLAINTEXT_AUTH_PROVIDER = "connector_plaintext"


def is_connector_watch_auth_state(auth_state: Optional[dict[str, Any]]) -> bool:
    return isinstance(auth_state, dict) and auth_state.get("provider") in {
        CONNECTOR_ENCRYPTED_AUTH_PROVIDER,
        CONNECTOR_PLAINTEXT_AUTH_PROVIDER,
    }
