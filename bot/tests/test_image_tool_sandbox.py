# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for image tool sandbox file handling."""

import base64
from types import SimpleNamespace

import pytest
from vikingbot.agent.tools import image as image_module
from vikingbot.agent.tools.image import ImageGenerationTool


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


@pytest.mark.asyncio
async def test_image_tool_uses_sandbox_for_local_paths(tmp_path):
    secret = tmp_path / "host-secret.png"
    secret.write_bytes(b"OPENVIKING_HOST_SECRET_MARKER")
    sandbox = _FakeSandbox(error=PermissionError("outside sandbox"))
    context = SimpleNamespace(session_key="session", sandbox_manager=_SandboxManager(sandbox))

    tool = ImageGenerationTool()

    with pytest.raises(PermissionError):
        await tool._parse_image_data(str(secret), context)

    assert sandbox.read_paths == [str(secret)]


@pytest.mark.asyncio
async def test_image_tool_reads_sandbox_local_file_paths():
    sandbox = _FakeSandbox(files={"image.png": b"SANDBOX_IMAGE_BYTES"})
    context = SimpleNamespace(session_key="session", sandbox_manager=_SandboxManager(sandbox))

    tool = ImageGenerationTool()
    data_uri, format_type = await tool._parse_image_data("image.png", context)

    assert sandbox.read_paths == ["image.png"]
    assert format_type == "data"
    assert data_uri.startswith("data:image/png;base64,")
    assert base64.b64decode(data_uri.split(",", 1)[1]) == b"SANDBOX_IMAGE_BYTES"


@pytest.mark.parametrize(
    "source, expected_type",
    [("data:image/png;base64,UE5H", "data"), ("https://example.com/image.png", "url")],
)
async def test_image_tool_accepts_remote_images_without_sandbox(source, expected_type):
    assert await ImageGenerationTool()._parse_image_data(source) == (source, expected_type)


@pytest.mark.asyncio
async def test_image_tool_rejects_local_paths_without_sandbox_context():
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
    monkeypatch.setattr(image_module, "get_data_path", lambda: tmp_path)
    captured_kwargs = {}

    async def fake_image_edit(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"OUTPUT").decode())]
        )

    monkeypatch.setattr(image_module.litellm, "aimage_edit", fake_image_edit)
    tool = ImageGenerationTool(gen_image_model="openai/dall-e-2")

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


@pytest.mark.asyncio
async def test_image_tool_saves_generated_jpeg_with_jpg_extension(monkeypatch, tmp_path):
    monkeypatch.setattr(image_module, "get_data_path", lambda: tmp_path)
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01fake-jpeg"

    async def fake_image_generation(**kwargs):
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(jpeg_bytes).decode())]
        )

    monkeypatch.setattr(image_module.litellm, "aimage_generation", fake_image_generation)
    context = SimpleNamespace(session_key="session", channel_metadata={})
    tool = ImageGenerationTool()

    result = await tool.execute(context, mode="generate", prompt="make a jpeg", send_to_user=False)

    saved_files = list((tmp_path / "images").iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].suffix == ".jpg"
    assert saved_files[0].read_bytes() == jpeg_bytes
    assert f"send://{saved_files[0].name}" in result
