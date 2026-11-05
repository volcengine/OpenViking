# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Archive probe failures must not produce a successful, incomplete context."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSNotFoundError, AGFSTimeoutError
from openviking.session import Session

SESSION_URI = "viking://user/acc1/sessions/archive-probe-test"


def _session(*, listing=None, listing_error=None, marker_error=None):
    fs = SimpleNamespace(
        ls=AsyncMock(return_value=listing, side_effect=listing_error),
        exists=AsyncMock(return_value=False, side_effect=marker_error),
        read_file=AsyncMock(return_value=""),
    )
    return Session(
        viking_fs=cast(Any, fs), session_uri=SESSION_URI, session_id="archive-probe-test"
    )


async def test_context_surfaces_transient_archive_listing_failure():
    session = _session(listing_error=AGFSTimeoutError("history listing timed out"))

    with pytest.raises(AGFSTimeoutError, match="history listing timed out"):
        await session.get_session_context()


async def test_context_surfaces_transient_terminal_marker_failure():
    session = _session(
        listing=[{"name": "archive_001"}],
        marker_error=AGFSTimeoutError("marker probe timed out"),
    )

    with pytest.raises(AGFSTimeoutError, match="marker probe timed out"):
        await session.get_session_context()


async def test_context_accepts_genuinely_missing_history_directory():
    session = _session(listing_error=AGFSNotFoundError("history is absent"))

    context = await session.get_session_context()

    assert context["stats"]["totalArchives"] == 0
    assert context["latest_archive_overview"] == ""


async def test_context_treats_absent_terminal_markers_as_pending():
    session = _session(listing=[{"name": "archive_001"}])

    context = await session.get_session_context()

    assert context["stats"]["totalArchives"] == 1
    assert context["latest_archive_overview"] == ""


async def test_context_treats_missing_terminal_marker_error_as_pending():
    session = _session(
        listing=[{"name": "archive_001"}],
        marker_error=AGFSNotFoundError("marker is absent"),
    )

    context = await session.get_session_context()

    assert context["stats"]["totalArchives"] == 1
    assert context["latest_archive_overview"] == ""
