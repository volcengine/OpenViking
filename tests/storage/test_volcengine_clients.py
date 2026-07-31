from volcengine.base.Request import Request

from openviking.storage.vectordb.collection.volcengine_clients import (
    ClientForConsoleApi,
    ClientForDataApi,
    ClientForDataApiWithApiKey,
)
from openviking.storage.vectordb.collection.volcengine_collection import VolcengineCollection
from openviking.storage.vectordb_adapters.volcengine_adapter import VolcengineCollectionAdapter
from openviking_cli.utils.config.vectordb_config import (
    VectorDBBackendConfig,
    VolcengineConfig,
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


def test_volcengine_collection_update_data_posts_to_update_endpoint(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"result": {"updated": 1}}

    collection = VolcengineCollection(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        meta_data={"ProjectName": "default", "CollectionName": "context"},
    )

    def _fake_do_req(method, path=None, req_params=None, req_body=None):
        captured["method"] = method
        captured["path"] = path
        captured["req_params"] = req_params
        captured["req_body"] = req_body
        return _Response()

    monkeypatch.setattr(collection.data_client, "do_req", _fake_do_req)

    result = collection.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == {"updated": 1}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/vikingdb/data/update"
    assert captured["req_body"] == {
        "project": "default",
        "collection_name": "context",
        "data": [{"id": "doc-1", "name": "updated"}],
    }


def test_volcengine_collection_update_data_sanitizes_uri_fields(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"result": {"updated": 1}}

    collection = VolcengineCollection(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        meta_data={"ProjectName": "default", "CollectionName": "context"},
    )

    def _fake_do_req(method, path=None, req_params=None, req_body=None):
        captured["method"] = method
        captured["path"] = path
        captured["req_body"] = req_body
        return _Response()

    monkeypatch.setattr(collection.data_client, "do_req", _fake_do_req)

    collection.update_data(
        [{"id": "doc-1", "uri": "viking://resources/demo", "parent_uri": "viking://resources"}]
    )

    assert captured["path"] == "/api/vikingdb/data/update"
    assert captured["req_body"] == {
        "project": "default",
        "collection_name": "context",
        "data": [{"id": "doc-1", "uri": "/resources/demo", "parent_uri": "/resources"}],
    }


def test_volcengine_api_key_collection_update_data_posts_to_update_endpoint(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"result": {"updated": 1}}

    from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
        VolcengineApiKeyCollection,
    )

    collection = VolcengineApiKeyCollection(
        api_key="vk-test-token",
        region="cn-beijing",
        meta_data={"ProjectName": "default", "CollectionName": "context", "IndexName": "default"},
    )

    def _fake_do_req(method, req_path=None, req_params=None, req_body=None):
        captured["method"] = method
        captured["path"] = req_path
        captured["req_params"] = req_params
        captured["req_body"] = req_body
        return _Response()

    monkeypatch.setattr(collection.data_client, "do_req", _fake_do_req)

    result = collection.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == {"updated": 1}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/vikingdb/data/update"
    assert captured["req_body"] == {
        "project": "default",
        "collection_name": "context",
        "data": [{"id": "doc-1", "name": "updated"}],
    }


def test_volcengine_api_key_collection_update_data_sanitizes_uri_fields(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"result": {"updated": 1}}

    from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
        VolcengineApiKeyCollection,
    )

    collection = VolcengineApiKeyCollection(
        api_key="vk-test-token",
        region="cn-beijing",
        meta_data={"ProjectName": "default", "CollectionName": "context", "IndexName": "default"},
    )

    def _fake_do_req(method, req_path=None, req_params=None, req_body=None):
        captured["path"] = req_path
        captured["req_body"] = req_body
        return _Response()

    monkeypatch.setattr(collection.data_client, "do_req", _fake_do_req)

    collection.update_data(
        [{"id": "doc-1", "uri": "viking://resources/demo", "parent_uri": "viking://resources"}]
    )

    assert captured["path"] == "/api/vikingdb/data/update"
    assert captured["req_body"] == {
        "project": "default",
        "collection_name": "context",
        "data": [{"id": "doc-1", "uri": "/resources/demo", "parent_uri": "/resources"}],
    }


def test_volcengine_adapter_update_data_returns_ids():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}]
            return {"updated": 1, "primary_keys": ["doc-1"]}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]


