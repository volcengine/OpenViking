import inspect

import pytest

from openviking_sdk.client import AsyncHTTPClient, SyncHTTPClient


def test_async_http_client_write_requires_named_options():
    signature = inspect.signature(AsyncHTTPClient.write)

    with pytest.raises(TypeError):
        signature.bind_partial(
            object(),
            "viking://resources/demo.md",
            "updated",
            {"processing_mode": "vectors_only"},
        )


def test_sync_http_client_write_requires_named_options():
    signature = inspect.signature(SyncHTTPClient.write)

    with pytest.raises(TypeError):
        signature.bind_partial(
            object(),
            "viking://resources/demo.md",
            "updated",
            {"processing_mode": "vectors_only"},
        )
