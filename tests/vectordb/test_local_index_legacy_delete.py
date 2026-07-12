# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking.storage.vectordb.index import local_index as local_index_module
from openviking.storage.vectordb.index.local_index import LocalIndex
from openviking.storage.vectordb.store.data import DeltaRecord


class _JsonFieldConverter:
    def convert_fields_for_index(self, fields_json: str) -> str:
        return json.dumps(json.loads(fields_json), separators=(",", ":"))


class _RecordingEngineProxy:
    def __init__(self):
        self.deleted = []
        self.upserted = []

    def delete_data(self, delta_list):
        self.deleted.extend(delta_list)

    def upsert_data(self, delta_list):
        self.upserted.extend(delta_list)


def _make_index() -> tuple[LocalIndex, _RecordingEngineProxy]:
    index = LocalIndex.__new__(LocalIndex)
    proxy = _RecordingEngineProxy()
    index.field_type_converter = _JsonFieldConverter()
    index.dense_search = None
    index.engine_proxy = proxy
    return index, proxy


def test_delete_legacy_record_ignores_unparseable_old_fields(monkeypatch):
    index, proxy = _make_index()
    warnings = []
    monkeypatch.setattr(
        local_index_module.logger,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )
    record = DeltaRecord(
        type=DeltaRecord.Type.DELETE,
        label=41,
        old_fields='{"truncated":',
    )

    index.delete_data([record])

    assert len(proxy.deleted) == 1
    assert proxy.deleted[0].label == 41
    assert proxy.deleted[0].old_fields == ""
    assert warnings == [
        "Ignoring unparseable old_fields while deleting legacy record label=41; "
        "scalar-index cleanup will be best-effort"
    ]


def test_upsert_legacy_record_keeps_invalid_old_fields_fail_closed():
    index, proxy = _make_index()
    record = DeltaRecord(
        type=DeltaRecord.Type.UPSERT,
        label=41,
        fields='{"current":true}',
        old_fields='{"truncated":',
    )

    with pytest.raises(json.JSONDecodeError):
        index.upsert_data([record])

    assert proxy.upserted == []


def test_delete_valid_old_fields_still_converts_them():
    index, proxy = _make_index()
    record = DeltaRecord(
        type=DeltaRecord.Type.DELETE,
        label=42,
        old_fields='{ "name": "healthy" }',
    )

    index.delete_data([record])

    assert proxy.deleted[0].old_fields == '{"name":"healthy"}'
