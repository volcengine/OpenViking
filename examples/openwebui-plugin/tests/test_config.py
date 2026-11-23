# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Environment configuration and listener address regression tests."""

from __future__ import annotations

import pytest
from openviking_openwebui.config import load_settings


@pytest.mark.parametrize(
    "bind, host, port",
    [
        ("[::1]:18765", "::1", 18765),
        ("[::]:18765", "::", 18765),
        ("[2001:db8::1]:18765", "2001:db8::1", 18765),
        ("[::1]", "::1", 8765),
        ("[::1]:", "::1", 8765),
        ("[::1]:invalid", "::1", 8765),
        ("[::ffff:127.0.0.1]:18765", "::ffff:127.0.0.1", 18765),
        ("127.0.0.1:18765", "127.0.0.1", 18765),
        ("0.0.0.0:18765", "0.0.0.0", 18765),
        ("localhost:18765", "localhost", 18765),
        ("localhost", "localhost", 8765),
        ("localhost:", "localhost", 8765),
        ("localhost:invalid", "localhost", 8765),
        (":18765", "0.0.0.0", 18765),
        ("[::1", "[", 8765),
        ("[::1]junk", "[", 8765),
    ],
)
def test_load_settings_parses_bind(monkeypatch, bind, host, port):
    monkeypatch.setenv("OV_BIND", bind)

    settings = load_settings()

    assert settings.bind_host == host
    assert settings.bind_port == port


def test_load_settings_default_bind(monkeypatch):
    monkeypatch.delenv("OV_BIND", raising=False)

    settings = load_settings()

    assert (settings.bind_host, settings.bind_port) == ("0.0.0.0", 8765)
