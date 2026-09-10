# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""``search`` must refuse a context-only argument in ``mode="list"``.

``POST /search`` already does: ``SearchRequest._validate_mode`` rejects any of
``CONTEXT_ONLY_FIELDS`` supplied in list mode. The MCP tool did not, so the two faces of
the same feature disagreed — and the tool refuses the mirror case (``read_content`` and
``target_uri`` in ``mode="context"``), so the gap was only in this direction.

Everything the context path consumes is read only inside that branch, and the list path
calls ``SearchService.search``, whose signature has no parameter for any of them, so
passing one in list mode did nothing and said nothing. For ``exclude_uris`` that is not a
tuning knob going unused: the caller gets back the URIs it asked to exclude.

These tests take no service fixture. A stub service records what the list path forwards,
which is also how the "these never reach the search service" half is pinned.
"""

from types import SimpleNamespace

import pytest

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier

CTX = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)

# Every parameter the context path consumes, paired with a non-default value.
CONTEXT_ONLY_ARGS = {
    "query_expansion": "off",
    "max_tokens": 999,
    "quotas": {"events": 5},
    "purpose": "coding",
    "detail": "full",
    "detail_by_category": {"events": "full"},
    "dedup_turns": 3,
    "exclude_uris": ["viking://user/test_user/memories/secret.md"],
    "peer_scope": "actor",
    "other_peer_penalty": 0.5,
    "other_peer_penalties": {"events": 0.5},
    "rewrite": "auto",
    "rewrite_max_bullets": 2,
}


class _SearchCalled(Exception):
    """Raised by the stub so the list path stops where it hands off to the service."""

    def __init__(self, kwargs: dict):
        super().__init__(sorted(kwargs))
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def _identity_and_stub_service():
    class _Search:
        def is_intent_enabled(self) -> bool:
            return False

        async def search(self, **kwargs):
            raise _SearchCalled(kwargs)

    set_service(SimpleNamespace(search=_Search()))
    token = mcp_endpoint._mcp_ctx.set(CTX)
    yield
    mcp_endpoint._mcp_ctx.reset(token)
    set_service(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", sorted(CONTEXT_ONLY_ARGS.items()))
async def test_list_mode_refuses_a_context_only_argument(name, value):
    with pytest.raises(InvalidArgumentError, match=rf"\b{name}\b.*mode='context'"):
        await mcp_endpoint.search(query="anything", **{name: value})


@pytest.mark.asyncio
async def test_list_mode_names_every_context_only_argument_it_refuses():
    # One error listing all of them beats making the caller discover them one call at a
    # time, which is what a per-argument raise would do.
    with pytest.raises(InvalidArgumentError) as excinfo:
        await mcp_endpoint.search(query="anything", **CONTEXT_ONLY_ARGS)

    message = str(excinfo.value)
    assert all(name in message for name in CONTEXT_ONLY_ARGS), message
    assert "require mode='context'" in message


@pytest.mark.asyncio
async def test_list_mode_is_unchanged_without_context_only_arguments():
    with pytest.raises(_SearchCalled) as excinfo:
        await mcp_endpoint.search(query="anything", limit=3)

    forwarded = excinfo.value.kwargs
    assert forwarded["query"] == "anything"
    assert forwarded["limit"] == 3
    # The guard must not have started forwarding them either.
    assert not set(forwarded) & set(CONTEXT_ONLY_ARGS)


@pytest.mark.asyncio
async def test_context_mode_still_accepts_them():
    # The guard is list-mode only; reaching assemble_context (which this stub service
    # cannot satisfy) is enough to show the arguments were not refused up front.
    with pytest.raises(Exception) as excinfo:
        await mcp_endpoint.search(query="anything", mode="context", **CONTEXT_ONLY_ARGS)

    assert "require mode='context'" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_context_mode_still_refuses_list_only_arguments():
    with pytest.raises(InvalidArgumentError, match="only supported in mode='list'"):
        await mcp_endpoint.search(query="anything", mode="context", read_content=True)

    with pytest.raises(InvalidArgumentError, match="not supported in mode='context'"):
        await mcp_endpoint.search(
            query="anything", mode="context", target_uri="viking://user/test_user"
        )


@pytest.mark.asyncio
async def test_the_tool_covers_every_field_the_rest_validator_rejects():
    """The tool's list must not drift from ``POST /search``'s.

    The tool splits two of the REST fields in two — ``detail``/``detail_by_category`` and
    ``other_peer_penalty``/``other_peer_penalties`` — so it carries two more names, not a
    different set. A field added to one face has to reach the other.
    """
    from openviking.server.routers.search import CONTEXT_ONLY_FIELDS

    missing = sorted(set(CONTEXT_ONLY_FIELDS) - set(CONTEXT_ONLY_ARGS))

    assert not missing, (
        "POST /search rejects these in list mode but the MCP tool does not: "
        + ", ".join(missing)
    )
    assert set(CONTEXT_ONLY_ARGS) - set(CONTEXT_ONLY_FIELDS) == {
        "detail_by_category",
        "other_peer_penalties",
    }
