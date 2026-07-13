# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx
import pytest

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def _ctx(user_id: str, role: str = Role.USER) -> RequestContext:
    return RequestContext(user=UserIdentifier("default", user_id), role=role)


async def _index(service, ctx: RequestContext, *records: tuple[str, str, int]) -> None:
    for record_id, uri, level in records:
        assert await service.vikingdb_manager.upsert(
            {
                "id": record_id,
                "uri": uri,
                "account_id": ctx.account_id,
                "context_type": "resource",
                "level": level,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
            },
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_acl_http_and_operation_levels(
    client: httpx.AsyncClient,
    app,
    service,
):
    owner = _ctx("test_user", Role.ADMIN)
    bob = _ctx("bob")
    private_directory = "viking://user/test_user/resources/acl-project"
    private_file_uri = f"{private_directory}/notes.md"
    directory = "viking://resources/acl-project"
    file_uri = f"{directory}/notes.md"
    await service.viking_fs.mkdir(private_directory, ctx=owner)
    await service.viking_fs.write_file(private_file_uri, "initial", ctx=owner)
    await _index(
        service,
        owner,
        ("acl-project-l0", private_directory, 0),
        ("acl-notes-l2", private_file_uri, 2),
    )
    with pytest.raises(InvalidArgumentError, match="viking://resources"):
        await service.viking_fs.get_acl(private_directory, ctx=owner)
    await service.viking_fs.mv(private_directory, directory, ctx=owner)
    app.dependency_overrides[get_request_context] = lambda: owner

    try:
        response = await client.put(
            "/api/v1/acl",
            json={
                "uri": directory,
                "entries": [{"principal": "user:bob", "level": "viewer"}],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["direct_entries"] == [
            {"principal": "user:bob", "level": "viewer"}
        ]

        assert await service.viking_fs.read_file(file_uri, ctx=bob) == "initial"
        with pytest.raises(PermissionDeniedError):
            await service.viking_fs.write_file(file_uri, "viewer write", ctx=bob)
        with pytest.raises(PermissionDeniedError):
            await service.viking_fs.get_acl(directory, ctx=bob)

        response = await client.post(
            "/api/v1/acl/grant",
            json={"uri": directory, "principal": "user:bob", "level": "manager"},
        )
        assert response.status_code == 200
        await service.viking_fs.rm(file_uri, ctx=bob)

        response = await client.get("/api/v1/acl", params={"uri": directory})
        assert response.status_code == 200
        assert response.json()["result"]["effective_entries"] == [
            {"principal": "user:bob", "level": "manager"}
        ]
    finally:
        app.dependency_overrides.pop(get_request_context, None)


@pytest.mark.asyncio
async def test_recursive_delete_cannot_bypass_child_acl(service):
    admin = _ctx("admin", Role.ADMIN)
    alice = _ctx("alice")
    parent = "viking://resources/acl-recursive-delete"
    protected = f"{parent}/protected"
    protected_file = f"{protected}/notes.md"

    await service.viking_fs.mkdir(protected, ctx=admin)
    await service.viking_fs.write_file(protected_file, "protected", ctx=admin)
    await _index(
        service,
        admin,
        ("acl-delete-parent-l0", parent, 0),
        ("acl-delete-protected-l0", protected, 0),
        ("acl-delete-notes-l2", protected_file, 2),
    )
    await service.viking_fs.set_acl(
        protected, [{"principal": "user:bob", "level": "manager"}], ctx=admin
    )

    with pytest.raises(PermissionDeniedError):
        await service.viking_fs.rm(parent, recursive=True, ctx=alice)
    assert await service.viking_fs.read_file(protected_file, ctx=admin) == "protected"
