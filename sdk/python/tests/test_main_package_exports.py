import sys
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _purge_openviking_modules() -> None:
    for name in list(sys.modules):
        if name == "openviking" or name.startswith("openviking."):
            sys.modules.pop(name, None)
        if name == "openviking_sdk" or name.startswith("openviking_sdk."):
            sys.modules.pop(name, None)


def test_openviking_top_level_exports_http_clients():
    _purge_openviking_modules()
    import openviking
    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient
    from openviking_cli.client.sync_http import SyncHTTPClient as LegacySyncHTTPClient

    assert openviking.AsyncHTTPClient is LegacyAsyncHTTPClient
    assert openviking.SyncHTTPClient is LegacySyncHTTPClient


def test_openviking_client_module_exports_http_clients():
    _purge_openviking_modules()
    import os

    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient
    from openviking_cli.client.sync_http import SyncHTTPClient as LegacySyncHTTPClient

    os.environ["OPENVIKING_LOG_ROUTING"] = "stdout"
    import openviking.client as client_module

    assert client_module.AsyncHTTPClient is LegacyAsyncHTTPClient
    assert client_module.SyncHTTPClient is LegacySyncHTTPClient


def test_openviking_client_module_can_fallback_to_repo_local_sdk():
    _purge_openviking_modules()
    original_sys_path = list(sys.path)
    try:
        sys.path[:] = [p for p in sys.path if p != str(SDK_ROOT)]

        import openviking.client as client_module

        assert client_module.AsyncHTTPClient.__module__ == "openviking_cli.client._http_compat"
        assert client_module.SyncHTTPClient.__module__ == "openviking_cli.client._http_compat"
    finally:
        sys.path[:] = original_sys_path


def test_openviking_client_module_import_is_lazy_for_local_client_stack():
    _purge_openviking_modules()

    import openviking.client as client_module

    assert "openviking.client.local" not in sys.modules
    assert "openviking.service" not in sys.modules
    assert client_module.AsyncHTTPClient.__module__ == "openviking_cli.client._http_compat"


def test_openviking_http_client_preserves_legacy_exception_types():
    _purge_openviking_modules()
    import openviking
    from openviking_cli.exceptions import ConflictError

    client = openviking.AsyncHTTPClient(url="http://127.0.0.1:1933")

    try:
        client._raise_exception({"code": "CONFLICT", "message": "mapped"})
    except Exception as exc:
        assert isinstance(exc, ConflictError)
        assert exc.code == "CONFLICT"
    else:
        raise AssertionError("expected ConflictError")
