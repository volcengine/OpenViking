# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch credential classification must not depend on Connector services."""

import subprocess
import sys

import pytest

from openviking.connector.delegate import ConnectorDelegate
from openviking.resource.watch_store import is_connector


@pytest.mark.parametrize(
    ("auth_state", "expected"),
    [
        (None, False),
        ({}, False),
        ({"provider": "connector_encrypted"}, True),
        ({"provider": "connector_plaintext"}, True),
        ({"provider": "other"}, False),
        ("connector_encrypted", False),
    ],
)
def test_store_and_delegate_share_watch_credential_classification(auth_state, expected):
    assert is_connector(auth_state) is expected
    assert ConnectorDelegate.is_watch_auth_state(auth_state) is expected


def test_watch_auth_classification_does_not_import_connector_service():
    script = """
import sys
from openviking.connector.watch_auth import is_watch_auth_state
assert is_watch_auth_state({'provider': 'connector_encrypted'})
assert 'openviking.connector.delegate' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
