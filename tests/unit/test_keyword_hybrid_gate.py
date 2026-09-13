# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tri-state request override for hybrid keyword fusion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.storage.keywordfs.keyword_fs import KeywordFS
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils.config.keyword_config import HybridRetrievalConfig, KeywordConfig


class _DummyAgfs:
    def read(self, path, offset=0, size=-1):  # pragma: no cover - unused here
        return b""


def _fs(tmp_path, *, config_enabled: bool) -> VikingFS:
    kfs = KeywordFS(tmp_path, KeywordConfig(enabled=True))
    kfs.upsert("default", "viking://resources/a.md", "needle")
    return VikingFS(
        agfs=_DummyAgfs(),
        retrieval_config=SimpleNamespace(hybrid=HybridRetrievalConfig(enabled=config_enabled)),
        keyword_config=KeywordConfig(enabled=True),
        keyword_fs=kfs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_enabled", "override", "expect_called"),
    [
        (True, None, True),
        (False, None, False),
        (False, True, True),
        (True, False, False),
    ],
)
async def test_tristate_override(
    tmp_path, monkeypatch, config_enabled, override, expect_called
):
    fs = _fs(tmp_path, config_enabled=config_enabled)
    calls = []

    class _Recaller:
        def __init__(self, *args, **kwargs):
            pass

        async def enhance(self, **kwargs):
            calls.append(kwargs)
            return kwargs["dense"]

    monkeypatch.setattr("openviking.retrieve.hybrid_keyword.HybridKeywordRecaller", _Recaller)
    result = await fs._maybe_hybrid_keyword(
        "needle",
        ["dense"],
        ["viking://resources"],
        SimpleNamespace(account_id="default"),
        limit=5,
        override=override,
    )
    assert result == ["dense"]
    assert bool(calls) is expect_called
