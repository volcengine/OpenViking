# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for tag normalization and namespace expansion helpers."""

from openviking.utils.tag_utils import (
    AUTO_TAG_NAMESPACE,
    USER_TAG_NAMESPACE,
    canonicalize_user_tags,
    expand_query_tags,
    extract_context_tags,
    namespace_tags,
    parse_tags,
    serialize_tags,
)


def test_parse_tags_preserves_explicit_namespace():
    assert parse_tags("user:machine-learning;auto:pytorch") == [
        "user:machine-learning",
        "auto:pytorch",
    ]


def test_canonicalize_user_tags_adds_default_namespace():
    assert canonicalize_user_tags("machine-learning;feature-store") == [
        "user:machine-learning",
        "user:feature-store",
    ]


def test_canonicalize_user_tags_rewrites_explicit_auto_namespace():
    assert canonicalize_user_tags("auto:pytorch;feature-store") == [
        "user:pytorch",
        "user:feature-store",
    ]


def test_expand_query_tags_matches_both_known_namespaces_for_bare_input():
    assert expand_query_tags("machine-learning") == [
        "user:machine-learning",
        "auto:machine-learning",
    ]


def test_expand_query_tags_preserves_namespaced_input():
    assert expand_query_tags("auto:pytorch;user:model-training") == [
        "auto:pytorch",
        "user:model-training",
    ]


def test_extract_context_tags_namespaces_auto_generated_terms():
    tags = extract_context_tags(
        "viking://resources/ml/feature-store.md",
        texts=["Feature store guidance for model training systems."],
        inherited_tags=["user:retrieval"],
    )
    assert "user:retrieval" in tags
    assert any(tag.startswith(f"{AUTO_TAG_NAMESPACE}:") for tag in tags if tag != "user:retrieval")


def test_namespace_tags_overrides_existing_prefix():
    assert namespace_tags("user:feature-store;model-training", AUTO_TAG_NAMESPACE) == [
        "auto:feature-store",
        "auto:model-training",
    ]


def test_serialize_tags_preserves_namespaces():
    assert (
        serialize_tags(
            ["user:machine-learning", "auto:feature-store"],
            default_namespace=USER_TAG_NAMESPACE,
        )
        == "user:machine-learning;auto:feature-store"
    )
