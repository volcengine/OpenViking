# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from openviking.utils.request_headers import (
    bind_captured_request_headers,
    bind_request_headers,
    collect_dynamic_request_header_names,
    create_task_with_request_headers,
    get_request_headers_snapshot,
    get_static_extra_headers,
    resolve_dynamic_extra_headers,
    resolve_extra_headers,
)

MARKER = "@request.header.X-Tenant"


def test_resolvers_preserve_static_values_and_ignore_dynamic_without_context() -> None:
    configured = {
        "X-Static": "fixed",
        "X-Literal": "Bearer @request.header.Authorization",
        "Authorization": "@request.header.Authorization",
    }

    assert get_static_extra_headers(configured) == {
        "X-Static": "fixed",
        "X-Literal": "Bearer @request.header.Authorization",
    }
    assert resolve_dynamic_extra_headers(configured) == {}
    assert resolve_extra_headers(configured) == {
        "X-Static": "fixed",
        "X-Literal": "Bearer @request.header.Authorization",
    }
    assert configured["Authorization"] == "@request.header.Authorization"


def test_binding_uses_lowercase_immutable_snapshots_and_resets_nested_contexts() -> None:
    configured = {
        "Authorization": "@request.header.Authorization",
        "X-Forwarded-Tenant": MARKER,
    }

    assert get_request_headers_snapshot() is None
    with bind_request_headers({"AUTHORIZATION": "Bearer outer"}):
        outer = get_request_headers_snapshot()
        assert isinstance(outer, Mapping)
        assert outer == {"authorization": "Bearer outer"}
        with pytest.raises(TypeError):
            outer["authorization"] = "mutated"  # type: ignore[index]

        assert resolve_extra_headers(configured) == {
            "Authorization": "Bearer outer",
            "X-Forwarded-Tenant": "",
        }
        assert resolve_dynamic_extra_headers(configured) == {
            "Authorization": "Bearer outer",
            "X-Forwarded-Tenant": "",
        }

        with bind_request_headers({"X-Tenant": "inner"}):
            assert get_request_headers_snapshot() == {"x-tenant": "inner"}

        assert get_request_headers_snapshot() is outer

    assert get_request_headers_snapshot() is None


async def test_concurrent_request_contexts_do_not_share_header_values() -> None:
    configured = {"Authorization": "@request.header.Authorization"}

    async def resolve_for(token: str) -> dict[str, str]:
        with bind_request_headers({"Authorization": token}):
            await asyncio.sleep(0)
            return resolve_dynamic_extra_headers(configured)

    first, second = await asyncio.gather(
        resolve_for("Bearer first"),
        resolve_for("Bearer second"),
    )

    assert first == {"Authorization": "Bearer first"}
    assert second == {"Authorization": "Bearer second"}
    assert get_request_headers_snapshot() is None


async def test_explicit_task_context_outlives_request_binding() -> None:
    release = asyncio.Event()

    async def read_after_request() -> Mapping[str, str] | None:
        await release.wait()
        return get_request_headers_snapshot()

    with bind_request_headers({"Authorization": "Bearer expired"}):
        task = create_task_with_request_headers(read_after_request())

    assert get_request_headers_snapshot() is None
    release.set()
    assert await task == {"authorization": "Bearer expired"}
    assert get_request_headers_snapshot() is None


async def test_unmarked_task_context_expires_with_request_binding() -> None:
    release = asyncio.Event()

    async def read_after_request() -> Mapping[str, str] | None:
        await release.wait()
        return get_request_headers_snapshot()

    with bind_request_headers({"Authorization": "Bearer expired"}):
        task = asyncio.create_task(read_after_request())

    release.set()
    assert await task is None


def test_binding_no_captured_request_clears_ambient_context() -> None:
    with bind_request_headers({"Authorization": "Bearer request"}):
        with bind_captured_request_headers(None):
            assert get_request_headers_snapshot() is None
        assert get_request_headers_snapshot() == {"authorization": "Bearer request"}


def test_rfc_token_source_header_names_are_supported() -> None:
    source_name = "X!#$%&'*+-.^_`|~Token"
    configured = {"X-Target": f"@request.header.{source_name}"}

    with bind_request_headers({source_name: "value"}):
        assert resolve_dynamic_extra_headers(configured) == {"X-Target": "value"}


def test_collect_dynamic_sources_from_nested_model_configs() -> None:
    config = {
        "vlm": {
            "extra_headers": {
                "Authorization": "@request.header.Authorization",
                "X-Static": "fixed",
            },
            "providers": {
                "backup": {
                    "extra_headers": {
                        "X-Tenant": "@request.header.X-OpenViking-Tenant",
                    }
                }
            },
        },
        "embedding": {
            "credentials": [
                {"extra_headers": {"X-Trace": "@request.header.X-Trace-ID"}}
            ]
        },
    }

    assert collect_dynamic_request_header_names(config) == {
        "Authorization",
        "X-OpenViking-Tenant",
        "X-Trace-ID",
    }


@pytest.mark.parametrize(
    "value",
    [
        "Bearer @request.header.Authorization",
        "@request.header.",
        "@request.header.X Tenant",
    ],
)
def test_non_exact_markers_remain_static(value: str) -> None:
    assert get_static_extra_headers({"X-Target": value}) == {"X-Target": value}
