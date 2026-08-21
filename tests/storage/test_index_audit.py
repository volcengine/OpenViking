# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.core.namespace import owner_fields_for_uri
from openviking.server.identity import RequestContext, Role
from openviking.storage.index_audit import audit_index
from openviking.storage.index_digest import source_digest
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class _FS:
    def __init__(self, values: dict[str, str], failures: set[str] | None = None) -> None:
        self.values = values
        self.failures = failures or set()

    async def exists(self, uri: str, *, ctx) -> bool:
        if uri in self.failures:
            raise OSError("read failed")
        return uri in self.values

    async def read_file(self, uri: str, *, ctx) -> str:
        if uri in self.failures:
            raise OSError("read failed")
        return self.values[uri]


class _VectorStore:
    collection_name = "context"

    def __init__(self, records: list[dict]) -> None:
        self.records = records
        self.write_calls = 0

    async def get_collection_meta(self, *, ctx):
        return {
            "CollectionName": "context",
            "Fields": [{"FieldName": "source_digest", "FieldType": "string"}],
        }

    async def filter(self, *, filter, limit, output_fields, ctx):
        return [record for record in self.records if record.get("uri") == filter.value][:limit]

    async def scroll(self, *, filter, limit, cursor, output_fields, ctx):
        return self.records[:limit], None


def _ctx() -> RequestContext:
    return RequestContext(UserIdentifier("account", "user"), Role.ROOT)


def _record(uri: str, level: int, digest: str, *, owner: str | None = None, suffix: str = ""):
    ctx = _ctx()
    expected_owner = owner_fields_for_uri(uri, ctx=ctx).get("owner_user_id")
    return {
        "id": f"{uri}:{level}:{suffix}",
        "uri": uri,
        "level": level,
        "context_type": "resource",
        "account_id": ctx.account_id,
        "owner_user_id": expected_owner if owner is None else owner,
        "source_digest": digest,
    }


def _config(monkeypatch) -> None:
    monkeypatch.setattr(
        "openviking.storage.index_source.get_openviking_config",
        lambda: SimpleNamespace(
            embedding=SimpleNamespace(text_source="content", max_input_tokens=1000)
        ),
    )


async def test_audit_classifies_six_issue_types_without_writes(monkeypatch) -> None:
    _config(monkeypatch)
    root = "viking://resources/audit"
    directory = f"{root}/dir"
    file_uri = f"{root}/ok.md"
    broken_uri = f"{root}/broken.md"
    fs = _FS(
        {
            f"{root}/.abstract.md": "root abstract",
            f"{directory}/.abstract.md": "dir abstract",
            f"{directory}/.overview.md": "dir overview",
            file_uri: "file body",
        },
        {broken_uri},
    )
    entries = [
        {"uri": directory, "rel_path": "dir", "name": "dir", "isDir": True},
        {"uri": file_uri, "rel_path": "ok.md", "name": "ok.md", "isDir": False, "size": 9},
        {
            "uri": broken_uri,
            "rel_path": "broken.md",
            "name": "broken.md",
            "isDir": False,
            "size": 9,
        },
    ]
    records = [
        _record(root, 1, source_digest("old")),
        _record(directory, 0, source_digest("dir abstract"), owner="wrong"),
        _record(directory, 1, source_digest("dir overview"), suffix="a"),
        _record(directory, 1, source_digest("dir overview"), suffix="b"),
        _record(file_uri, 2, source_digest("file body")),
        _record(f"{root}/gone.md", 2, source_digest("gone")),
        _record(broken_uri, 2, source_digest("unknown")),
    ]
    store = _VectorStore(records)

    result = await audit_index(fs, store, root, entries, _ctx(), limit=100)

    assert {finding["issue_type"] for finding in result["findings"]} == {
        "missing",
        "stale",
        "orphan",
        "metadata_mismatch",
        "duplicate_keys",
        "unverifiable",
    }
    assert not any(
        finding["issue_type"] == "orphan" and finding["uri"] == broken_uri
        for finding in result["findings"]
    )
    assert store.write_calls == 0

    reversed_result = await audit_index(
        fs, _VectorStore(list(reversed(records))), root, entries, _ctx(), limit=100
    )
    assert reversed_result["findings"] == result["findings"]
    assert reversed_result["counts"] == result["counts"]


