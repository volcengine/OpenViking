import inspect

from openviking import AsyncOpenViking, SyncOpenViking
from openviking.client.local import LocalClient
from openviking_sdk.client import AsyncHTTPClient, SyncHTTPClient


def test_async_openviking_write_preserves_positional_telemetry():
    bound = inspect.signature(AsyncOpenViking.write).bind_partial(
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


def test_sync_openviking_write_preserves_positional_telemetry():
    bound = inspect.signature(SyncOpenViking.write).bind_partial(
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


def test_local_client_write_preserves_positional_telemetry():
    bound = inspect.signature(LocalClient.write).bind_partial(
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
