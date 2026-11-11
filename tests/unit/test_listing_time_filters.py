# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the time-window filters on session and directory listings.

The filters are metadata predicates: they must select exactly the entries
inside the window, they must not depend on where the entry sits in the raw
listing, and they must not cost an extra pass over the directory.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking.service.session_service import SessionService
from openviking.storage.viking_fs._listing import ListingPage, _ListingMixin
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


def _ctx():
    return RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user"),
        role=Role.ROOT,
    )


def _entry(name, mod_time, uri=None):
    return {
        "name": name,
        "isDir": False,
        "modTime": mod_time,
        "uri": uri or f"viking://user/user/memories/{name}",
    }


def _fake_ls(entries):
    """Build a VikingFS.ls stub that honours offset and node_limit."""

    async def ls(uri, ctx=None, offset=0, node_limit=None, **kwargs):
        page = entries[offset:] if node_limit is None else entries[offset : offset + node_limit]
        return [dict(item) for item in page]

    return ls


@pytest.fixture
def fs_service():
    return FSService(viking_fs=MagicMock())


# --------------------------------------------------------------------------
# Directory listings
# --------------------------------------------------------------------------


class _FakeVikingFS(_ListingMixin):
    """A filesystem layer that serves a fixed listing.

    The window and the pagination contract live in the filesystem layer, so
    the listing tests drive that layer directly.
    """

    def __init__(self, entries=None, pages=None):
        self._entries = [dict(item) for item in (entries or [])]
        self._pages = pages
        self.ls_calls = 0

    # The access checks are not part of the listing contract under test.
    def authorize_read(self, ctx, uris):
        return ctx

    def _ctx_or_default(self, ctx):
        return ctx

    async def _ensure_traversable(self, uri, ctx):
        return None

    async def _ls_original(
        self,
        uri,
        show_all_hidden=False,
        node_limit=1000,
        offset=0,
        sort_by=None,
        sort_order="asc",
        ctx=None,
    ):
        self.ls_calls += 1
        if self._pages is not None:
            index = self.ls_calls - 1
            page = self._pages[index] if index < len(self._pages) else []
            return [dict(item) for item in page]
        page = (
            self._entries[offset:]
            if node_limit is None
            else self._entries[offset : offset + node_limit]
        )
        return [dict(item) for item in page]

    async def _tree_original(
        self,
        uri,
        show_all_hidden=False,
        node_limit=1000,
        level_limit=3,
        offset=0,
        sort_by=None,
        sort_order="asc",
        ctx=None,
        directories_only=False,
    ):
        return await self._ls_original(
            uri,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
            ctx=ctx,
        )

    async def _finalize_listing_entries(self, entries, *args, **kwargs):
        return entries


async def test_ls_keeps_only_entries_inside_the_window():
    fs = _FakeVikingFS(
        [
            _entry("old", "2026-03-09T10:00:00Z"),
            _entry("inside", "2026-03-11T10:00:00Z"),
            _entry("new", "2026-03-20T10:00:00Z"),
        ]
    )

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        since="2026-03-10",
        until="2026-03-12",
    )

    assert [entry["name"] for entry in page.entries] == ["inside"]
    assert page.has_more is False


async def test_ls_applies_the_window_before_the_node_limit():
    # The matching entry sits behind 300 raw entries, so a filter applied after
    # the raw page was cut would return nothing.
    entries = [_entry(f"old-{index}", "2026-03-09T10:00:00Z") for index in range(300)]
    entries.append(_entry("inside", "2026-03-11T10:00:00Z"))
    fs = _FakeVikingFS(entries)

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        since="2026-03-10",
        until="2026-03-12",
        node_limit=10,
    )

    assert [entry["name"] for entry in page.entries] == ["inside"]


async def test_ls_paginates_over_the_matched_entries():
    fs = _FakeVikingFS(
        [
            _entry("a", "2026-03-11T10:00:00Z"),
            _entry("b", "2026-03-11T11:00:00Z"),
            _entry("c", "2026-03-11T12:00:00Z"),
        ]
    )

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        since="2026-03-10",
        node_limit=1,
        offset=1,
    )

    assert [entry["name"] for entry in page.entries] == ["b"]
    assert page.has_more is True


