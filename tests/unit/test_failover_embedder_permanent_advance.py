# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for FailoverEmbedder behavior when credential-level errors occur.

In multi-credential mode, FailoverEmbedder advances on auth errors (HTTP
401/403) by default, since another credential may carry a valid key /
permission / balance. The last credential's auth error re-raises the original
exception.

Request-level errors (e.g. HTTP 400 parameter errors) fail fast and re-raise
immediately without trying other credentials, since the same request fails on
every credential of the same model.
"""

from typing import List

import pytest

from openviking.models.embedder.base import EmbedderBase, EmbedResult, FailoverEmbedder


class _StubEmbedder(EmbedderBase):
    """Minimal embedder that returns a canned vector or raises a canned error."""

    def __init__(self, name: str, error: Exception | None = None, dim: int = 4):
        super().__init__(model_name=name, config={"provider": "stub"})
        self._error = error
        self._dim = dim
        self.calls = 0

    @property
    def supports_multimodal(self) -> bool:
        return False

    def get_dimension(self) -> int:
        return self._dim

    def embed(self, content, is_query: bool = False) -> EmbedResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return EmbedResult(dense_vector=[0.0] * self._dim)

    def embed_batch(self, contents, is_query: bool = False) -> List[EmbedResult]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return [EmbedResult(dense_vector=[0.0] * self._dim) for _ in contents]

    async def embed_async(self, content, is_query: bool = False) -> EmbedResult:
        return self.embed(content, is_query=is_query)

    async def embed_batch_async(self, contents, is_query: bool = False) -> List[EmbedResult]:
        return self.embed_batch(contents, is_query=is_query)


def _make_400_error() -> Exception:
    """Construct a request-level 400 parameter error (PERMANENT, fail-fast)."""
    return RuntimeError(
        "Error code: 400 - {'error': {'message': "
        "'The parameter `model` specified in the request are not valid'}}"
    )


def _make_401_error() -> Exception:
    """Construct a credential-level 401 auth error (AUTH, advances in multi-credential)."""
    return RuntimeError(
        "Error code: 401 - {'error': {'message': 'Incorrect API key provided'}}"
    )


def test_auth_error_on_primary_advances_to_backup():
    """In multi-credential mode a 401 on primary advances to backup automatically."""
    primary = _StubEmbedder("primary", error=_make_401_error())
    backup = _StubEmbedder("backup")

    fe = FailoverEmbedder(
        embedders=[primary, backup],
        credential_ids=["primary", "backup"],
    )

    result = fe.embed("hello")

    assert primary.calls == 1
    assert backup.calls == 1
    assert result.dense_vector == [0.0, 0.0, 0.0, 0.0]


def test_auth_error_on_all_credentials_reraises_original():
    """All credentials returning auth errors re-raise the original exception (last fails fast)."""
    primary = _StubEmbedder("primary", error=_make_401_error())
    backup = _StubEmbedder("backup", error=_make_401_error())

    fe = FailoverEmbedder(
        embedders=[primary, backup],
        credential_ids=["primary", "backup"],
    )

    with pytest.raises(RuntimeError, match="401"):
        fe.embed("hello")

    assert primary.calls == 1
    assert backup.calls == 1


def test_permanent_400_fails_fast_without_trying_backup():
    """A request-level 400 fails fast: backup must NOT be tried, original is re-raised."""
    primary = _StubEmbedder("primary", error=_make_400_error())
    backup = _StubEmbedder("backup")

    fe = FailoverEmbedder(
        embedders=[primary, backup],
        credential_ids=["primary", "backup"],
    )

    with pytest.raises(RuntimeError, match="400"):
        fe.embed("hello")

    assert primary.calls == 1
    assert backup.calls == 0


def test_three_credentials_advance_through_chain():
    """Across 3 credentials, two AUTH errors should land on credential #3."""
    cred0 = _StubEmbedder("cred0", error=_make_401_error())
    cred1 = _StubEmbedder("cred1", error=_make_401_error())
    cred2 = _StubEmbedder("cred2")

    fe = FailoverEmbedder(
        embedders=[cred0, cred1, cred2],
        credential_ids=["c0", "c1", "c2"],
    )

    result = fe.embed("hello")

    assert cred0.calls == 1
    assert cred1.calls == 1
    assert cred2.calls == 1
    assert result.dense_vector == [0.0] * 4
