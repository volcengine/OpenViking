# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import pytest

from openviking.storage.vectordb.project.project_group import get_or_create_project_group


def test_persistent_group_lifecycle(tmp_path):
    group = get_or_create_project_group(str(tmp_path))
    try:
        assert group.has_project("default")
        project = group.create_project("analytics")
        with pytest.raises(ValueError, match="analytics already exists"):
            group.create_project("analytics")
        collection = project.create_collection(
            "events",
            {"Fields": [{"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True}]},
        )
        collection.upsert_data([{"id": 42}])
    finally:
        group.close()

    reopened = get_or_create_project_group(str(tmp_path))
    try:
        project = reopened.get_project("analytics")
        assert project is not None
        collection = project.get_collection("events")
        assert collection is not None
        assert [item.id for item in collection.fetch_data([42]).items] == [42]

        reopened.delete_project("analytics")
        assert not reopened.has_project("analytics")
        assert reopened.has_project("default")
    finally:
        reopened.close()

    after_delete = get_or_create_project_group(str(tmp_path))
    try:
        project = after_delete.get_or_create_project("analytics")
        assert project.get_collection("events") is None
    finally:
        after_delete.close()
