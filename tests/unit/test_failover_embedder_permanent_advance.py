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

import time
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


def test_auth_error_on_all_credentials_raises_aggregated():
    """When every credential fails with auth, the whole ring is tried then
    AllCredentialsFailedError is raised aggregating each failure."""
    primary = _StubEmbedder("primary", error=_make_401_error())
    backup = _StubEmbedder("backup", error=_make_401_error())

    fe = FailoverEmbedder(
        embedders=[primary, backup],
        credential_ids=["primary", "backup"],
    )

    with pytest.raises(AllCredentialsFailedError) as excinfo:
        fe.embed("hello")

    assert primary.calls == 1
    assert backup.calls == 1
    # The aggregated error records both failing credentials.
    assert len(excinfo.value.errors) == 2


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


def test_more_than_ten_credentials_all_tried():
    """With >10 credentials, every one is tried (no global retry cap cuts it short).

    Regression for the removed ``total_max_retries`` cap: exhaustion is decided
    solely by reaching the end of the credential chain.
    """
    n = 15
    failing = [_StubEmbedder(f"cred{i}", error=_make_401_error()) for i in range(n - 1)]
    last = _StubEmbedder(f"cred{n - 1}")
    embedders = failing + [last]

    fe = FailoverEmbedder(
        embedders=embedders,
        credential_ids=[f"c{i}" for i in range(n)],
    )

    result = fe.embed("hello")

    # Every failing credential was attempted exactly once, then the last succeeded.
    assert all(e.calls == 1 for e in failing)
    assert last.calls == 1
    assert result.dense_vector == [0.0] * 4


def test_ring_wraps_when_active_is_last_and_unavailable():
    """If the active credential is the last one and it is down, the ring wraps
    around to earlier credentials within the same request (fast failover)."""
    good = _StubEmbedder("good")  # idx 0, healthy
    down = _StubEmbedder("down", error=_make_401_error())  # idx 1, unavailable

    fe = FailoverEmbedder(embedders=[good, down], credential_ids=["good", "down"])

    # Force the active credential to the last (unavailable) one. Bump the
    # switch timestamp so maybe_failback() does not immediately retreat to 0
    # before we even try idx 1 (which is what we want to exercise here).
    fe._switcher._active_idx = 1
    fe._switcher._last_switch_time = time.monotonic()

    result = fe.embed("hello")

    # Started at idx 1 (down) -> failed -> wrapped to idx 0 (good) -> success.
    assert down.calls == 1
    assert good.calls == 1
    assert result.dense_vector == [0.0] * 4


def test_fast_failover_commits_new_active_index():
    """A credential that serves the request after the active one was down
    becomes the new active credential (fast failover, choice (a))."""
    good = _StubEmbedder("good")  # idx 0
    down = _StubEmbedder("down", error=_make_401_error())  # idx 1

    fe = FailoverEmbedder(embedders=[good, down], credential_ids=["good", "down"])
    fe._switcher._active_idx = 1
    fe._switcher._last_switch_time = time.monotonic()

    fe.embed("hello")

    # active index committed to the credential that actually worked (idx 0).
    assert fe._switcher.get_active_index() == 0
    # A subsequent request now starts directly at idx 0 without touching idx 1.
    good.calls = 0
    down.calls = 0
    fe.embed("hello again")
    assert good.calls == 1
    assert down.calls == 0


def test_content_safety_does_not_cycle_the_ring():
    """Request-level content-safety errors fail fast and must not try others."""
    cs = _StubEmbedder(
        "cs", error=RuntimeError("content_filter: sensitive content detected")
    )
    backup = _StubEmbedder("backup")

    fe = FailoverEmbedder(embedders=[cs, backup], credential_ids=["cs", "backup"])

    with pytest.raises(RuntimeError, match="content_filter"):
        fe.embed("hello")

    assert cs.calls == 1
    assert backup.calls == 0
