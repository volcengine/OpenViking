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


def _load_image_module(monkeypatch):
    """Load the image tool module directly so this regression stays isolated."""
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
    return module


def _load_image_tool(monkeypatch):
    return _load_image_module(monkeypatch).ImageGenerationTool


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


def test_image_tool_documents_mask_sandbox_local_paths(monkeypatch):
    ImageGenerationTool = _load_image_tool(monkeypatch)
    tool = ImageGenerationTool()

    mask_description = tool.parameters["properties"]["mask"]["description"]

    assert "sandbox-local" in mask_description


@pytest.mark.asyncio
async def test_image_tool_keeps_url_support_without_sandbox_context(monkeypatch):
    ImageGenerationTool = _load_image_tool(monkeypatch)
    tool = ImageGenerationTool()
    image_url = "https://example.com/image.png"

    parsed, format_type = await tool._parse_image_data(image_url)

    assert parsed == image_url
    assert format_type == "url"


@pytest.mark.asyncio
async def test_image_tool_rejects_local_paths_without_sandbox_context(monkeypatch):
    ImageGenerationTool = _load_image_tool(monkeypatch)
    tool = ImageGenerationTool()

    with pytest.raises(ValueError, match="sandbox context"):
        await tool._parse_image_data("image.png")


@pytest.mark.asyncio
async def test_edit_mode_reads_base_image_and_mask_from_sandbox(monkeypatch, tmp_path):
    sandbox = _FakeSandbox(
        files={
            "base.png": b"SANDBOX_BASE_IMAGE_BYTES",
            "mask.png": b"SANDBOX_MASK_IMAGE_BYTES",
        }
    )
    context = SimpleNamespace(session_key="session", sandbox_manager=_SandboxManager(sandbox))
    module = _load_image_module(monkeypatch)
    monkeypatch.setattr(module, "get_data_path", lambda: tmp_path)
    captured_kwargs = {}

    async def fake_image_edit(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"OUTPUT").decode())]
        )

    monkeypatch.setattr(module.litellm, "aimage_edit", fake_image_edit)
    tool = module.ImageGenerationTool(gen_image_model="openai/dall-e-2")

    result = await tool.execute(
        context,
        mode="edit",
        prompt="edit safely",
        base_image="base.png",
        mask="mask.png",
        send_to_user=False,
    )

    assert sandbox.read_paths == ["base.png", "mask.png"]
    assert captured_kwargs["image"].startswith("data:image/png;base64,")
    assert captured_kwargs["mask"].startswith("data:image/png;base64,")
    assert (
        base64.b64decode(captured_kwargs["image"].split(",", 1)[1]) == b"SANDBOX_BASE_IMAGE_BYTES"
    )
    assert base64.b64decode(captured_kwargs["mask"].split(",", 1)[1]) == b"SANDBOX_MASK_IMAGE_BYTES"
    assert result.startswith("生成图片：")
