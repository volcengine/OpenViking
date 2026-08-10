from openviking_cli.client.base import BaseClient


def test_legacy_base_client_subclass_without_git_diff_remains_instantiable():
    async def noop(self, *args, **kwargs):
        return None

    implementations = {name: noop for name in BaseClient.__abstractmethods__ if name != "git_diff"}
    legacy_client_type = type("LegacyClient", (BaseClient,), implementations)

    legacy_client_type()
