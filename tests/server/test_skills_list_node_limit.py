# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Skill listing must never exceed the requested per-root ``node_limit``.

The router fetches one extra entry so the internal ``.skill-history``
directory cannot consume a caller's quota.  Roots without that directory
(legacy/restored installations) must still be capped.
"""

from types import SimpleNamespace

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.skills import _list_skills_from_root
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

_SKILLS_ROOT = "viking://user/test/skills"


class _FakeFS:
    def __init__(self, entries, skill_roots):
        self._entries = entries
        self._skill_roots = skill_roots
        self.requested_node_limits = []

    async def ls(self, uri, ctx=None, output=None, abs_limit=None, node_limit=None):
        self.requested_node_limits.append(node_limit)
        return SimpleNamespace(entries=self._entries[:node_limit])

    async def stat(self, uri, ctx=None, skip_count=False):
        root_uri = uri[: -len("/SKILL.md")]
        if root_uri in self._skill_roots:
            return {"isDir": False, "size": 10}
        raise NotFoundError(uri)

    async def read(self, uri, ctx=None):
        raise NotFoundError(uri)


def _entry(name: str) -> dict:
    return {"name": name, "uri": f"{_SKILLS_ROOT}/{name}", "isDir": True, "abstract": ""}


async def test_list_skills_from_root_caps_legacy_root_without_history():
    entries = [_entry("legacy-a"), _entry("legacy-b")]
    fs = _FakeFS(entries, {entry["uri"] for entry in entries})
    service = SimpleNamespace(fs=fs)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    skills = await _list_skills_from_root(service, ctx, _SKILLS_ROOT, 1)

    assert [skill["name"] for skill in skills] == ["legacy-a"]
    # The extra probe entry is still fetched so a hidden history directory
    # cannot consume the caller's quota.
    assert fs.requested_node_limits == [2]
