# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _DummyAgfs:
    pass


def _default_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


@pytest.mark.asyncio
async def test_missing_record_id_explains_index_or_deletion_state(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(fs, "_get_vector_store", lambda: None)
    record_id = "0123456789abcdef0123456789abcdef"

    with pytest.raises(NotFoundError) as exc_info:
        await fs.resolve_uri(record_id, ctx=_default_ctx())

    error = exc_info.value
    assert "not have been indexed yet" in error.message
    assert "may have been deleted" in error.message
    assert error.details["reason"] == (
        "The data may not have been indexed yet or may have been deleted"
    )
