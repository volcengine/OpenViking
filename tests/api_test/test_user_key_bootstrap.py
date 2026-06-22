from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

API_TEST_DIR = Path(__file__).resolve().parent
if str(API_TEST_DIR) not in sys.path:
    sys.path.insert(0, str(API_TEST_DIR))


def _load_api_test_conftest():
    spec = importlib.util.spec_from_file_location(
        "api_test_bootstrap_conftest",
        API_TEST_DIR / "conftest.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load tests/api_test/conftest.py")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_conftest = _load_api_test_conftest()


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeRootClient:
    def __init__(
        self,
        *,
        list_responses: list[FakeResponse],
        create_responses: list[FakeResponse] | None = None,
        register_responses: list[FakeResponse] | None = None,
    ):
        self.list_responses = list(list_responses)
        self.create_responses = list(create_responses or [])
        self.register_responses = list(register_responses or [])

    def admin_list_users(self, account_id: str):
        return self.list_responses.pop(0)

    def admin_create_account(self, account_id: str, user_id: str):
        return self.create_responses.pop(0)

    def admin_register_user(self, account_id: str, user_id: str, role: str):
        return self.register_responses.pop(0)

    def admin_set_role(self, account_id: str, user_id: str, role: str):
        return FakeResponse(200, {"status": "ok"})

    def admin_regenerate_key(self, account_id: str, user_id: str):
        return FakeResponse(200, {"result": {"user_key": "regenerated-key"}})


def _users_response(user_id: str = "default", api_key: str = "user-key") -> FakeResponse:
    return FakeResponse(
        200,
        {"result": [{"user_id": user_id, "role": "admin", "api_key": api_key}]},
    )


def test_register_conflict_reuses_concurrently_created_user_key(monkeypatch):
    monkeypatch.setattr(api_conftest.time, "sleep", lambda _seconds: None)
    root_client = FakeRootClient(
        list_responses=[
            FakeResponse(200, {"result": []}),
            _users_response(api_key="race-user-key"),
        ],
        register_responses=[FakeResponse(409, {"error": {"code": "ALREADY_EXISTS"}})],
    )

    assert (
        api_conftest._ensure_api_test_user_key(root_client, "default", "default") == "race-user-key"
    )


def test_create_account_conflict_reuses_concurrently_created_user_key(monkeypatch):
    monkeypatch.setattr(api_conftest.time, "sleep", lambda _seconds: None)
    root_client = FakeRootClient(
        list_responses=[
            FakeResponse(404, {"error": {"code": "NOT_FOUND"}}),
            _users_response(api_key="created-by-peer-key"),
        ],
        create_responses=[FakeResponse(409, {"error": {"code": "ALREADY_EXISTS"}})],
    )

    assert (
        api_conftest._ensure_api_test_user_key(root_client, "default", "default")
        == "created-by-peer-key"
    )


def test_refuses_to_fall_back_to_root_key(monkeypatch):
    monkeypatch.setattr(api_conftest.Config, "OPENVIKING_API_KEY", "root-key")
    monkeypatch.setattr(api_conftest.Config, "OPENVIKING_ROOT_API_KEY", "root-key")
    root_client = FakeRootClient(
        list_responses=[FakeResponse(500, {"error": {"code": "SERVER_ERROR"}})]
    )

    with pytest.raises(RuntimeError, match="refusing to fall back to the ROOT API key"):
        api_conftest._ensure_api_test_user_key(root_client, "default", "default")
