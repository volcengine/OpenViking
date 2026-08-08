import inspect

from openviking_sdk.client import AsyncHTTPClient, SyncHTTPClient


def test_async_http_client_write_preserves_positional_telemetry():
    bound = inspect.signature(AsyncHTTPClient.write).bind_partial(
        object(),
        "viking://resources/demo.md",
        "updated",
        "append",
        True,
        3.0,
        False,
    )

    assert bound.arguments["telemetry"] is False
    assert "processing_mode" not in bound.arguments


def test_sync_http_client_write_preserves_positional_telemetry():
    bound = inspect.signature(SyncHTTPClient.write).bind_partial(
        object(),
        "viking://resources/demo.md",
        "updated",
        "append",
        True,
        3.0,
        False,
    )

    assert bound.arguments["telemetry"] is False
    assert "processing_mode" not in bound.arguments
