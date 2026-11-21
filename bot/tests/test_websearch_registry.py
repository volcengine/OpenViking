# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""web_search uses Keenable only when it is selected in config."""

import pytest
from vikingbot.agent.tools.factory import register_default_tools
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import Config


@pytest.mark.parametrize(("backend", "expected"), [("auto", "ddgs"), ("keenable", "keenable")])
def test_keenable_is_opt_in(monkeypatch, backend, expected):
    for var in ("BRAVE_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KEENABLE_API_KEY", "key")  # a key alone does not opt in
    config = Config(tools={"web": {"search": {"backend": backend}}})
    registry = ToolRegistry(config=config)
    register_default_tools(registry, config)
    assert registry.get("web_search").backend.name == expected
