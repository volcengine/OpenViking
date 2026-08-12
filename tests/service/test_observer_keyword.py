# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Observer component for the local keyword sidecar."""

import pytest

from openviking.service.debug_service import ObserverService
from openviking.storage.keywordfs.keyword_fs import KeywordFS
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils.config.keyword_config import KeywordConfig


class _DummyAgfs:
    pass


@pytest.fixture
def obs():
    return ObserverService(config=None)


def _vfs_with_keyword(tmp_path, enabled=True):
    kfs = KeywordFS(tmp_path, KeywordConfig(enabled=True))
    if enabled:
        kfs.upsert("default", "viking://resources/a.md", "foo token", level=2)
    vfs = VikingFS(
        agfs=_DummyAgfs(),
        keyword_config=KeywordConfig(enabled=enabled),
        keyword_fs=kfs if enabled else None,
    )
    return vfs


def test_observer_keyword_ready(obs, tmp_path, monkeypatch):
    import openviking.storage.viking_fs as vf_module

    monkeypatch.setattr(vf_module, "get_viking_fs", lambda: _vfs_with_keyword(tmp_path))
    c = obs.keyword
    assert c.is_healthy and not c.has_errors
    assert "Enabled: true" in c.status
    assert "Docs: 1" in c.status


def test_observer_keyword_disabled(obs, tmp_path, monkeypatch):
    import openviking.storage.viking_fs as vf_module

    monkeypatch.setattr(vf_module, "get_viking_fs", lambda: _vfs_with_keyword(tmp_path, enabled=False))
    c = obs.keyword
    assert c.is_healthy
    assert "Disabled" in c.status


def test_observer_keyword_not_wired(obs, tmp_path, monkeypatch):
    import openviking.storage.viking_fs as vf_module

    vfs = VikingFS(agfs=_DummyAgfs(), keyword_config=KeywordConfig(enabled=True), keyword_fs=None)
    monkeypatch.setattr(vf_module, "get_viking_fs", lambda: vfs)
    c = obs.keyword
    assert not c.is_healthy and c.has_errors
