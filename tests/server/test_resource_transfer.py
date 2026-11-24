# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared-resource copy and move boundary tests."""

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import NotFoundError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "from_uri", "to_uri"),
    [
        ("copy", "viking://resources", "viking://resources/copy"),
        ("move", "viking://user/alice/resources/private", "viking://resources/copy"),
        ("move", "viking://resources/source", "viking://resources"),
        ("copy", "viking://resources/source", "viking://agent/tools/copy"),
        ("copy", "viking://resources/source", "viking://user/alice/resources/private"),
        ("copy", "viking://resources/source", "viking://resources-other/copy"),
        ("move", "viking://resources/../agent/tools/source", "viking://resources/copy"),
        ("copy", "viking://resources/source", "viking://resources/%2e%2e/agent/tools/copy"),
    ],
)
async def test_resource_transfer_rejects_non_shared_source_or_target(
    client, service, operation, from_uri, to_uri, monkeypatch
):
    async def unexpected_transfer(*args, **kwargs):
        raise AssertionError("rejected URI must not reach filesystem transfer")

    monkeypatch.setattr(service.fs, "cp", unexpected_transfer)
    monkeypatch.setattr(service.fs, "mv", unexpected_transfer)
    verb = "cp" if operation == "copy" else "mv"
    payload = {"from_uri": from_uri, "to_uri": to_uri}
    if operation == "copy":
        payload["recursive"] = True
    response = await client.post(f"/api/v1/resources/{verb}", json=payload)
    assert response.status_code == 400, response.text
    expected_code = (
        "INVALID_URI" if to_uri.startswith("viking://resources-other/") else "INVALID_ARGUMENT"
    )
    assert response.json()["error"]["code"] == expected_code


@pytest.mark.asyncio
async def test_resource_transfer_http_copy_and_move_preserves_file_contents(
    client: httpx.AsyncClient,
    service,
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    source = "viking://resources/transfer-source"
    copied = "viking://resources/transfer-copy"
    moved = "viking://resources/transfer-renamed"
    child = "nested/readme.md"
    content = "# Resource transfer\n\nKeep this file intact."
    await service.viking_fs.write_file(f"{source}/{child}", content, ctx=ctx)

    copy_response = await client.post(
        "/api/v1/resources/cp",
        json={"from_uri": f"{source}/", "to_uri": f"{copied}/", "recursive": True},
    )
    assert copy_response.status_code == 200, copy_response.text
    assert await service.fs.read(f"{source}/{child}", ctx=ctx) == content
    assert await service.fs.read(f"{copied}/{child}", ctx=ctx) == content

    move_response = await client.post(
        "/api/v1/resources/mv",
        json={"from_uri": copied, "to_uri": moved},
    )
    assert move_response.status_code == 200, move_response.text
    with pytest.raises(NotFoundError):
        await service.fs.stat(copied, ctx=ctx)
    assert await service.fs.read(f"{moved}/{child}", ctx=ctx) == content
    assert await service.fs.read(f"{source}/{child}", ctx=ctx) == content
