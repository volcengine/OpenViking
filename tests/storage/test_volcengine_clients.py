from volcengine.base.Request import Request

from openviking.storage.vectordb.collection.volcengine_clients import (
    ClientForConsoleApi,
    ClientForDataApi,
    ClientForDataApiWithApiKey,
)
from openviking.storage.vectordb.collection.volcengine_collection import VolcengineCollection
from openviking.storage.vectordb_adapters.volcengine_adapter import VolcengineCollectionAdapter
from openviking_cli.utils.config.vectordb_config import (
    VolcengineConfig,
    VectorDBBackendConfig,
)


def test_console_client_prepare_request_includes_session_token():
    client = ClientForConsoleApi(
        "test-ak",
        "test-sk",
        "cn-beijing",
        session_token="test-session-token",
    )

    request = client.prepare_request(
        "POST",
        params={"Action": "ListVikingdbCollection", "Version": "2025-06-09"},
        data={"PageNumber": 1, "PageSize": 10},
    )

    assert request.headers["X-Security-Token"] == "test-session-token"
    assert "Authorization" in request.headers


def test_console_client_do_req_uses_signed_query_params(monkeypatch):
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return object()

    def fake_prepare_request(self, method, params=None, data=None):
        request = Request()
        request.method = method
        request.path = "/"
        request.body = '{"PageNumber": 1, "PageSize": 10}'
        request.headers = {"Authorization": "signed-auth"}
        request.query = {
            "Action": "ListVikingdbCollection",
            "Version": "2025-06-09",
            "X-Date": "20260405T091640Z",
            "X-Signature": "signed",
        }
        return request

    monkeypatch.setattr(
        "openviking.storage.vectordb.collection.volcengine_clients.requests.request",
        fake_request,
    )
    monkeypatch.setattr(ClientForConsoleApi, "prepare_request", fake_prepare_request)

    client = ClientForConsoleApi("test-ak", "test-sk", "cn-beijing")
    client.do_req(
        "POST",
        req_params={"Action": "ListVikingdbCollection", "Version": "2025-06-09"},
        req_body={"PageNumber": 1, "PageSize": 10},
    )

    assert captured["params"]["X-Date"] == "20260405T091640Z"
    assert captured["params"]["X-Signature"] == "signed"


def test_data_client_do_req_uses_signed_query_params(monkeypatch):
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return object()

    def fake_prepare_request(self, method, path, params=None, data=None):
        request = Request()
        request.method = method
        request.path = path
        request.body = '{"project": "default"}'
        request.headers = {"Authorization": "signed-auth"}
        request.query = {
            "Action": "Search",
            "Version": "2025-06-09",
            "X-Date": "20260405T091640Z",
            "X-Signature": "signed",
        }
        return request

    monkeypatch.setattr(
        "openviking.storage.vectordb.collection.volcengine_clients.requests.request",
        fake_request,
    )
    monkeypatch.setattr(ClientForDataApi, "prepare_request", fake_prepare_request)

    client = ClientForDataApi("test-ak", "test-sk", "cn-beijing")
    client.do_req(
        "POST",
        "/api/vikingdb/data/search/vector",
        req_params={"Action": "Search", "Version": "2025-06-09"},
        req_body={"project": "default"},
    )

    assert captured["params"]["X-Date"] == "20260405T091640Z"
    assert captured["params"]["X-Signature"] == "signed"


def test_data_api_key_client_prepare_request_sets_bearer_auth():
    client = ClientForDataApiWithApiKey(
        api_key="vk-test-token",
        host="api-vikingdb.vikingdb.cn-beijing.volces.com",
    )

    request = client.prepare_request(
        "POST",
        "/api/vikingdb/data/search/vector",
        data={"project": "default"},
    )

    assert request.headers["Authorization"] == "Bearer vk-test-token"
    assert request.headers["Host"] == "api-vikingdb.vikingdb.cn-beijing.volces.com"
    assert request.path == "/api/vikingdb/data/search/vector"


def test_volcengine_adapter_preserves_session_token_from_config():
    config = VectorDBBackendConfig(
        backend="volcengine",
        name="context",
        volcengine=VolcengineConfig(
            ak="test-ak",
            sk="test-sk",
            region="cn-beijing",
            session_token="test-session-token",
        ),
    )

    adapter = VolcengineCollectionAdapter.from_config(config)

    assert adapter._config()["SessionToken"] == "test-session-token"


def test_volcengine_adapter_supports_api_key_mode_from_config():
    config = VectorDBBackendConfig(
        backend="volcengine",
        name="context",
        project="default",
        volcengine=VolcengineConfig(
            api_key="vk-test-token",
            host="api-vikingdb.vikingdb.cn-beijing.volces.com",
        ),
    )

    adapter = VolcengineCollectionAdapter.from_config(config)

    assert adapter.mode == "volcengine"
    assert adapter.collection_name == "context"
    assert adapter.index_name == "default"
    assert adapter.collection_exists() is True
    assert adapter._config()["ApiKey"] == "vk-test-token"


def test_volcengine_collection_get_meta_data_returns_empty_on_signature_error(monkeypatch):
    class _Response:
        status_code = 403
        text = "signature mismatch"

        @staticmethod
        def json():
            return {
                "ResponseMetadata": {
                    "Error": {
                        "Code": "SignatureDoesNotMatch",
                        "Message": "The request signature we calculated does not match",
                    }
                }
            }

    collection = VolcengineCollection(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        meta_data={"ProjectName": "default", "CollectionName": "context"},
    )
    monkeypatch.setattr(collection.console_client, "do_req", lambda *args, **kwargs: _Response())

    assert collection.get_meta_data() == {}


def test_volcengine_collection_get_meta_data_returns_empty_on_collection_not_found(
    monkeypatch,
):
    class _Response:
        status_code = 404
        text = "collection not found"

        @staticmethod
        def json():
            return {
                "ResponseMetadata": {
                    "Error": {
                        "Code": "NotFound.VikingdbCollection",
                        "Message": "The specified collection 'context' of VikingDB does not exist.",
                    }
                }
            }

    collection = VolcengineCollection(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        meta_data={"ProjectName": "default", "CollectionName": "context"},
    )
    monkeypatch.setattr(collection.console_client, "do_req", lambda *args, **kwargs: _Response())

    assert collection.get_meta_data() == {}
