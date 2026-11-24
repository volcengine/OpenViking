# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared-resource deletion boundary tests."""

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import NotFoundError


@pytest.mark.asyncio
async def test_delete_resource_http_removes_real_shared_directory(
    client: httpx.AsyncClient,
    service,
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    parent = "viking://resources/project"
    uri = f"{parent}/delete-me"
    await service.fs.mkdir(parent, ctx=ctx)
    await service.fs.mkdir(uri, ctx=ctx)
    await service.viking_fs.write_file(f"{uri}/nested.txt", "present before deletion", ctx=ctx)

    response = await client.delete(
        "/api/v1/resources",
        params={"uri": f"{uri}/", "recursive": True, "wait": True},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["uri"] == uri
    assert result["semantic_root_uri"] == parent
    assert result["semantic_status"] == "complete"
    with pytest.raises(NotFoundError):
        await service.fs.stat(f"{uri}/nested.txt", ctx=ctx)
    assert await service.viking_fs.exists(parent, ctx=ctx)


@pytest.mark.asyncio
async def test_delete_resource_http_rejects_private_resource_before_service(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def unexpected_rm(*args, **kwargs):
        raise AssertionError("private resource must not reach filesystem deletion")

    monkeypatch.setattr(service.fs, "rm", unexpected_rm)

    for uri in ("viking://resources/", "viking://user/alice/resources/private.md"):
        response = await client.delete("/api/v1/resources", params={"uri": uri, "recursive": True})
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "INVALID_ARGUMENT"
        assert (
            error["message"]
            == "Only children of viking://resources/ are allowed for this operation"
        )
