import inspect

from openviking_sdk.client import (
    AsyncHTTPClient,
    Session,
    SyncHTTPClient,
    SyncSession,
)


def test_async_http_client_core_methods_allow_positional_options():
    inspect.signature(AsyncHTTPClient.add_resource).bind_partial(
        object(), "source.md", "viking://resources/", None, True, 60, {}
    )
    inspect.signature(AsyncHTTPClient.find).bind_partial(
        object(), "query", "viking://resources/", 5, {"level": [1]}
    )
    inspect.signature(AsyncHTTPClient.search).bind_partial(
        object(), "query", "session-1", "viking://resources/", 5, {}
    )
    inspect.signature(AsyncHTTPClient.write).bind_partial(
        object(), "viking://resources/demo.md", "updated", "append", True, 60, {}
    )
    inspect.signature(AsyncHTTPClient.add_message).bind_partial(
        object(), "session-1", "user", "hello", None, {}
    )

def test_sync_http_client_core_methods_allow_positional_options():
    inspect.signature(SyncHTTPClient.add_resource).bind_partial(
        object(), "source.md", "viking://resources/", None, True, 60, {}
    )
    inspect.signature(SyncHTTPClient.find).bind_partial(
        object(), "query", "viking://resources/", 5, {"level": [1]}
    )
    inspect.signature(SyncHTTPClient.search).bind_partial(
        object(), "query", "session-1", "viking://resources/", 5, {}
    )
    inspect.signature(SyncHTTPClient.write).bind_partial(
        object(), "viking://resources/demo.md", "updated", "append", True, 60, {}
    )
    inspect.signature(SyncHTTPClient.add_message).bind_partial(
        object(), "session-1", "user", "hello", None, {}
    )


def test_session_message_methods_allow_positional_options():
    inspect.signature(Session.add_message).bind_partial(
        object(), "user", "hello", None, {}
    )
    inspect.signature(SyncSession.add_message).bind_partial(
        object(), "user", "hello", None, {}
    )
