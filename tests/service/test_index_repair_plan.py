# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import ReindexExecutor
from openviking.storage.index_audit import audit_index
from openviking.storage.index_digest import canonical_digest
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class _Locks:
    async def pathlock_acquire_tree(self, path):
        return "lease"

    async def pathlock_release(self, lease):
        return None


class _FS:
    def __init__(self, root: str, text: str) -> None:
        self.root = root
        self.values = {f"{root}/.abstract.md": text}
        self._async_agfs = _Locks()

    def _uri_to_path(self, uri, *, ctx):
        return uri

    async def exists(self, uri, *, ctx):
        return uri == self.root or uri in self.values

    async def read_file(self, uri, *, ctx):
        return self.values[uri]

    async def stat(self, uri, *, ctx, skip_count=False):
        return {"isDir": uri == self.root}

    async def tree(self, uri, **kwargs):
        return []


class _DB:
    collection_name = "context"
    has_queue_manager = True

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.delete_calls = 0
        self.enqueue_calls = 0
        self.meta = {
            "CollectionName": "context",
            "Fields": [{"FieldName": "source_digest", "FieldType": "string"}],
        }

    async def get_collection_meta(self, *, ctx):
        return self.meta

    async def filter(self, *, filter, limit, output_fields, ctx):
        return [record for record in self.records if record["uri"] == filter.value][:limit]

    async def scroll(self, *, filter, limit, cursor, output_fields, ctx):
        return self.records[:limit], None

    async def get_context_by_uri(self, *, uri, level, limit, ctx):
        return [
            record for record in self.records if record["uri"] == uri and record["level"] == level
        ][:limit]

    async def delete(self, ids, *, ctx):
        self.delete_calls += 1
        before = len(self.records)
        self.records = [record for record in self.records if record.get("id") not in ids]
        return before - len(self.records)

    async def enqueue_embedding_msg(self, msg):
        from openviking.telemetry.request_wait_tracker import get_request_wait_tracker

        self.enqueue_calls += 1
        data = dict(msg.context_data)
        data["id"] = f"{data['uri']}:{data['level']}"
        self.records = [
            record
            for record in self.records
            if (record["uri"], record["level"]) != (data["uri"], data["level"])
        ]
        self.records.append(data)
        get_request_wait_tracker().mark_embedding_done(msg.telemetry_id, msg.id)
        return True


def _ctx() -> RequestContext:
    return RequestContext(UserIdentifier("account", "user"), Role.ROOT)


def _config(monkeypatch) -> None:
    monkeypatch.setattr(
        "openviking.storage.index_source.get_openviking_config",
        lambda: SimpleNamespace(
            embedding=SimpleNamespace(text_source="content", max_input_tokens=1000)
        ),
    )


async def _plan(fs: _FS, db: _DB) -> dict:
    report = await audit_index(
        fs,
        db,
        fs.root,
        [],
        _ctx(),
        generate_repair_plan=True,
    )
    return report["repair_plan"]


def test_tampered_plan_is_rejected_before_service_lookup() -> None:
    plan = {
        "plan_version": "index-repair/v1",
        "account_id": "account",
        "root_uri": "viking://resources/audit",
        "collection": {"name": "context", "schema_fingerprint": "x"},
        "root_fingerprint": "x",
        "actions": [],
    }
    plan["plan_digest"] = canonical_digest(plan)
    plan["root_uri"] = "viking://resources/tampered"
    with pytest.raises(InvalidArgumentError, match="digest mismatch"):
        ReindexExecutor._validate_repair_plan_envelope(plan, _ctx())


async def test_stale_plan_is_rejected_before_first_write(monkeypatch) -> None:
    _config(monkeypatch)
    fs = _FS("viking://resources/audit", "before")
    db = _DB()
    plan = await _plan(fs, db)
    fs.values[f"{fs.root}/.abstract.md"] = "after"
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(viking_fs=fs, vikingdb_manager=db),
    )
    with pytest.raises(FailedPreconditionError):
        await ReindexExecutor().apply_repair_plan(
            plan=plan,
            wait=True,
            dry_run=False,
            ctx=_ctx(),
        )
    assert db.delete_calls == 0
    assert db.enqueue_calls == 0


async def test_apply_converges_and_second_apply_is_noop(monkeypatch) -> None:
    _config(monkeypatch)
    fs = _FS("viking://resources/audit", "text")
    db = _DB()
    plan = await _plan(fs, db)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(viking_fs=fs, vikingdb_manager=db),
    )
    first = await ReindexExecutor().apply_repair_plan(
        plan=plan,
        wait=True,
        dry_run=False,
        ctx=_ctx(),
    )
    writes = db.enqueue_calls
    second = await ReindexExecutor().apply_repair_plan(
        plan=plan,
        wait=True,
        dry_run=False,
        ctx=_ctx(),
    )
    assert first["status"] == "completed"
    assert second["status"] == "already_converged"
    assert db.enqueue_calls == writes


async def test_dry_run_has_zero_writes(monkeypatch) -> None:
    _config(monkeypatch)
    fs = _FS("viking://resources/audit", "text")
    db = _DB()
    plan = await _plan(fs, db)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(viking_fs=fs, vikingdb_manager=db),
    )
    result = await ReindexExecutor().apply_repair_plan(
        plan=plan,
        wait=True,
        dry_run=True,
        ctx=_ctx(),
    )
    assert result["status"] == "dry_run"
    assert db.delete_calls == 0
    assert db.enqueue_calls == 0
