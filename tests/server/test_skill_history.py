# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace

from openviking.server.skill_history import record_skill_change
from openviking_cli.exceptions import NotFoundError


class _MemoryFS:
    def __init__(self):
        self.files = {}

    async def read(self, uri, ctx=None):
        try:
            return self.files[uri]
        except KeyError as exc:
            raise NotFoundError(uri, "file") from exc

    async def write_file(self, uri, content, ctx=None):
        self.files[uri] = content


def _ctx(user_id):
    return SimpleNamespace(user=SimpleNamespace(user_id=user_id))


async def test_owner_is_not_replaced_by_a_later_actor():
    fs = _MemoryFS()
    root_uri = "viking://agent/skills/example"
    created = await record_skill_change(
        fs,
        _ctx("uploader"),
        root_uri,
        operation="create",
        request_id="create-request",
        before_hash=None,
        after_hash="a" * 64,
    )
    updated = await record_skill_change(
        fs,
        _ctx("editor"),
        root_uri,
        operation="update",
        request_id="update-request",
        before_hash="a" * 64,
        after_hash="b" * 64,
    )

    assert updated["owner_user_id"] == created["owner_user_id"] == "uploader"
    assert updated["created_by"] == created["created_by"] == "uploader"
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_by"] == "editor"
    assert [event["actor_user_id"] for event in updated["events"]] == ["uploader", "editor"]
    persisted = json.loads(next(iter(fs.files.values())))
    assert persisted["events"] == updated["events"]
