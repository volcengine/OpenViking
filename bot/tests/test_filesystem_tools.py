# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingBot filesystem tool contract tests."""

import codecs
from types import SimpleNamespace

import pytest
from vikingbot.agent.tools.filesystem import WriteFileTool
from vikingbot.config.schema import SessionKey
from vikingbot.sandbox.backends.direct import DirectBackend


class _SandboxManager:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    async def get_sandbox(self, _session_key):
        return self.sandbox


def _render(text: str, encoding: str, bom: bytes, newline: str) -> bytes:
    text = text.replace("\n", newline)
    return bom + text.encode(encoding)


def _tool_context(workspace):
    session_key = SessionKey(type="cli", channel_id="default", chat_id="write-test")
    sandbox = DirectBackend(SimpleNamespace(restrict_workspaces={}), session_key, workspace)
    return SimpleNamespace(
        sandbox_manager=_SandboxManager(sandbox),
        session_key=session_key,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("encoding", "bom", "newline", "text"),
    [
        ("utf-8", codecs.BOM_UTF8, "\r\n", "fruit = APPLE\nこんにちは"),
        ("utf-8", b"", "\r\n", "fruit = APPLE\nこんにちは"),
        ("ascii", b"", "\r\n", "fruit = APPLE\nline two"),
        ("utf-16-le", codecs.BOM_UTF16_LE, "\r\n", "fruit = APPLE\nメモ"),
        ("utf-16-be", codecs.BOM_UTF16_BE, "\r", "fruit = APPLE\nメモ"),
        ("utf-8", b"", "\n", "fruit = APPLE\nこんにちは"),
    ],
)
async def test_write_file_preserves_supported_existing_text_format(
    tmp_path, encoding, bom, newline, text
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "example.txt"
    path.write_bytes(_render(text, encoding, bom, newline))

    result = await WriteFileTool().execute(
        _tool_context(workspace),
        path=path.name,
        content=text.replace("APPLE", "ORANGE"),
    )

    assert result.startswith("Successfully wrote")
    assert path.read_bytes() == _render(text.replace("APPLE", "ORANGE"), encoding, bom, newline)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("encoding", "text"),
    [
        ("cp1252", '// è già così\nfruit = "APPLE"'),
        ("shift_jis", "日本語のメモ\nfruit = APPLE"),
        ("utf-16-le", "unmarked UTF-16\nfruit = APPLE"),
    ],
)
async def test_write_file_refuses_ambiguous_existing_encoding(tmp_path, encoding, text):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "example.txt"
    before = text.replace("\n", "\r\n").encode(encoding)
    path.write_bytes(before)

    result = await WriteFileTool().execute(
        _tool_context(workspace),
        path=path.name,
        content=text.replace("APPLE", "ORANGE"),
    )

    assert result == "Error: existing file has an unsupported text encoding"
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_write_file_handles_chunk_boundary_text_and_newline(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "example.txt"
    text = "a" * 65535 + "界\r\nfruit = APPLE"
    path.write_bytes(text.encode())

    result = await WriteFileTool().execute(
        _tool_context(workspace),
        path=path.name,
        content=text.replace("APPLE", "ORANGE").replace("\r\n", "\n"),
    )

    assert result.startswith("Successfully wrote")
    assert path.read_bytes() == text.replace("APPLE", "ORANGE").encode()


@pytest.mark.asyncio
async def test_write_file_keeps_new_file_utf8_behavior(tmp_path):
    workspace = tmp_path / "workspace"

    result = await WriteFileTool().execute(
        _tool_context(workspace), path="new.txt", content="line one\nこんにちは"
    )

    assert result.startswith("Successfully wrote")
    assert (workspace / "new.txt").read_bytes() == "line one\nこんにちは".encode()
