import sys
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _purge_legacy_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "openviking_sdk"
            or name.startswith("openviking_sdk.")
            or name == "openviking_cli"
            or name.startswith("openviking_cli.")
        ):
            sys.modules.pop(name, None)


def test_legacy_async_http_client_shim_points_to_sdk():
    _purge_legacy_modules()
    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")
    assert client._url == "http://localhost:1933"
    try:
        client._raise_exception({"code": "CONFLICT", "message": "boom"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "CONFLICT"


def test_legacy_sync_http_client_shim_points_to_sdk():
    _purge_legacy_modules()
    from openviking_cli.client.sync_http import SyncHTTPClient as LegacySyncHTTPClient

    client = LegacySyncHTTPClient(url="http://localhost:1933")
    assert client._async_client._url == "http://localhost:1933"
