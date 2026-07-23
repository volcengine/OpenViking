# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for shared internal storage entry-name policies."""

from openviking.storage.internal_names import (
    file_relation_sidecar_path,
    is_relation_sidecar_name,
    is_storage_internal_entry_name,
    is_webdav_reserved_filename,
    relation_table_path,
)


def test_relation_sidecar_name_covers_file_and_directory_relation_tables():
    assert is_relation_sidecar_name(".relations.json")
    assert is_relation_sidecar_name("source.md.relations.json")
    assert not is_relation_sidecar_name("source.relations.json.bak")
    assert not is_relation_sidecar_name("source.md")


def test_relation_sidecar_paths_match_source_type():
    assert file_relation_sidecar_path("/local/account/resources/source.md") == (
        "/local/account/resources/source.md.relations.json"
    )
    assert relation_table_path("/local/account/resources/source.md", is_dir=False) == (
        "/local/account/resources/source.md.relations.json"
    )
    assert relation_table_path("/local/account/resources/source", is_dir=True) == (
        "/local/account/resources/source/.relations.json"
    )


def test_relation_sidecars_are_internal_and_reserved_for_webdav():
    name = "source.md.relations.json"
    assert is_storage_internal_entry_name(name)
    assert is_webdav_reserved_filename(name)
