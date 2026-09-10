import asyncio
from unittest.mock import Mock

import pytest

from openviking.pyagfs.request_cache import RequestCacheScope, request_cache_scope
from openviking.server.request_cache_middleware import RequestCacheMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["response", "error", "cancel"])
async def test_middleware_releases_on_response_error_and_cancellation(ending):
    client = Mock()
    scopes = []

    async def app(scope, receive, send):
        cache = request_cache_scope.get()
        scopes.append(cache)
        cache.bindings[(id(client), "tenant")] = (client, "tenant")
        if ending == "error":
            raise RuntimeError("failure")
        if ending == "cancel":
            raise asyncio.CancelledError()
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        assert not cache.active

    async def send(message):
        pass

    middleware = RequestCacheMiddleware(app, lambda: True)
    if ending == "response":
        await middleware({"type": "http"}, None, send)
    else:
        error = RuntimeError if ending == "error" else asyncio.CancelledError
        with pytest.raises(error):
            await middleware({"type": "http"}, None, send)
    client.release_request_cache.assert_called_once_with("tenant", scopes[0].cache_id)
    assert request_cache_scope.get() is None


@pytest.mark.asyncio
async def test_scope_waits_for_admitted_operations_before_release():
    scope = RequestCacheScope()
    started = asyncio.Event()
    finish = asyncio.Event()
    client = Mock()
    scope.bindings[(id(client), "tenant")] = (client, "tenant")

    async def operation():
        started.set()
        await finish.wait()
        client.release_request_cache.assert_not_called()

    task = asyncio.create_task(operation())
    scope.operations.add(task)
    await started.wait()
    close = asyncio.create_task(scope.close())
    await asyncio.sleep(0)
    assert not scope.active
    client.release_request_cache.assert_not_called()
    finish.set()
    await close
    await scope.close()
    client.release_request_cache.assert_called_once_with("tenant", scope.cache_id)
