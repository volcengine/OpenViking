# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for image tool sandbox file handling."""

import base64
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeSandbox:
    def __init__(self, files=None, error=None):
        self.files = files or {}
        self.error = error
        self.read_paths = []

    async def read_file_bytes(self, path: str) -> bytes:
        self.read_paths.append(path)
        if self.error:
            raise self.error
        return self.files[path]


class _SandboxManager:
    def __init__(self, sandbox):
        self._sandbox = sandbox

    async def get_sandbox(self, session_key):
        return self._sandbox


def _load_image_tool(monkeypatch):
    """Load the image tool directly so this regression stays isolated."""
    repo_root = Path(__file__).resolve().parents[2]
    image_path = repo_root / "bot" / "vikingbot" / "agent" / "tools" / "image.py"

    base_mod = types.ModuleType("vikingbot.agent.tools.base")

    class Tool:
        pass

    class ToolContext:
        pass

    base_mod.Tool = Tool
    base_mod.ToolContext = ToolContext

    events_mod = types.ModuleType("vikingbot.bus.events")

    class OutboundMessage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    events_mod.OutboundMessage = OutboundMessage

    utils_mod = types.ModuleType("vikingbot.utils")
    utils_mod.get_data_path = lambda: repo_root

    monkeypatch.setitem(sys.modules, "vikingbot", types.ModuleType("vikingbot"))
    monkeypatch.setitem(sys.modules, "vikingbot.agent", types.ModuleType("vikingbot.agent"))
    monkeypatch.setitem(
        sys.modules, "vikingbot.agent.tools", types.ModuleType("vikingbot.agent.tools")
    )
    monkeypatch.setitem(sys.modules, "vikingbot.agent.tools.base", base_mod)
    monkeypatch.setitem(sys.modules, "vikingbot.bus", types.ModuleType("vikingbot.bus"))
    monkeypatch.setitem(sys.modules, "vikingbot.bus.events", events_mod)
    monkeypatch.setitem(sys.modules, "vikingbot.utils", utils_mod)

    spec = importlib.util.spec_from_file_location("image_tool_under_test", image_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.ImageGenerationTool


@pytest.mark.asyncio
async def test_image_tool_uses_sandbox_for_local_paths(monkeypatch, tmp_path):
    secret = tmp_path / "host-secret.png"
    secret.write_bytes(b"OPENVIKING_HOST_SECRET_MARKER")
    sandbox = _FakeSandbox(error=PermissionError("outside sandbox"))
    context = SimpleNamespace(session_key="session", sandbox_manager=_SandboxManager(sandbox))

    ImageGenerationTool = _load_image_tool(monkeypatch)
    tool = ImageGenerationTool()

    with pytest.raises(PermissionError):
        await tool._parse_image_data(str(secret), context)

    assert sandbox.read_paths == [str(secret)]


@pytest.mark.asyncio
async def test_image_tool_reads_sandbox_local_file_paths(monkeypatch):
    sandbox = _FakeSandbox(files={"image.png": b"SANDBOX_IMAGE_BYTES"})
    context = SimpleNamespace(session_key="session", sandbox_manager=_SandboxManager(sandbox))

    ImageGenerationTool = _load_image_tool(monkeypatch)
    tool = ImageGenerationTool()
    data_uri, format_type = await tool._parse_image_data("image.png", context)

    assert sandbox.read_paths == ["image.png"]
    assert format_type == "data"
    assert data_uri.startswith("data:image/png;base64,")
    assert base64.b64decode(data_uri.split(",", 1)[1]) == b"SANDBOX_IMAGE_BYTES"


@pytest.mark.asyncio
async def test_image_tool_keeps_data_uri_support_without_sandbox_context(monkeypatch):
    ImageGenerationTool = _load_image_tool(monkeypatch)
    tool = ImageGenerationTool()
    data_uri = "data:image/png;base64,UE5H"

    parsed, format_type = await tool._parse_image_data(data_uri)

    assert parsed == data_uri
    assert format_type == "data"