async def test_legacy_schema_digest_is_unverifiable(monkeypatch) -> None:
    _config(monkeypatch)
    root = "viking://resources/audit"
    fs = _FS({f"{root}/.abstract.md": "text"})
    store = _VectorStore(
        [_record(root, 0, source_digest("old")), _record(root, 1, source_digest("old"))]
    )

    async def legacy_meta(*, ctx):
        return {"Fields": [{"FieldName": "uri", "FieldType": "path"}]}

    store.get_collection_meta = legacy_meta
    result = await audit_index(fs, store, root, [], _ctx())
    assert result["counts"]["stale"] == 0
    assert result["counts"]["unverifiable"] == 2

    planned = await audit_index(fs, store, root, [], _ctx(), generate_repair_plan=True)
    assert "repair_plan" not in planned
    assert planned["repair_plan_error"] == "complete_verifiable_scan_required"


async def test_failed_scroll_never_turns_partial_results_into_orphans(monkeypatch) -> None:
    _config(monkeypatch)
    root = "viking://resources/audit"
    fs = _FS({f"{root}/.abstract.md": "text"})
    store = _VectorStore([_record(f"{root}/gone.md", 2, source_digest("gone"))])

    async def failed_scroll(**kwargs):
        raise OSError("backend unavailable")

    store.scroll = failed_scroll
    result = await audit_index(fs, store, root, [], _ctx())

    assert result["counts"]["orphan"] == 0
    assert any(finding["reason_code"] == "index_scroll_failed" for finding in result["findings"])


async def test_wrong_level_is_metadata_mismatch(monkeypatch) -> None:
    _config(monkeypatch)
    root = "viking://resources/audit"
    fs = _FS({f"{root}/.abstract.md": "text"})
    store = _VectorStore([_record(root, 9, source_digest("text"))])

    result = await audit_index(fs, store, root, [], _ctx(), generate_repair_plan=True)

    wrong_level = next(
        finding
        for finding in result["findings"]
        if finding["reason_code"] == "unexpected_level_for_source"
    )
    assert wrong_level["issue_type"] == "metadata_mismatch"
    action = next(action for action in result["repair_plan"]["actions"] if action["level"] == 9)
    assert action["action"] == "delete"


async def test_plan_requires_complete_verifiable_scan(monkeypatch) -> None:
    _config(monkeypatch)
    root = "viking://resources/audit"
    fs = _FS({f"{root}/.abstract.md": "text"})
    store = _VectorStore([])
    result = await audit_index(
        fs,
        store,
        root,
        [],
        _ctx(),
        generate_repair_plan=True,
    )
    assert result["repair_plan"]["actions"]
    assert "content" not in result["repair_plan"]

    paged = await audit_index(
        fs,
        store,
        root,
        [],
        _ctx(),
        limit=1,
        generate_repair_plan=True,
    )
    assert paged["next_cursor"]
    assert "repair_plan" not in paged
    assert paged["repair_plan_error"] == "complete_verifiable_scan_required"


async def test_cursor_is_bound_to_request(monkeypatch) -> None:
    _config(monkeypatch)
    root = "viking://resources/audit"
    fs = _FS({f"{root}/.abstract.md": "text"})
    store = _VectorStore([])
    first = await audit_index(fs, store, root, [], _ctx(), limit=1)
    assert first["next_cursor"]
    with pytest.raises(InvalidArgumentError):
        await audit_index(
            fs,
            store,
            root,
            [],
            _ctx(),
            issue_types=["stale"],
            limit=1,
            cursor=first["next_cursor"],
        )
