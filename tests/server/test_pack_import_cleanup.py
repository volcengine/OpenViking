# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A rejected pack import must not leave a materialized shared upload behind."""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import pack as pack_router
from openviking.server.temp_upload_store import ResolvedTempUpload
from openviking_cli.exceptions import InvalidURIError
from openviking_cli.session.user_id import UserIdentifier


def _context() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)


async def test_import_ovpack_cleans_up_materialized_upload_when_parent_is_invalid(
    tmp_path, monkeypatch
):
    materialized = tmp_path / "ov_shared_upload_pack.ovpack"
    resolved = ResolvedTempUpload(
        mode="shared",
        temp_file_id="shared_2026010100-deadbeef",
        original_filename="pack.ovpack",
        local_path=str(materialized),
    )
    resolve_calls: list[str] = []

    class FakeStore:
        async def resolve_for_consume(self, temp_file_id, ctx):
            # Materializing a shared upload is what creates this local copy.
            resolve_calls.append(temp_file_id)
            materialized.write_bytes(b"ovpack")
            return resolved

    class FakePack:
        async def import_ovpack(self, *args, **kwargs):
            pytest.fail("import must not run when the parent URI is invalid")

    monkeypatch.setattr(
        pack_router, "TempUploadStore", SimpleNamespace(build=lambda config: FakeStore())
    )
    monkeypatch.setattr(pack_router, "get_service", lambda: SimpleNamespace(pack=FakePack()))

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=object())))
    body = pack_router.ImportRequest(temp_file_id=resolved.temp_file_id, parent="")

    with pytest.raises(InvalidURIError):
        await pack_router.import_ovpack.__wrapped__(request, body, _context())

    assert resolve_calls == []
    assert not materialized.exists()
