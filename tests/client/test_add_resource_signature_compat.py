import inspect

from openviking_sdk.client import AsyncHTTPClient, SyncHTTPClient

from openviking import AsyncOpenViking, SyncOpenViking
from openviking.client.local import LocalClient


def test_python_add_resource_clients_accept_add_type_keyword():
    for client_type in (
        AsyncOpenViking,
        SyncOpenViking,
        LocalClient,
        AsyncHTTPClient,
        SyncHTTPClient,
    ):
        parameters = inspect.signature(client_type.add_resource).parameters
        assert "add_type" in parameters, client_type.__name__


def test_async_openviking_add_resource_preserves_positional_watch_args():
    bound = inspect.signature(AsyncOpenViking.add_resource).bind_partial(
        object(),
        "doc.md",
        None,
        None,
        "",
        "",
        False,
        None,
        True,
        False,
        1440,
        {"site": True},
        False,
    )

    assert bound.arguments["watch_interval"] == 1440
    assert bound.arguments["args"] == {"site": True}
    assert bound.arguments["telemetry"] is False
    assert "processing_mode" not in bound.arguments


def test_sync_openviking_add_resource_preserves_positional_args_and_telemetry():
    bound = inspect.signature(SyncOpenViking.add_resource).bind_partial(
        object(),
        "doc.md",
        None,
        None,
        "",
        "",
        False,
        None,
        True,
        False,
        {"site": True},
        False,
    )

    assert bound.arguments["args"] == {"site": True}
    assert bound.arguments["telemetry"] is False
    assert "processing_mode" not in bound.arguments


def test_local_client_add_resource_preserves_positional_watch_args():
    bound = inspect.signature(LocalClient.add_resource).bind_partial(
        object(),
        "doc.md",
        None,
        None,
        "",
        "",
        False,
        None,
        True,
        False,
        False,
        1440,
        {"site": True},
    )

    assert bound.arguments["telemetry"] is False
    assert bound.arguments["watch_interval"] == 1440
    assert bound.arguments["args"] == {"site": True}
    assert "processing_mode" not in bound.arguments


def test_async_http_client_add_resource_preserves_positional_watch_args():
    bound = inspect.signature(AsyncHTTPClient.add_resource).bind_partial(
        object(),
        "doc.md",
        None,
        None,
        "",
        "",
        False,
        None,
        False,
        None,
        None,
        None,
        True,
        None,
        1440,
        {"site": True},
        False,
    )

    assert bound.arguments["watch_interval"] == 1440
    assert bound.arguments["args"] == {"site": True}
    assert bound.arguments["telemetry"] is False
    assert "processing_mode" not in bound.arguments


def test_sync_http_client_add_resource_preserves_positional_watch_args():
    bound = inspect.signature(SyncHTTPClient.add_resource).bind_partial(
        object(),
        "doc.md",
        None,
        None,
        "",
        "",
        False,
        None,
        False,
        None,
        None,
        None,
        True,
        None,
        1440,
        {"site": True},
        False,
    )

    assert bound.arguments["watch_interval"] == 1440
    assert bound.arguments["args"] == {"site": True}
    assert bound.arguments["telemetry"] is False
    assert "processing_mode" not in bound.arguments
