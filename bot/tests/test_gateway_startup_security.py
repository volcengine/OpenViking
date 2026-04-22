# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest
from vikingbot.cli import commands


class _AbortCalled(RuntimeError):
    pass


def test_gateway_rejects_non_localhost_without_token(monkeypatch):
    config = SimpleNamespace(
        gateway=SimpleNamespace(host="0.0.0.0", port=18790, token=""),
    )

    monkeypatch.setattr(commands, "ensure_config", lambda _: config)

    def _abort(*args, **kwargs):
        raise AssertionError("_abort_if_port_in_use should not be reached")

    monkeypatch.setattr(commands, "_abort_if_port_in_use", _abort)

    with pytest.raises(SystemExit):
        commands.gateway(port=None, host=None, verbose=False, config_path=None)


def test_gateway_allows_non_localhost_with_token(monkeypatch):
    config = SimpleNamespace(
        gateway=SimpleNamespace(host="0.0.0.0", port=18790, token="secret"),
    )

    monkeypatch.setattr(commands, "ensure_config", lambda _: config)

    def _abort(*args, **kwargs):
        raise _AbortCalled

    monkeypatch.setattr(commands, "_abort_if_port_in_use", _abort)

    with pytest.raises(_AbortCalled):
        commands.gateway(port=None, host=None, verbose=False, config_path=None)
