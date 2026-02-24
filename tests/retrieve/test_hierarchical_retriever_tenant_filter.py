# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tenant filter construction tests for hierarchical retriever."""

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


class _DummyStorage:
    pass


def _make_ctx(role: Role) -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", "alice", "helper"), role=role)


def _make_retriever() -> HierarchicalRetriever:
    return HierarchicalRetriever(storage=_DummyStorage(), embedder=None, rerank_config=None)


def test_build_tenant_filter_root_returns_none():
    retriever = _make_retriever()
    ctx = _make_ctx(Role.ROOT)

    assert retriever._build_tenant_filter(ctx) is None


def test_build_tenant_filter_admin_filters_by_account_only():
    retriever = _make_retriever()
    ctx = _make_ctx(Role.ADMIN)

    assert retriever._build_tenant_filter(ctx) == {
        "op": "must",
        "field": "account_id",
        "conds": ["acme"],
    }


def test_build_tenant_filter_user_filters_by_account_and_owner_spaces():
    retriever = _make_retriever()
    ctx = _make_ctx(Role.USER)

    assert retriever._build_tenant_filter(ctx) == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "account_id", "conds": ["acme"]},
            {
                "op": "must",
                "field": "owner_space",
                "conds": [ctx.user.user_space_name(), ctx.user.agent_space_name()],
            },
        ],
    }
