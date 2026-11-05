# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture(autouse=True)
def _config():
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "memory": {"eager_prefetch": True, "prefetch_search_topn": 20},
        }
    )
    yield
    OpenVikingConfigSingleton.reset_instance()


class _Schema:
    stage = "user"
    directory = "viking://{user_space}/memories"
    operation_mode = "upsert"

    def filename_has_variables(self):
        return True


class _IsolationHandler:
    def allows_schema(self, schema):
        return True

    def get_read_scope(self):
        return SimpleNamespace(user_ids=["u"], peer_ids=[])

    def render_schema_directories(self, schema):
        return ["viking://u/memories"]


@pytest.mark.asyncio
async def test_prefetch_search_topn_reaches_configured_value(monkeypatch):
    provider = SessionExtractContextProvider(messages=[])
    provider._eager_prefetch = True
    provider._prefetch_search_topn = 20
    provider._isolation_handler = _IsolationHandler()

    monkeypatch.setattr(provider, "_build_conversation_message", lambda: {"role": "user", "content": ""})
    monkeypatch.setattr(provider, "_get_registry", lambda: SimpleNamespace(list_all=lambda include_disabled=False: [_Schema()]))
    monkeypatch.setattr(provider, "_build_prefetch_search_query", lambda: "q")

    async def fake_search_files(query, search_uris=None, limit=5):
        captured["limit"] = limit
        return [f"viking://u/memories/f{i}.md" for i in range(limit)]

    captured = {}
    read_uris = []

    async def fake_read_file(uri):
        read_uris.append(uri)
        return {"uri": uri}

    monkeypatch.setattr(provider, "search_files", fake_search_files)
    monkeypatch.setattr(provider, "read_file", fake_read_file)

    await provider.prefetch()

    assert captured["limit"] == 20
    assert len(read_uris) == 20
