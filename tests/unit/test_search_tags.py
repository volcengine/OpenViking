# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.utils.search_tags import (
    AUTO_SEARCH_TAG_NAMESPACE,
    USER_SEARCH_TAG_NAMESPACE,
    expand_query_tags,
    extract_context_tags,
    sanitize_search_tags,
)


def test_expand_query_tags_adds_default_namespaces():
    assert expand_query_tags(["invoice", "user:finance"]) == [
        f"{USER_SEARCH_TAG_NAMESPACE}:invoice",
        f"{AUTO_SEARCH_TAG_NAMESPACE}:invoice",
        f"{USER_SEARCH_TAG_NAMESPACE}:finance",
    ]


def test_sanitize_search_tags_keeps_supported_namespaces_only():
    assert sanitize_search_tags(["user:finance", "auto:invoice", "legacy:value", "finance"]) == [
        "user:finance",
        "auto:invoice",
    ]


def test_extract_context_tags_preserves_user_tags_and_adds_auto_tags():
    tags = extract_context_tags(
        "viking://resources/finance/invoice-guide.md",
        texts=["Invoice workflow for reimbursements"],
        inherited_tags=["user:billing"],
    )

    assert "user:billing" in tags
    assert "auto:finance" in tags
    assert "auto:invoice" in tags