async def test_ls_stops_scanning_once_the_window_is_exceeded():
    # A descending mtime listing ends with the oldest entries, so the scan can
    # stop as soon as it reaches entries that are older than the window
    # instead of walking the whole tail.
    entries = [_entry(f"inside-{index}", "2026-03-05T10:00:00Z") for index in range(2)]
    entries += [_entry(f"old-{index}", "2026-01-01T10:00:00Z") for index in range(5000)]
    fs = _FakeVikingFS(entries)

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        since="2026-03-02",
        sort_by="mtime",
        sort_order="desc",
        node_limit=10,
    )

    assert [entry["name"] for entry in page.entries] == ["inside-0", "inside-1"]
    assert fs.ls_calls == 1


async def test_ls_keeps_scanning_while_the_batch_is_still_in_the_directory_group():
    # A listing is ordered "directories first, then files". Stopping the scan
    # while it is still inside the directory group would skip the file group
    # further down, and the files there can be inside the window.
    directories = [_entry(f"dir-{index}", "2026-03-01T10:00:00Z") for index in range(300)]
    for entry in directories:
        entry["isDir"] = True
    files = [_entry("inside.md", "2026-03-11T10:00:00Z")]
    fs = _FakeVikingFS(pages=[directories, files])

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        since="2026-03-10",
        sort_by="mtime",
        sort_order="desc",
        node_limit=10,
    )

    assert [entry["name"] for entry in page.entries] == ["inside.md"]
    assert fs.ls_calls == 2, "the scan must continue past the directory group"


async def test_ls_stops_scanning_an_ascending_listing_at_the_upper_bound():
    entries = [_entry(f"inside-{index}", "2026-03-05T10:00:00Z") for index in range(2)]
    entries += [_entry(f"new-{index}", "2027-01-01T10:00:00Z") for index in range(5000)]
    fs = _FakeVikingFS(entries)

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        until="2026-03-10",
        sort_by="mtime",
        sort_order="asc",
        node_limit=10,
    )

    assert [entry["name"] for entry in page.entries] == ["inside-0", "inside-1"]
    assert fs.ls_calls == 1


async def test_tree_keeps_only_entries_inside_the_window():
    fs = _FakeVikingFS(
        [
            _entry("old", "2026-03-09T10:00:00Z"),
            _entry("inside", "2026-03-11T10:00:00Z"),
        ]
    )

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        recursive=True,
        since="2026-03-10",
        until="2026-03-12",
    )

    assert [entry["name"] for entry in page.entries] == ["inside"]


async def test_ls_rejects_an_inverted_window():
    fs = _FakeVikingFS([])

    with pytest.raises(ValueError):
        await fs.list_page(
            "viking://user/user/memories",
            ctx=_ctx(),
            since="2026-03-12",
            until="2026-03-11",
        )


async def test_ls_with_a_zero_node_limit_returns_nothing():
    # The raw listing treats a zero limit as "nothing to list".
    fs = _FakeVikingFS([_entry("inside", "2026-03-11T10:00:00Z")])

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        node_limit=0,
    )

    assert page.entries == []
    assert page.has_more is False


async def test_ls_applies_the_entry_filter_together_with_the_window():
    # The retrieval tags are a filter over the matched entries, not a
    # replacement for the window.
    fs = _FakeVikingFS(
        [
            _entry("keep", "2026-03-11T10:00:00Z"),
            _entry("drop-window", "2026-01-01T10:00:00Z"),
            _entry("drop-filter", "2026-03-11T10:00:00Z"),
        ]
    )

    async def entry_filter(entries):
        return [entry for entry in entries if entry["name"] != "drop-filter"]

    page = await fs.list_page(
        "viking://user/user/memories",
        ctx=_ctx(),
        since="2026-03-10",
        entry_filter=entry_filter,
    )

    assert [entry["name"] for entry in page.entries] == ["keep"]


async def test_ls_without_a_window_keeps_the_previous_behaviour():
    fs = _FakeVikingFS(
        [
            _entry("old", "2026-03-09T10:00:00Z"),
            _entry("new", "2026-03-20T10:00:00Z"),
        ]
    )

    page = await fs.list_page("viking://user/user/memories", ctx=_ctx())

    assert [entry["name"] for entry in page.entries] == ["old", "new"]
    assert fs.ls_calls == 1


