# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for delegated GLOBAL scope in ``VikingFS.search``.

``find`` authorizes its targets, rewrites them to the owner's policy scope and
filters hits through the access check. ``search`` must do the same: otherwise a
delegated GLOBAL query aimed at an owner root reaches the retriever without any
policy scope and returns content the owner's GLOBAL_POLICY.md denies.
"""

from __future__ import annotations

import contextvars
from dataclasses import replace
from types import SimpleNamespace

import pytest

from openviking.core.context import ContextLevel
from openviking.core.global_policy import PolicyPathScope, PolicyScopePlan
from openviking.core.retrieval_targets import ResolvedRetrievalTargets
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import DataAccessScope, RequestContext, Role
from openviking.storage.acl import AclAction
from openviking.storage.viking_fs import _semantic
from openviking.storage.viking_fs._access import _AccessMixin
from openviking.storage.viking_fs._semantic import _SemanticMixin
from openviking_cli.retrieve import ContextType
from openviking_cli.session.user_id import UserIdentifier

OWNER_ROOT = "viking://user/bob"
PUBLIC_ROOT = f"{OWNER_ROOT}/resources/public"
PUBLIC_FILE = f"{PUBLIC_ROOT}/handbook.md"
DENIED_FILE = f"{OWNER_ROOT}/private/secret.md"


class _FakeFS(_SemanticMixin, _AccessMixin):
    """Bare retrieval harness with a fake owner policy and no ACL manager."""

    def __init__(self) -> None:
        self.acl_manager = None
        self.rerank_config = None
        self.retrieval_config = None
        self._vlm_resolver = None
        self._bound_ctx = contextvars.ContextVar("search_scope_test_ctx", default=None)
        self.scope_calls: list[tuple[str, bool]] = []
        self.access_calls: list[list[str]] = []

    def authorize_read(self, ctx, targets, *, recursive=True):
        """Derive one owner scope like the real mixin, without the native parser."""
        if ctx.role != Role.GLOBAL:
            return ctx
        owners = {
            target[len("viking://user/") :].split("/")[0]
            for target in targets
            if target.startswith("viking://user/") and target != "viking://user/"
        }
        if not owners or owners == {ctx.user.user_id}:
            return ctx
        owner = next(iter(owners))
        root = f"viking://user/{owner}"
        return replace(
            ctx,
            data_access=DataAccessScope(
                owner_user_id=owner,
                operation=AclAction.READ,
                plan=PolicyScopePlan("allowed", (PolicyPathScope("resources/public", True),), ()),
                policy_hash=None,
                request_root=root,
                requested_targets=tuple(targets),
                effective_targets=(f"{root}/resources/public",),
            ),
        )

    async def _ensure_retrieval_scope(self, uri, ctx):
        self.scope_calls.append((uri, ctx.data_access is not None))

    async def _can_access_many(self, uris, ctx, *, action=AclAction.READ):
        self.access_calls.append(list(uris))
        return {uri: uri == PUBLIC_FILE or uri.startswith(PUBLIC_ROOT + "/") for uri in uris}

    async def abstract(self, uri, ctx=None):
        return ""

    def _get_vector_store(self):
        return object()

    def _get_embedder(self, ctx=None):
        return None


def _hit(uri: str) -> SimpleNamespace:
    return SimpleNamespace(uri=uri, level=ContextLevel.DETAIL, context_type=ContextType.RESOURCE)


def _install_retriever(monkeypatch, hits):
    captured = {}

    async def fake_retrieve(self, typed_query, ctx, **kwargs):
        captured["data_access"] = ctx.data_access
        captured["target_directories"] = list(typed_query.target_directories)
        return SimpleNamespace(matched_contexts=list(hits))

    monkeypatch.setattr(HierarchicalRetriever, "retrieve", fake_retrieve)
    return captured


def _pin_targets(monkeypatch):
    monkeypatch.setattr(
        _semantic,
        "resolve_retrieval_targets",
        lambda target_uri, ctx: ResolvedRetrievalTargets(
            target_directories=[OWNER_ROOT], first_explicit_directory=OWNER_ROOT
        ),
    )


@pytest.mark.asyncio
async def test_search_applies_delegated_owner_scope(monkeypatch):
    """A GLOBAL caller targeting another owner's root only retrieves policy-allowed content."""
    _pin_targets(monkeypatch)
    captured = _install_retriever(monkeypatch, [_hit(PUBLIC_FILE), _hit(DENIED_FILE)])
    fs = _FakeFS()
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.GLOBAL)

    result = await fs.search("handbook", target_uri=OWNER_ROOT, ctx=ctx)

    assert captured["data_access"] is not None
    assert captured["target_directories"] == [PUBLIC_ROOT]
    assert fs.scope_calls == [(PUBLIC_ROOT, True)]
    assert fs.access_calls == [[PUBLIC_FILE, DENIED_FILE]]
    assert [r.uri for r in result.resources] == [PUBLIC_FILE]
    assert result.total == 1


@pytest.mark.asyncio
async def test_search_keeps_scope_for_non_global_callers(monkeypatch):
    """Ordinary callers keep their unrestricted targets and unfiltered hits."""
    _pin_targets(monkeypatch)
    captured = _install_retriever(monkeypatch, [_hit(PUBLIC_FILE), _hit(DENIED_FILE)])
    fs = _FakeFS()
    ctx = RequestContext(user=UserIdentifier("acme", "bob"), role=Role.USER)

    result = await fs.search("handbook", target_uri=OWNER_ROOT, ctx=ctx)

    assert captured["data_access"] is None
    assert captured["target_directories"] == [OWNER_ROOT]
    assert fs.access_calls == []
    assert [r.uri for r in result.resources] == [PUBLIC_FILE, DENIED_FILE]
