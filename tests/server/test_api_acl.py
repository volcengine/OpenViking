# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx
import pytest

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def _ctx(user_id: str, role: str = Role.USER) -> RequestContext:
    return RequestContext(user=UserIdentifier("default", user_id), role=role)


@pytest.mark.asyncio
async def test_acl_http_crud_and_operation_levels(
    client: httpx.AsyncClient,
    app,
    service,
):
    owner = _ctx("test_user")
    bob = _ctx("bob")
    root = _ctx("root", Role.ROOT)
    directory = "viking://user/test_user/resources/acl-project"
    file_uri = f"{directory}/notes.md"
    await service.viking_fs.mkdir(directory, ctx=owner)
    await service.viking_fs.write_file(file_uri, "initial", ctx=owner)
    for record_id, uri, level in (
        ("acl-project-l0", directory, 0),
        ("acl-notes-l2", file_uri, 2),
    ):
        await service.vikingdb_manager.upsert(
            {
                "id": record_id,
                "uri": uri,
                "account_id": owner.account_id,
                "context_type": "resource",
                "level": level,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
            },
            ctx=owner,
        )
    with pytest.raises(PermissionDeniedError):
        await service.viking_fs.read_file(file_uri, ctx=root)
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
            await service.viking_fs.read_file(file_uri, ctx=root)
        with pytest.raises(PermissionDeniedError):
            await service.viking_fs.write_file(file_uri, "viewer write", ctx=bob)
        with pytest.raises(PermissionDeniedError):
            await service.fs.set_tags(file_uri, ["access=viewer"], "replace", False, ctx=bob)
        with pytest.raises(PermissionDeniedError):
            await service.viking_fs.get_acl(directory, ctx=bob)

        response = await client.post(
            "/api/v1/acl/grant",
            json={"uri": directory, "principal": "user:bob", "level": "editor"},
        )
        assert response.status_code == 200
        await service.viking_fs.write_file(file_uri, "editor write", ctx=bob)
        tags_result = await service.fs.set_tags(
            file_uri, ["access=editor"], "replace", False, ctx=bob
        )
        assert tags_result["tags_updated"] is True
        with pytest.raises(PermissionDeniedError):
            await service.viking_fs.rm(file_uri, ctx=bob)
        with pytest.raises(PermissionDeniedError):
            await service.viking_fs.get_acl(directory, ctx=bob)

        response = await client.post(
            "/api/v1/acl/grant",
            json={"uri": directory, "principal": "user:bob", "level": "manager"},
        )
        assert response.status_code == 200
        report = await service.viking_fs.get_acl(directory, ctx=bob)
        assert report["direct_entries"] == [
            {"principal": "user:bob", "level": "manager"}
        ]
        await service.viking_fs.grant_acl(directory, "user:carol", "viewer", ctx=bob)
        await service.viking_fs.revoke_acl(directory, "user:carol", ctx=bob)
        await service.viking_fs.rm(file_uri, ctx=bob)

        response = await client.get("/api/v1/acl", params={"uri": directory})
        assert response.status_code == 200
        assert response.json()["result"]["effective_entries"] == [
            {"principal": "user:bob", "level": "manager"}
        ]

        response = await client.post(
            "/api/v1/acl/revoke",
            json={"uri": directory, "principal": "user:bob"},
        )
        assert response.status_code == 200
        assert response.json()["result"]["direct_entries"] == []

        response = await client.delete("/api/v1/acl", params={"uri": directory})
        assert response.status_code == 200
        assert response.json()["result"]["acl_enabled"] is False
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
    for record_id, uri, level in (
        ("acl-delete-parent-l0", parent, 0),
        ("acl-delete-protected-l0", protected, 0),
        ("acl-delete-notes-l2", protected_file, 2),
    ):
        await service.vikingdb_manager.upsert(
            {
                "id": record_id,
                "uri": uri,
                "account_id": admin.account_id,
                "context_type": "resource",
                "level": level,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
            },
            ctx=admin,
        )
    await service.viking_fs.set_acl(
        protected, [{"principal": "user:bob", "level": "manager"}], ctx=admin
    )

    with pytest.raises(PermissionDeniedError):
        await service.viking_fs.rm(parent, recursive=True, ctx=alice)
    assert await service.viking_fs.read_file(protected_file, ctx=admin) == "protected"

    await service.viking_fs.rm(parent, recursive=True, ctx=admin)
