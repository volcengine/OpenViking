import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.client.local import LocalClient
from openviking_cli.client.base import BaseClient


def test_legacy_base_client_subclass_without_git_diff_remains_instantiable():
    async def noop(self, *args, **kwargs):
        return None

    implementations = {name: noop for name in BaseClient.__abstractmethods__ if name != "git_diff"}
    legacy_client_type = type("LegacyClient", (BaseClient,), implementations)

    legacy_client_type()


def test_base_client_session_signatures_include_event_tag_configuration():
    create_params = inspect.signature(BaseClient.create_session).parameters
    commit_params = inspect.signature(BaseClient.commit_session).parameters
    update_params = inspect.signature(BaseClient.update_session_config).parameters

    assert "memory_extraction_config" in create_params
    assert "event_tags" in commit_params
    assert "auto_commit_policy" in update_params


@pytest.mark.asyncio
async def test_base_client_update_session_config_is_optional_for_legacy_subclasses():
    async def noop(self, *args, **kwargs):
        return None

    implementations = dict.fromkeys(BaseClient.__abstractmethods__, noop)
    legacy_client_type = type("LegacyClient", (BaseClient,), implementations)
    client = legacy_client_type()

    with pytest.raises(NotImplementedError):
        await client.update_session_config(
            "s1",
            memory_extraction_config={"events": {"tags": []}},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["create_session", "update_session_config"])
async def test_local_client_accepts_null_events_config(method_name):
    client = LocalClient.__new__(LocalClient)
    client._ctx = object()
    session = SimpleNamespace(
        session_id="s1",
        uri="viking://session/s1",
        user=SimpleNamespace(to_dict=lambda: {}),
        meta=SimpleNamespace(auto_commit_policy=None, event_search_tags=None),
    )
    sessions = SimpleNamespace(
        create=AsyncMock(return_value=session),
        update_config=AsyncMock(return_value=session),
        effective_auto_commit_policy=lambda _session: None,
        effective_memory_extraction_config=lambda _session: {"events": {"tags": []}},
    )
    client._service = SimpleNamespace(
        sessions=sessions,
        initialize_user_directories=AsyncMock(),
    )

    await getattr(client, method_name)(
        "s1",
        memory_extraction_config={"events": None},
    )

    if method_name == "create_session":
        assert sessions.create.await_args.kwargs["event_tags"] is None
    else:
        assert sessions.update_config.await_args.kwargs["event_tags"] is None
