import inspect

import pytest

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
