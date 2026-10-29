# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for system namespace constants and processing policy."""

from types import SimpleNamespace

import pytest

from openviking.core.namespace_policy import (
    SYSTEM_FIXED_DIRECTORIES,
    SYSTEM_MEMORY_CUSTOM_ROOT,
    SYSTEM_OFFICIAL_ASSET_ROOTS,
    NamespaceProcessingMode,
    is_system_custom_asset_uri,
    is_system_fixed_directory_uri,
    is_system_uri,
    namespace_policy_for_uri,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


def test_system_namespace_uses_file_only_policy():
    policy = namespace_policy_for_uri("viking://system/compile/example-skills/demo")

    assert policy.processing_mode is NamespaceProcessingMode.FILE_ONLY
    assert policy.supports_acl is False
    assert policy.supports_tags is False
    assert policy.supports_reindex is False


@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/project",
        "viking://user/alice/memories/preferences",
        "viking://agent/skills/demo",
    ],
)
def test_existing_namespaces_keep_indexed_policy(uri: str):
    policy = namespace_policy_for_uri(uri)

    assert policy.processing_mode is NamespaceProcessingMode.INDEXED
    assert policy.supports_acl is True
    assert policy.supports_tags is True
    assert policy.supports_reindex is True


def test_system_uri_detection_matches_complete_path_segments():
    assert is_system_uri("viking://system")
    assert is_system_uri("viking://system/")
    assert is_system_uri("viking://system/memory?level=1")
    assert not is_system_uri("viking://resources/system")
    assert not is_system_uri("viking://systematic")
    assert not is_system_uri("viking://resources/system-foo")


def test_fixed_and_official_system_roots_are_explicit():
    assert "viking://system" in SYSTEM_FIXED_DIRECTORIES
    assert SYSTEM_MEMORY_CUSTOM_ROOT in SYSTEM_FIXED_DIRECTORIES
    assert SYSTEM_OFFICIAL_ASSET_ROOTS == {
        "viking://system/compile/example-skills",
        "viking://system/memory/memory_templates/builtin",
    }
    assert is_system_fixed_directory_uri("viking://system/memory/memory_templates/custom/")
    assert not is_system_fixed_directory_uri(
        "viking://system/memory/memory_templates/custom/team.yaml"
    )
    assert not is_system_fixed_directory_uri("viking://system/memory-templates")


def test_custom_asset_detection_excludes_fixed_custom_root():
    assert not is_system_custom_asset_uri(SYSTEM_MEMORY_CUSTOM_ROOT)
    assert is_system_custom_asset_uri(f"{SYSTEM_MEMORY_CUSTOM_ROOT}/team.yaml")
    assert is_system_custom_asset_uri(f"{SYSTEM_MEMORY_CUSTOM_ROOT}/nested/team.yaml")
    assert not is_system_custom_asset_uri(
        "viking://system/memory/memory_templates/customized/team.yaml"
    )


def test_system_uri_storage_mapping_is_isolated_by_account():
    fs = VikingFS(agfs=SimpleNamespace())
    uri = "viking://system/memory/memory_templates/custom/team.yaml"

    for account_id in ("account-a", "account-b"):
        ctx = RequestContext(
            user=UserIdentifier(account_id=account_id, user_id="admin"),
            role=Role.ADMIN,
        )
        path = f"/local/{account_id}/system/memory/memory_templates/custom/team.yaml"
        assert fs._uri_to_path(uri, ctx=ctx) == path
        assert fs._path_to_uri(path, ctx=ctx) == uri