def test_volcengine_adapter_update_data_returns_batch_primary_keys():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [
                {"id": "doc-1", "name": "updated-1"},
                {"id": "doc-2", "name": "updated-2"},
            ]
            return {"updated": 2, "primary_keys": ["doc-1", "doc-2"]}

    adapter._collection = _Collection()

    result = adapter.update_data(
        [
            {"id": "doc-1", "name": "updated-1"},
            {"id": "doc-2", "name": "updated-2"},
        ]
    )

    assert result == ["doc-1", "doc-2"]


def test_volcengine_adapter_update_data_returns_ids_from_ids_key():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}]
            return {"updated": 1, "ids": ["doc-1"]}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]


def test_volcengine_adapter_update_data_returns_batch_ids_from_ids_key():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [
                {"id": "doc-1", "name": "updated-1"},
                {"id": "doc-2", "name": "updated-2"},
            ]
            return {"updated": 2, "ids": ["doc-1", "doc-2"]}

    adapter._collection = _Collection()

    result = adapter.update_data(
        [
            {"id": "doc-1", "name": "updated-1"},
            {"id": "doc-2", "name": "updated-2"},
        ]
    )

    assert result == ["doc-1", "doc-2"]


def test_volcengine_adapter_update_data_falls_back_to_request_ids_when_backend_omits_them():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}]
            return {"updated": 1}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]


def test_volcengine_adapter_update_data_falls_back_to_batch_request_ids_when_backend_omits_them():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [
                {"id": "doc-1", "name": "updated-1"},
                {"id": "doc-2", "name": "updated-2"},
            ]
            return {"updated": 2}

    adapter._collection = _Collection()

    result = adapter.update_data(
        [
            {"id": "doc-1", "name": "updated-1"},
            {"id": "doc-2", "name": "updated-2"},
        ]
    )

    assert result == ["doc-1", "doc-2"]


def test_volcengine_adapter_update_data_falls_back_when_ids_key_is_not_a_list():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}]
            return {"updated": 1, "ids": "doc-1"}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]


def test_volcengine_adapter_update_data_falls_back_when_primary_keys_is_not_a_list():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}]
            return {"updated": 1, "primary_keys": "doc-1"}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]


def test_volcengine_adapter_update_data_returns_empty_when_backend_reports_zero_updates():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-404", "name": "updated"}]
            return {"updated": 0}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-404", "name": "updated"}])

    assert result == []


def test_volcengine_adapter_update_data_returns_empty_when_backend_dict_has_no_ids_or_updated_count():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-404", "name": "updated"}]
            return {"status": "ok"}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-404", "name": "updated"}])

    assert result == []


def test_volcengine_adapter_update_data_skips_records_without_id_in_fallback():
    adapter = VolcengineCollectionAdapter(
        ak="test-ak",
        sk="test-sk",
        region="cn-beijing",
        session_token=None,
        api_key=None,
        host=None,
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}, {"name": "missing-id"}]
            return {"updated": 2}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}, {"name": "missing-id"}])

    assert result == ["doc-1"]


def test_volcengine_adapter_update_data_supports_api_key_mode():
    adapter = VolcengineCollectionAdapter(
        ak=None,
        sk=None,
        region="cn-beijing",
        session_token=None,
        api_key="vk-test-token",
        host="api-vikingdb.vikingdb.cn-beijing.volces.com",
        project_name="default",
        collection_name="context",
        index_name="default",
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}]
            return {"updated": 1, "primary_keys": ["doc-1"]}

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]


def test_http_collection_update_data_posts_to_update_endpoint(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200
        text = '{"data": ["doc-1"]}'

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(
        "openviking.storage.vectordb.collection.http_collection.requests.post",
        _fake_post,
    )

    from openviking.storage.vectordb.collection.http_collection import HttpCollection

    collection = HttpCollection(
        ip="127.0.0.1",
        port=1933,
        meta_data={"ProjectName": "default", "CollectionName": "context"},
    )

    result = collection.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]
    assert captured["url"] == "http://127.0.0.1:1933/api/vikingdb/data/update"
    assert captured["json"] == {
        "project": "default",
        "collection_name": "context",
        "fields": '[{"id": "doc-1", "name": "updated"}]',
    }
