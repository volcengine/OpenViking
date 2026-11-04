# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression test for the encryption readiness probe's bootstrap call.

``EncryptionProbeDataSource.read_probe_state`` used to call
``bootstrap_encryption()`` without arguments.  That function is a coroutine
that requires the full configuration mapping, so the call raised
``TypeError`` (missing ``config``) before the coroutine was ever awaited:
the probe reported ``ok=False`` for every deployment regardless of whether
encryption could actually be initialised.

The fix materialises the coroutine with the resolved configuration
(``run_async(bootstrap_encryption(cfg.to_dict()))``), which is what makes the
probe mean what its docstring says.

This file lives at the root of ``tests/api_test`` because that is the one
broad test tree the workflows collect (``cd tests/api_test && pytest .``);
the datasource suite under ``tests/metrics/`` is not collected by any
workflow, so its copy of this assertion never runs.
"""

from types import SimpleNamespace

import pytest
from openviking.metrics.datasources.encryption import EncryptionProbeDataSource

BOOTSTRAP_TARGET = "openviking.crypto.config.bootstrap_encryption"


def _config():
    class _Cfg:
        encryption = SimpleNamespace(provider="volcengine")

        def to_dict(self):
            return {"encryption": {"provider": "volcengine"}}

    return _Cfg()


def test_probe_reports_ready_when_bootstrap_succeeds(monkeypatch):
    """A successful bootstrap must surface as ``ok=True`` with the provider name."""
    seen: list[dict] = []

    async def _bootstrap(config):
        seen.append(config)

    monkeypatch.setattr(BOOTSTRAP_TARGET, _bootstrap)

    envelope = EncryptionProbeDataSource(config_provider=_config).read_probe_state()

    assert envelope.ok is True, (
        f"probe reported a failure: {envelope.error_type}: {envelope.error_message}"
    )
    assert envelope.value == (True, "volcengine")
    # The coroutine must receive the resolved configuration, not be called bare.
    assert seen == [{"encryption": {"provider": "volcengine"}}]


def test_probe_reports_not_ready_when_bootstrap_raises(monkeypatch):
    """A raising bootstrap must surface as ``ok=False`` rather than escaping."""

    async def _boom(config):
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(BOOTSTRAP_TARGET, _boom)

    envelope = EncryptionProbeDataSource(config_provider=_config).read_probe_state()

    assert envelope.ok is False
    assert envelope.value == (False, "volcengine")
    assert envelope.error_type is not None


def test_probe_falls_back_to_unknown_provider(monkeypatch):
    """A config provider that raises must not stop the probe reporting the default."""

    async def _bootstrap(config):
        return None

    monkeypatch.setattr(BOOTSTRAP_TARGET, _bootstrap)

    def _raise():
        raise RuntimeError("no config")

    envelope = EncryptionProbeDataSource(config_provider=_raise).read_probe_state()

    assert envelope.value == (False, "unknown")


@pytest.mark.parametrize("provider", ["volcengine", "local"])
def test_probe_echoes_configured_provider(monkeypatch, provider):
    """The reported provider is the one the config names, not a constant."""

    async def _bootstrap(config):
        return None

    monkeypatch.setattr(BOOTSTRAP_TARGET, _bootstrap)

    class _Cfg:
        encryption = SimpleNamespace(provider=provider)

        def to_dict(self):
            return {"encryption": {"provider": provider}}

    envelope = EncryptionProbeDataSource(config_provider=_Cfg).read_probe_state()

    assert envelope.ok is True
    assert envelope.value == (True, provider)
