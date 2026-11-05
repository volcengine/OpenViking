from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_get_artifact_relations_api_uses_fixed_path_and_normalized_to(service, app, client):
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )
    app.dependency_overrides[get_request_context] = lambda: ctx
    graph = {
        "To": "viking://resources/wiki",
        "SnapshotID": "snapshot-1",
        "GeneratedAt": "2026-11-05T00:00:00Z",
        "Nodes": [],
        "Edges": [],
    }
    artifact_relations = SimpleNamespace(get=AsyncMock(return_value=graph))
    service._artifact_relations_service = artifact_relations

    response = await client.post(
        "/api/openviking/GetArtifactRelations",
        json={"To": "viking://resources/wiki/"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == graph
    artifact_relations.get.assert_awaited_once_with("viking://resources/wiki", ctx)
