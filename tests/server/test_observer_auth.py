# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Auth regressions for observer endpoints."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import AccountNamespacePolicy, ResolvedIdentity, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.routers import observer as observer_router
from openviking_cli.exceptions import OpenVikingError, UnauthenticatedError

ROOT_KEY = "root-secret-key-for-testing-only-1234567890abcdef"
ADMIN_KEY = "observer-admin-key"
USER_KEY = "observer-user-key"


class FakeAPIKeyManager:
    def resolve(self, api_key: str) -> ResolvedIdentity:
        if api_key == ROOT_KEY:
            return ResolvedIdentity(role=Role.ROOT)
        if api_key == ADMIN_KEY:
            return ResolvedIdentity(
                role=Role.ADMIN,
                account_id="acct1",
                user_id="admin_user",
                agent_id="agent1",
                namespace_policy=AccountNamespacePolicy(),
            )
        if api_key == USER_KEY:
            return ResolvedIdentity(
                role=Role.USER,
                account_id="acct1",
                user_id="regular_user",
                agent_id="agent1",
                namespace_policy=AccountNamespacePolicy(),
            )
        raise UnauthenticatedError("Invalid API Key")

    def get_account_policy(self, account_id: str) -> AccountNamespacePolicy:
        return AccountNamespacePolicy()


class FakeComponent:
    def __init__(self, name: str, status: str, healthy: bool = True, has_errors: bool = False):
        self.name = name
        self.status = status
        self.is_healthy = healthy
        self.has_errors = has_errors


class FakeObserverService:
    @property
    def queue(self):
        return FakeComponent("queue", "global queue backlog")

    def vikingdb(self, ctx=None):
        return FakeComponent("vikingdb", f"global vikingdb stats for {ctx.user.user_id}")

    @property
    def models(self):
        return FakeComponent("models", "global model health")

    @property
    def lock(self):
        return FakeComponent("lock", "global active locks")

    @property
    def retrieval(self):
        return FakeComponent("retrieval", "global retrieval stats", healthy=False, has_errors=True)

    def system(self, ctx=None):
        return type(
            "SystemStatus",
            (),
            {
                "is_healthy": False,
                "errors": ["retrieval has errors"],
                "components": {
                    "queue": self.queue,
                    "vikingdb": self.vikingdb(ctx=ctx),
                    "models": self.models,
                    "lock": self.lock,
                    "retrieval": self.retrieval,
                },
            },
        )()


class FakeDebugService:
    def __init__(self):
        self.observer = FakeObserverService()


class FakeService:
    def __init__(self):
        self.debug = FakeDebugService()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key=ROOT_KEY)
    app.state.api_key_manager = FakeAPIKeyManager()

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request, exc: OpenVikingError):
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(code=exc.code, message=exc.message, details=exc.details),
            ).model_dump(),
        )

    set_service(FakeService())
    app.include_router(observer_router.router)
    return app


def _build_trusted_app() -> FastAPI:
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="trusted")

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request, exc: OpenVikingError):
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(code=exc.code, message=exc.message, details=exc.details),
            ).model_dump(),
        )

    set_service(FakeService())
    app.include_router(observer_router.router)
    return app


def test_observer_endpoints_reject_user_keys_but_allow_admin_and_root():
    app = _build_app()
    client = TestClient(app)

    paths = (
        "/api/v1/observer/queue",
        "/api/v1/observer/vikingdb",
        "/api/v1/observer/models",
        "/api/v1/observer/lock",
        "/api/v1/observer/retrieval",
        "/api/v1/observer/system",
    )

    for path in paths:
        user_resp = client.get(path, headers={"X-API-Key": USER_KEY})
        assert user_resp.status_code == 403, f"{path} should reject USER keys"
        assert user_resp.json()["error"]["code"] == "PERMISSION_DENIED"

        admin_resp = client.get(path, headers={"X-API-Key": ADMIN_KEY})
        assert admin_resp.status_code == 200, f"{path} should allow ADMIN keys"

        root_resp = client.get(path, headers={"X-API-Key": ROOT_KEY})
        assert root_resp.status_code == 200, f"{path} should allow ROOT keys"

    admin_system = client.get("/api/v1/observer/system", headers={"X-API-Key": ADMIN_KEY})
    assert (
        admin_system.json()["result"]["components"]["vikingdb"]["status"]
        == "global vikingdb stats for admin_user"
    )


def test_observer_endpoints_allow_trusted_mode_requests():
    app = _build_trusted_app()
    client = TestClient(app)

    response = client.get(
        "/api/v1/observer/system",
        headers={
            "X-OpenViking-Account": "acct1",
            "X-OpenViking-User": "trusted_user",
        },
    )

    assert response.status_code == 200
    assert (
        response.json()["result"]["components"]["vikingdb"]["status"]
        == "global vikingdb stats for trusted_user"
    )
