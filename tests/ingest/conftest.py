# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Test isolation for the ingest suite.

``resolve_git_human_peer`` shells out to ``git config``, which otherwise reads the
developer machine's GLOBAL identity and makes human-peer resolution non-deterministic.
Neutralize git config discovery so user-side peers fall back to the configured value.
"""

import pytest

from openviking.ingest import peer


@pytest.fixture(autouse=True)
def _isolate_git_identity(tmp_path_factory, monkeypatch):
    empty = tmp_path_factory.mktemp("gitcfg") / "empty.cfg"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    peer._GIT_PEER_CACHE.clear()
    yield
    peer._GIT_PEER_CACHE.clear()
