# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the default-off ``expires_at`` echo-back hook.

The read-path extension point must do
zero TTL work when no TTL implementation is enabled, and must attach exactly
what a registered adapter computes (string or ``None``) without fabricating a
field for URIs the adapter does not cover. A misbehaving adapter must never
break ``stat`` / ``ls`` / ``tree``.
"""

from __future__ import annotations

import pytest

from openviking.ttl import wiring


@pytest.fixture(autouse=True)
def _clear_adapter():
    wiring.set_ttl_adapter(None)
    try:
        yield
    finally:
        wiring.set_ttl_adapter(None)


class _Adapter:
    def __init__(self, mapping, calls=None):
        self._mapping = mapping
        self.calls = calls if calls is not None else []

    def supports_config(self) -> bool:
        return True

    async def resolve_expires_at(self, uris, ctx):  # noqa: ANN001
        self.calls.append(list(uris))
        return {u: self._mapping[u] for u in uris if u in self._mapping}


async def test_attach_noop_without_adapter_leaves_entries_untouched():
    entries = [{"uri": "viking://user/u/sessions/s1"}]
    await wiring.attach_expires_at(entries, None)
    assert entries == [{"uri": "viking://user/u/sessions/s1"}]
    assert "expires_at" not in entries[0]


async def test_attach_attaches_value_and_null_only_for_covered_uris():
    calls = []
    wiring.set_ttl_adapter(
        _Adapter(
            {
                "viking://user/u/sessions/s1": "2030-01-31T00:00:00Z",
                "viking://user/u/sessions/s2": None,
            },
            calls,
        )
    )
    entries = [
        {"uri": "viking://user/u/sessions/s1"},
        {"uri": "viking://user/u/sessions/s2"},
        {"uri": "viking://user/u/resources/r1"},  # not covered by the adapter
        {"name": "no-uri-entry"},
    ]
    await wiring.attach_expires_at(entries, None)

    assert entries[0]["expires_at"] == "2030-01-31T00:00:00Z"
    assert entries[1]["expires_at"] is None
    # Uncovered URI and the URI-less entry are left exactly as they were.
    assert "expires_at" not in entries[2]
    assert "expires_at" not in entries[3]
    # Only the deduped real URIs are passed through, once.
    assert calls == [
        [
            "viking://user/u/sessions/s1",
            "viking://user/u/sessions/s2",
            "viking://user/u/resources/r1",
        ]
    ]


async def test_attach_dedupes_and_drops_blank_uris():
    calls = []
    wiring.set_ttl_adapter(_Adapter({"viking://user/u/sessions/s1": "2030-01-31T00:00:00Z"}, calls))
    entries = [
        {"uri": "viking://user/u/sessions/s1"},
        {"uri": "viking://user/u/sessions/s1"},
        {"uri": ""},
        {"uri": "viking://x"},
    ]
    await wiring.attach_expires_at(entries, None)
    assert entries[0]["expires_at"] == "2030-01-31T00:00:00Z"
    assert entries[1]["expires_at"] == "2030-01-31T00:00:00Z"
    assert "expires_at" not in entries[2]
    assert "expires_at" not in entries[3]
    assert calls == [["viking://user/u/sessions/s1", "viking://x"]]


class _BoomAdapter:
    def supports_config(self) -> bool:
        return True

    async def resolve_expires_at(self, uris, ctx):  # noqa: ANN001
        raise RuntimeError("redis down")


async def test_adapter_failure_degrades_to_empty_without_raising():
    wiring.set_ttl_adapter(_BoomAdapter())
    entries = [{"uri": "viking://user/u/sessions/s1"}]
    # Must not raise; the listing is returned untouched.
    await wiring.attach_expires_at(entries, None)
    assert "expires_at" not in entries[0]
