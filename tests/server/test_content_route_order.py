# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compare public dispatch and OpenAPI after moving the content router."""

import ast
import inspect

from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from openviking.server import app as module


async def test_content_first_preserves_public_dispatch_and_openapi(monkeypatch):
    factory = ast.parse(inspect.getsource(module.create_app)).body[0]
    calls = [
        n.value
        for n in factory.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "include_router"
    ]
    assert calls[0].args[0].id == "content_router"
    old = list(calls)
    content = old.pop(0)
    index = next(i for i, call in enumerate(old) if call.args[0].id == "console_router")
    old.insert(index, content)

    def build(ordered):
        app = FastAPI()
        for call in ordered:
            app.include_router(
                getattr(module, call.args[0].id),
                **{k.arg: ast.literal_eval(k.value) for k in call.keywords},
            )
        return app

    baseline, candidate = build(old), build(calls)
    assert baseline.openapi() == candidate.openapi()

    async def probe(self, scope, receive, send):
        response = JSONResponse(
            {
                "endpoint": self.endpoint.__module__ + "." + self.endpoint.__qualname__,
                "params": scope.get("path_params", {}),
            }
        )
        await response(scope, receive, send)

    monkeypatch.setattr(APIRoute, "handle", probe)
    requests = {("GET", "/missing-route"), ("POST", "/api/v1/content/read")}

    def routes(items):
        for item in items:
            if hasattr(item, "effective_candidates"):
                yield from routes(item.effective_candidates())
            else:
                yield item

    for route in routes(baseline.routes):
        if not isinstance(getattr(route, "original_route", route), APIRoute):
            continue
        path = route.path_format
        for name, converter in route.param_convertors.items():
            kind = type(converter).__name__
            value = (
                "1"
                if kind in {"IntegerConvertor", "FloatConvertor"}
                else "12345678-1234-5678-1234-567812345678"
                if kind == "UUIDConvertor"
                else "route-probe"
            )
            path = path.replace("{" + name + "}", value)
        for method in set(route.methods or ()) | {"GET", "POST", "DELETE", "HEAD", "OPTIONS"}:
            requests.add((method, path))
            requests.add((method, path.rstrip("/") + "/"))
    assert len(requests) > 500, "public route traversal must not silently omit included routers"
    async with (
        AsyncClient(transport=ASGITransport(app=baseline), base_url="http://test") as a,
        AsyncClient(transport=ASGITransport(app=candidate), base_url="http://test") as b,
    ):
        for method, path in sorted(requests):
            x, y = await a.request(method, path), await b.request(method, path)
            assert (x.status_code, x.content, x.headers.get("location")) == (
                y.status_code,
                y.content,
                y.headers.get("location"),
            ), (method, path)
