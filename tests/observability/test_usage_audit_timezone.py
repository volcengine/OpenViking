# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Timezone resolution for the Console usage/audit endpoints.

`?timezone=` is an untrusted free-form query parameter (no `pattern=` on the
route), and `ZoneInfo()` rejects a bad key in two different ways: an
unknown-but-well-formed name raises `ZoneInfoNotFoundError`, a malformed one
raises `ValueError` before any lookup happens. Only the first was handled, so a
malformed value escaped as an unhandled exception instead of falling back.
"""

from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from openviking.observability.usage_audit.api_service import UsageAuditQueryService
from openviking.observability.usage_audit.time import (
    resolve_usage_timezone,
    resolve_user_timezone,
)
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.console import router as console_router
from openviking_cli.session.user_id import UserIdentifier

# Malformed keys, each rejected by a different branch of ZoneInfo's validation.
MALFORMED_TIMEZONES = [
    "..",  # traverses out of TZPATH
    "../../etc/passwd",
    "/UTC",  # absolute path
    "Asia/",  # non-normalized
    ".",
]


@pytest.mark.parametrize("value", MALFORMED_TIMEZONES)
def test_resolve_user_timezone_falls_back_on_a_malformed_name(value: str) -> None:
    assert resolve_user_timezone(value, fallback=timezone.utc) is timezone.utc


@pytest.mark.parametrize("value", MALFORMED_TIMEZONES)
def test_resolve_usage_timezone_falls_back_on_a_malformed_name(value: str) -> None:
    # Returns the local tz; the point is that it returns at all.
    assert resolve_usage_timezone(value) is not None


def test_resolve_usage_timezone_falls_back_on_a_non_string_config_value() -> None:
    # `timezone: 8` in YAML parses as an int and used to abort startup.
    assert resolve_usage_timezone(8) is not None  # type: ignore[arg-type]


def test_resolve_user_timezone_still_resolves_a_valid_name() -> None:
    assert resolve_user_timezone("Asia/Shanghai", fallback=timezone.utc) == ZoneInfo(
        "Asia/Shanghai"
    )


def test_resolve_user_timezone_still_falls_back_on_an_unknown_name() -> None:
    assert resolve_user_timezone("Not/AZone", fallback=timezone.utc) is timezone.utc


def test_resolve_user_timezone_falls_back_on_an_empty_value() -> None:
    assert resolve_user_timezone("", fallback=timezone.utc) is timezone.utc
    assert resolve_user_timezone(None, fallback=timezone.utc) is timezone.utc


class _StubStore:
    async def get_today_tokens(self, **_kwargs):
        return {}

    async def get_today_retrievals(self, **_kwargs):
        return {}


class _StubInventory:
    async def get_counts(self, _ctx):
        return {}


class _Runtime:
    def __init__(self, api_service) -> None:
        self.api_service = api_service


def _app() -> FastAPI:
    """Console router wired to the REAL query service, not a fake one.

    The tz resolution under test lives in the service, so a fake service would
    not exercise it.
    """
    app = FastAPI()
    app.include_router(console_router)
    app.state.usage_audit_runtime = _Runtime(
        UsageAuditQueryService(
            store=_StubStore(),  # type: ignore[arg-type]
            inventory=_StubInventory(),  # type: ignore[arg-type]
            timezone_name="UTC",
        )
    )
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier(account_id="acct-1", user_id="user-1"),
        role=Role.ADMIN,
    )
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("value", MALFORMED_TIMEZONES)
async def test_dashboard_summary_survives_a_malformed_timezone_parameter(value: str) -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/console/dashboard/summary", params={"timezone": value})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
