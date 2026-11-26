# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.session.history import session_history


@pytest.mark.asyncio
@pytest.mark.parametrize("line_break", ["\n", "\r\n"])
async def test_search_returns_every_line_of_a_literal_match(line_break):
    ctx = object()
    uri = "viking://user/test/sessions/example"
    matched_lines = ["ERROR 42", "retry after 5 seconds"]
    text = line_break.join(["setup", *matched_lines, "done"])
    message = Message(id="message-1", role="user", parts=[TextPart(text)])
    session = SimpleNamespace(ctx=ctx, uri=uri)
    files = {
        f"{uri}/messages.jsonl": json.dumps(message.to_dict()) + "\n",
        f"{uri}/.meta.json": "{}",
    }
    service = SimpleNamespace(
        sessions=SimpleNamespace(open=AsyncMock(return_value=session)),
        viking_fs=SimpleNamespace(
            read_file=AsyncMock(side_effect=lambda path, *, ctx: files[path]),
            ls=AsyncMock(return_value=[]),
        ),
        fs=SimpleNamespace(make_visible=AsyncMock(side_effect=lambda _uri, content, _ctx: content)),
    )
    query = line_break.join(matched_lines)
    search = await session_history(service, ctx, sessions="example", action="search", query=query)

    printed_lines = [line for line in search.text.splitlines() if line.startswith("| ")]
    assert printed_lines == [f"| {line}" for line in matched_lines]

    for line in matched_lines:
        offset = text.index(line)
        assert f"[{search.ids[0]} @{offset}]" in search.text
        assert f"| {line}" in search.text
        read = await session_history(
            service,
            ctx,
            sessions="example",
            action="read",
            id=search.ids[0],
            offset=offset,
            limit=len(line),
        )
        assert read.text.splitlines()[-1] == f"| {line}"

    limited = await session_history(
        service, ctx, sessions="example", action="search", query=query, limit=1
    )
    assert limited.has_more
    assert [line for line in limited.text.splitlines() if line.startswith("| ")] == ["| ERROR 42"]
    assert f"@{text.index(matched_lines[1])}" in limited.text