async def test_the_service_forwards_the_window_to_the_filesystem_layer():
    # The window is a filesystem-level contract: the service only hands it
    # over, so every listing surface shares the same pagination semantics.
    service = FSService(viking_fs=_FakeVikingFS([_entry("inside", "2026-03-11T10:00:00Z")]))
    calls = []

    async def list_page(uri, ctx=None, **kwargs):
        calls.append(kwargs)
        return ListingPage(entries=[], has_more=False)

    service._viking_fs.list_page = list_page

    await service.ls(
        "viking://user/user/memories",
        ctx=_ctx(),
        since="2026-03-10",
        until="2026-03-12",
        offset=3,
        node_limit=7,
    )

    assert calls[0]["since"] == "2026-03-10"
    assert calls[0]["until"] == "2026-03-12"
    assert calls[0]["offset"] == 3
    assert calls[0]["node_limit"] == 7


async def test_the_service_rejects_an_inverted_window():
    service = FSService(viking_fs=_FakeVikingFS([]))

    with pytest.raises(InvalidArgumentError):
        await service.ls(
            "viking://user/user/memories",
            ctx=_ctx(),
            since="2026-03-12",
            until="2026-03-11",
        )


def test_the_mcp_and_rest_surfaces_build_the_same_time_filter():
    """Both retrieval surfaces have to compile the same metadata filter.

    The MCP tools and the REST routes are separate entry points, and a window
    that selects different records depending on which one the caller used
    would make the feature unusable for an agent.
    """
    from openviking.server.mcp_endpoint import _resolve_mcp_search_filter
    from openviking.server.routers.search import _resolve_search_filter

    for bounds in (
        {"since": "2026-03-01"},
        {"until": "2026-03-02"},
        {"since": "2026-03-01", "until": "2026-03-02"},
        {"since": "2026-03-01", "until": "2026-03-02", "time_field": "created_at"},
    ):
        mcp_filter = _resolve_mcp_search_filter(None, **bounds)
        rest_filter = _resolve_search_filter(
            None,
            None,
            bounds.get("since"),
            bounds.get("until"),
            bounds.get("time_field"),
            None,
        )
        assert mcp_filter == rest_filter, bounds


# --------------------------------------------------------------------------
# Session listings
# --------------------------------------------------------------------------


def _session_service(session_meta, directory_time=None):
    viking_fs = MagicMock()
    viking_fs.ls = AsyncMock(
        side_effect=_fake_ls(
            [
                {
                    "name": session_id,
                    "isDir": True,
                    # The directory modification time tracks the last write of
                    # the session, so it mirrors the metadata timestamp. The
                    # listing truncates it to whole seconds.
                    "modTime": directory_time
                    or meta.get("updated_at")
                    or meta.get("created_at")
                    or "2026-03-11T10:00:00Z",
                    "uri": f"viking://user/user/sessions/{session_id}",
                }
                for session_id, meta in session_meta.items()
            ]
        )
    )

    async def read_file(uri, ctx=None, **kwargs):
        session_id = uri.split("/sessions/")[-1].split("/")[0]
        return json.dumps(session_meta[session_id])

    viking_fs.read_file = AsyncMock(side_effect=read_file)
    return SessionService(viking_fs=viking_fs)


async def test_sessions_keeps_only_sessions_inside_the_window():
    service = _session_service(
        {
            "old": {"created_at": "2026-03-09T10:00:00Z", "updated_at": "2026-03-09T10:00:00Z"},
            "inside": {
                "created_at": "2026-03-11T09:00:00Z",
                "updated_at": "2026-03-11T10:00:00Z",
            },
            "new": {"created_at": "2026-03-20T10:00:00Z", "updated_at": "2026-03-20T10:00:00Z"},
        }
    )

    sessions = await service.sessions(
        _ctx(),
        since="2026-03-10",
        until="2026-03-12",
    )

    assert [session["session_id"] for session in sessions] == ["inside"]


async def test_sessions_exposes_the_resolved_timestamps():
    service = _session_service(
        {
            "inside": {
                "created_at": "2026-03-11T09:00:00Z",
                "updated_at": "2026-03-11T10:00:00Z",
                "last_message_at": "2026-03-11T10:05:00Z",
            }
        }
    )

    sessions = await service.sessions(_ctx(), since="2026-03-10")

    assert sessions[0]["created_at"] == "2026-03-11T09:00:00Z"
    assert sessions[0]["updated_at"] == "2026-03-11T10:00:00Z"
    assert sessions[0]["last_message_at"] == "2026-03-11T10:05:00Z"


