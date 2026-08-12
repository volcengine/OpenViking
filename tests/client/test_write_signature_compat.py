import inspect

from openviking_sdk.client import AsyncHTTPClient, SyncHTTPClient


def test_async_http_client_write_accepts_options_as_third_argument():
    bound = inspect.signature(AsyncHTTPClient.write).bind_partial(
        object(),
        "viking://resources/demo.md",
        "updated",
        {"mode": "append", "wait": True, "timeout": 3.0, "telemetry": False},
    )

    assert bound.arguments["options"] == {
        "mode": "append",
        "wait": True,
        "timeout": 3.0,
        "telemetry": False,
    }


def test_sync_http_client_write_accepts_options_as_third_argument():
    bound = inspect.signature(SyncHTTPClient.write).bind_partial(
        object(),
        "viking://resources/demo.md",
        "updated",
        {"mode": "append", "wait": True, "timeout": 3.0, "telemetry": False},
    )

    assert bound.arguments["options"] == {
        "mode": "append",
        "wait": True,
        "timeout": 3.0,
        "telemetry": False,
    }
