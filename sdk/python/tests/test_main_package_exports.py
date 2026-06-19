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


def test_openviking_top_level_exports_http_clients():
    _purge_openviking_modules()
    from openviking_sdk import AsyncHTTPClient as SDKAsyncHTTPClient
    from openviking_sdk import SyncHTTPClient as SDKSyncHTTPClient

    import openviking

    assert openviking.AsyncHTTPClient is SDKAsyncHTTPClient
    assert openviking.SyncHTTPClient is SDKSyncHTTPClient


def test_openviking_client_module_exports_http_clients():
    _purge_openviking_modules()
    from openviking_sdk import AsyncHTTPClient as SDKAsyncHTTPClient
    from openviking_sdk import SyncHTTPClient as SDKSyncHTTPClient

    import openviking.client as client_module

    assert client_module.AsyncHTTPClient is SDKAsyncHTTPClient
    assert client_module.SyncHTTPClient is SDKSyncHTTPClient
