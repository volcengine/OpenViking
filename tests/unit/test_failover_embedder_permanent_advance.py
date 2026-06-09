# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for FailoverEmbedder behavior when permanent errors occur.

In multi-credential mode, FailoverEmbedder advances on permanent errors
(HTTP 400/401/403) by default, since different credentials may resolve to
different upstream resources (e.g. ARK endpoint ids per credential). The
last credential's permanent error still raises AllCredentialsFailedError.
"""

from typing import List

import pytest

from openviking.models.embedder.base import EmbedderBase, EmbedResult, FailoverEmbedder
from openviking.utils.exceptions import AllCredentialsFailedError


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
    """Construct an exception whose message matches PERMANENT_API_ERROR_PATTERNS ('400')."""
    return RuntimeError(
        "Error code: 400 - {'error': {'message': "
        "'The parameter `model` specified in the request are not valid'}}"
    )


def test_permanent_error_on_primary_advances_to_backup():
    """In multi-credential mode a 400 on primary advances to backup automatically."""
    primary = _StubEmbedder("primary", error=_make_400_error())
    backup = _StubEmbedder("backup")

    fe = FailoverEmbedder(
        embedders=[primary, backup],
        credential_ids=["primary", "backup"],
    )

    result = fe.embed("hello")

    assert primary.calls == 1
    assert backup.calls == 1
    assert result.dense_vector == [0.0, 0.0, 0.0, 0.0]


def test_permanent_error_on_all_credentials_raises():
    """All credentials returning permanent errors still surface AllCredentialsFailedError."""
    primary = _StubEmbedder("primary", error=_make_400_error())
    backup = _StubEmbedder("backup", error=_make_400_error())

    fe = FailoverEmbedder(
        embedders=[primary, backup],
        credential_ids=["primary", "backup"],
    )

    with pytest.raises(AllCredentialsFailedError):
        fe.embed("hello")

    assert primary.calls == 1
    assert backup.calls == 1


def test_three_credentials_advance_through_chain():
    """Across 3 credentials, two PERMANENT errors should land on credential #3."""
    cred0 = _StubEmbedder("cred0", error=_make_400_error())
    cred1 = _StubEmbedder("cred1", error=_make_400_error())
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
