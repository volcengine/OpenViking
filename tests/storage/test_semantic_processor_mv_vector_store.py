from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, In
from openviking_cli.session.user_id import UserIdentifier


class _FakeVectorStore:
    def __init__(self, records: List[Dict[str, Any]]):
        self.records = list(records)
        self.deleted_ids: List[str] = []

    async def update_uri_mapping(
        self,
        *,
        ctx: RequestContext,
        uri: str,
        new_uri: str,
        new_parent_uri: str,
        levels: Optional[List[int]] = None,
    ) -> bool:
        def seed_uri_for_id(target_uri: str, level: int) -> str:
            if level == 0:
                return (
                    target_uri
                    if target_uri.endswith("/.abstract.md")
                    else f"{target_uri}/.abstract.md"
                )
            if level == 1:
                return (
                    target_uri
                    if target_uri.endswith("/.overview.md")
                    else f"{target_uri}/.overview.md"
                )
            return target_uri

        touched = False
        ids_to_delete: List[str] = []
        for record in list(self.records):
            if record.get("account_id") != ctx.account_id:
                continue
            if record.get("uri") != uri:
                continue
            try:
                level = int(record.get("level", 2))
            except (TypeError, ValueError):
                level = 2
            if levels is not None and level not in set(levels):
                continue

            seed_uri = seed_uri_for_id(new_uri, level)
            new_id = hashlib.md5(f"{ctx.account_id}:{seed_uri}".encode("utf-8")).hexdigest()
            new_record = dict(record)
            new_record["id"] = new_id
            new_record["uri"] = new_uri
            new_record["parent_uri"] = new_parent_uri
            self.records.append(new_record)
            touched = True

            old_id = record.get("id")
            if old_id and old_id != new_id:
                ids_to_delete.append(old_id)

        if ids_to_delete:
            await self.delete(list(set(ids_to_delete)), ctx=ctx)

        return touched

    async def filter(self, *, filter=None, limit: int = 100, ctx: RequestContext):
        conds = []
        if filter is not None:
            if isinstance(filter, And):
                conds = list(filter.conds)
            else:
                conds = [filter]

        uri: Optional[str] = None
        account_id: Optional[str] = None
        owner_space: Optional[str] = None
        levels: Optional[List[int]] = None

        for cond in conds:
            if isinstance(cond, Eq) and cond.field == "uri":
                uri = cond.value
            elif isinstance(cond, Eq) and cond.field == "account_id":
                account_id = cond.value
            elif isinstance(cond, Eq) and cond.field == "owner_space":
                owner_space = cond.value
            elif isinstance(cond, In) and cond.field == "level":
                levels = [int(v) for v in cond.values]

        matched = [
            r
            for r in self.records
            if (uri is None or r.get("uri") == uri)
            and (account_id is None or r.get("account_id") == account_id)
            and (owner_space is None or r.get("owner_space") == owner_space)
            and (levels is None or int(r.get("level", 2)) in levels)
        ]
        return matched[:limit]

    async def delete(self, ids: List[str], *, ctx: RequestContext) -> int:
        id_set = set(ids)
        self.deleted_ids.extend(ids)
        self.records = [r for r in self.records if r.get("id") not in id_set]
        return len(ids)


class _NoopLockContext:
    def __init__(self, *_args, **_kwargs):
        return None

    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_mv_vector_store_moves_records(monkeypatch):
    from openviking.storage.viking_fs import VikingFS

    ctx = RequestContext(user=UserIdentifier("acc", "user", "agent"), role=Role.ROOT)
    old_uri = "viking://resources/a"
    new_uri = "viking://resources/b"

    store = _FakeVectorStore(
        [
            {"id": "l0", "uri": old_uri, "level": 0, "account_id": ctx.account_id, "owner_space": ""},
            {"id": "l1", "uri": old_uri, "level": 1, "account_id": ctx.account_id, "owner_space": ""},
            {"id": "l2", "uri": old_uri, "level": 2, "account_id": ctx.account_id, "owner_space": ""},
            {
                "id": "child-l0",
                "uri": f"{old_uri}/x",
                "level": 0,
                "account_id": ctx.account_id,
                "owner_space": "",
            },
        ]
    )

    class _FakeAGFS:
        def rm(self, _path, recursive: bool = False):
            return None

    class _FakeVikingFS(VikingFS):
        def __init__(self):
            super().__init__(agfs=_FakeAGFS(), vector_store=store)

        def _uri_to_path(self, uri, ctx=None):
            return f"/mock/{uri.replace('viking://', '')}"

        async def stat(self, uri, ctx=None):
            return {"isDir": True}

        def _ensure_access(self, uri, ctx):
            return None

    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: None)
    monkeypatch.setattr("openviking.storage.transaction.LockContext", _NoopLockContext)

    fs = _FakeVikingFS()
    await fs._mv_vector_store_l0_l1(old_uri, new_uri, ctx=ctx)

    assert {r["id"] for r in store.records if r.get("uri") == old_uri} == {"l2"}
    assert {r["id"] for r in store.records if r.get("uri") == f"{old_uri}/x"} == {"child-l0"}
    assert {int(r["level"]) for r in store.records if r.get("uri") == new_uri} == {0, 1}
    assert set(store.deleted_ids) == {"l0", "l1"}


@pytest.mark.asyncio
async def test_mv_vector_store_requires_directories(monkeypatch):
    from openviking.storage.viking_fs import VikingFS

    ctx = RequestContext(user=UserIdentifier("acc", "user", "agent"), role=Role.ROOT)
    old_uri = "viking://resources/a"
    new_uri = "viking://resources/b"

    store = _FakeVectorStore([])

    class _FakeAGFS:
        def rm(self, _path, recursive: bool = False):
            return None

    class _FakeVikingFS(VikingFS):
        def __init__(self):
            super().__init__(agfs=_FakeAGFS(), vector_store=store)

        def _uri_to_path(self, uri, ctx=None):
            return f"/mock/{uri.replace('viking://', '')}"

        async def stat(self, uri, ctx=None):
            return {"isDir": uri == old_uri}

        def _ensure_access(self, uri, ctx):
            return None

    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: None)
    monkeypatch.setattr("openviking.storage.transaction.LockContext", _NoopLockContext)

    fs = _FakeVikingFS()
    with pytest.raises(ValueError):
        await fs._mv_vector_store_l0_l1(old_uri, new_uri, ctx=ctx)