async def test_sessions_reads_the_metadata_of_every_candidate():
    # The listing truncates the directory modification time to whole seconds,
    # so it cannot rule a session out before its metadata is read.
    service = _session_service(
        {
            "old": {"created_at": "2026-03-01T10:00:00Z", "updated_at": "2026-03-01T10:00:00Z"},
            "inside": {
                "created_at": "2026-03-11T09:00:00Z",
                "updated_at": "2026-03-11T10:00:00Z",
            },
        }
    )
    read_file = service._viking_fs.read_file
    read_uris = []

    async def tracking_read_file(uri, ctx=None, **kwargs):
        read_uris.append(uri)
        return await read_file(uri, ctx=ctx, **kwargs)

    service._viking_fs.read_file = tracking_read_file

    sessions = await service.sessions(_ctx(), since="2026-03-10")

    assert [session["session_id"] for session in sessions] == ["inside"]
    assert read_uris == [
        "viking://user/user/sessions/old/.meta.json",
        "viking://user/user/sessions/inside/.meta.json",
    ]


async def test_sessions_keeps_a_session_inside_a_sub_second_window():
    # The listing truncates the directory modification time to whole seconds
    # while the metadata keeps the sub-second precision, so a lower bound
    # inside the same second must not drop the session.
    service = _session_service(
        {
            "sub-second": {
                "created_at": "2026-03-11T10:00:00.400Z",
                "updated_at": "2026-03-11T10:00:00.500Z",
                "last_message_at": "2026-03-11T10:00:00.500Z",
            }
        },
        directory_time="2026-03-11T10:00:00Z",
    )

    sessions = await service.sessions(_ctx(), since="2026-03-11T10:00:00.450Z")

    assert [session["session_id"] for session in sessions] == ["sub-second"]


async def test_sessions_drops_a_session_outside_a_sub_second_window():
    service = _session_service(
        {
            "sub-second": {
                "created_at": "2026-03-11T10:00:00.400Z",
                "updated_at": "2026-03-11T10:00:00.500Z",
                "last_message_at": "2026-03-11T10:00:00.500Z",
            }
        },
        directory_time="2026-03-11T10:00:00Z",
    )

    sessions = await service.sessions(_ctx(), since="2026-03-11T10:00:00.600Z")

    assert sessions == []


async def test_sessions_filters_on_the_requested_time_field():
    service = _session_service(
        {
            "old-but-active": {
                "created_at": "2026-03-01T10:00:00Z",
                "updated_at": "2026-03-11T10:00:00Z",
            }
        }
    )

    created = await service.sessions(_ctx(), since="2026-03-10", time_field="created_at")
    updated = await service.sessions(_ctx(), since="2026-03-10", time_field="updated_at")

    assert created == []
    assert [session["session_id"] for session in updated] == ["old-but-active"]


async def test_sessions_paginates_over_the_matched_sessions():
    service = _session_service(
        {
            f"session-{index}": {
                "created_at": "2026-03-11T10:00:00Z",
                "updated_at": "2026-03-11T10:00:00Z",
            }
            for index in range(5)
        }
    )

    sessions = await service.sessions(_ctx(), limit=2, offset=1)

    assert len(sessions) == 2


async def test_sessions_falls_back_to_the_directory_time_for_a_legacy_session():
    # A session written before the metadata carried the field still has to show
    # up in a filtered listing.
    service = _session_service({"legacy": {"created_at": "2026-03-11T09:00:00Z"}})

    sessions = await service.sessions(_ctx(), since="2026-03-10", until="2026-03-12")

    assert [session["session_id"] for session in sessions] == ["legacy"]


async def test_sessions_rejects_an_unknown_time_field():
    service = _session_service({})

    with pytest.raises(InvalidArgumentError):
        await service.sessions(_ctx(), since="2026-03-10", time_field="published_at")


async def test_sessions_rejects_an_inverted_window():
    service = _session_service({})

    with pytest.raises(InvalidArgumentError):
        await service.sessions(_ctx(), since="2026-03-12", until="2026-03-11")
