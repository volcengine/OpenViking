# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Protocol-level MCP stdio tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _ensure_agfs_binary_available() -> None:
    binary_name = "agfs-server.exe" if os.name == "nt" else "agfs-server"
    binary_path = (Path("openviking") / "bin" / binary_name).resolve()
    if not binary_path.exists():
        pytest.skip(f"AGFS binary not found: {binary_path}")


def _repo_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def _extract_payload(call_result) -> dict:
    texts = [item.text for item in call_result.content if getattr(item, "type", "") == "text"]
    assert texts, "No text content returned by MCP tool"
    return json.loads(texts[0])


@pytest.mark.anyio
async def test_stdio_readonly_hides_write_tools_and_health_works():
    _ensure_agfs_binary_available()
    with tempfile.TemporaryDirectory(dir=_repo_root()) as data_path:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openviking", "mcp", "--path", data_path, "--access-level", "readonly"],
            cwd=_repo_root(),
        )

        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}
                assert "openviking_resource_add" not in tool_names
                assert "openviking_session_create" not in tool_names
                assert "openviking_fs_mkdir" not in tool_names

                health_result = await session.call_tool("openviking_health", {})
                assert health_result.isError is False
                payload = _extract_payload(health_result)
                assert payload["ok"] is True
                assert isinstance(payload["result"]["healthy"], bool)


@pytest.mark.anyio
async def test_stdio_mutate_exposes_mutate_tools_but_not_admin_tools():
    _ensure_agfs_binary_available()
    with tempfile.TemporaryDirectory(dir=_repo_root()) as data_path:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openviking", "mcp", "--path", data_path, "--access-level", "mutate"],
            cwd=_repo_root(),
        )

        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}
                assert "openviking_resource_add" in tool_names
                assert "openviking_fs_mkdir" in tool_names
                assert "openviking_fs_rm" not in tool_names
                assert "openviking_pack_import" not in tool_names


@pytest.mark.anyio
async def test_stdio_admin_exposes_admin_tools():
    _ensure_agfs_binary_available()
    with tempfile.TemporaryDirectory(dir=_repo_root()) as data_path:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openviking", "mcp", "--path", data_path, "--access-level", "admin"],
            cwd=_repo_root(),
        )

        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}
                assert "openviking_fs_rm" in tool_names
                assert "openviking_pack_import" in tool_names
