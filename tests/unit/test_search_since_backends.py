import pytest
from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
    VolcengineApiKeyCollection,
)
from openviking.storage.vectordb.collection.volcengine_collection import VolcengineCollection
from openviking.storage.vectordb.index.local_index import LocalIndex
from openviking.storage.vectordb.utils.data_processor import DataProcessor
from openviking.utils.search_filters import merge_time_filter


class RecordingEngine:
    def __init__(self, rows):
        self.rows = rows
        self.filters = None

    def search(self, query_vector, limit, filters, sparse_raw_terms, sparse_values):
        self.filters = filters
        labels = []
        for label, value in self.rows:
            if "gt" in filters and value <= filters["gt"]:
                continue
            if "gte" in filters and value < filters["gte"]:
                continue
            if "lt" in filters and value >= filters["lt"]:
                continue
            if "lte" in filters and value > filters["lte"]:
                continue
            labels.append(label)
        return labels[:limit], [1.0] * min(len(labels), limit)


def local_index_with_records():
    processor = DataProcessor({"updated_at": {"FieldType": "date_time"}}, tz_policy="utc")
    rows = [
        (label, processor.convert_fields_dict_for_index({"updated_at": timestamp})["updated_at"])
        for label, timestamp in [
            (1, "2026-11-24T15:22:40.123Z"),
            (2, "2026-11-24T15:22:40.124Z"),
        ]
    ]
    index = LocalIndex.__new__(LocalIndex)
    index.field_type_converter = processor
    index.engine_proxy = RecordingEngine(rows)
    index.dense_search = None
    return index


@pytest.mark.parametrize(
    ("since", "until", "expected"),
    [
        ("2026-11-24T15:22:40.123000Z", None, [1, 2]),
        ("2026-11-24T15:22:40.123456Z", None, [2]),
        ("2026-11-24T23:22:40.123456+08:00", None, [2]),
        ("2026-11-24T15:22:40.123456Z", "2026-11-24T15:22:40.123999Z", []),
    ],
)
def test_local_index_forwards_correct_millisecond_bounds(since, until, expected):
    index = local_index_with_records()
    requested = merge_time_filter(None, since=since, until=until)
    labels, scores = index.search([1.0], filters=requested)
    assert labels == expected
    assert len(scores) == len(expected)


class OkResponse:
    status_code = 200

    def json(self):
        return {"result": {"data": []}}


class RecordingClient:
    def __init__(self):
        self.path = None
        self.body = None

    def do_req(self, method, path, *, req_body):
        assert method == "POST"
        self.path = path
        self.body = req_body
        return OkResponse()


@pytest.mark.parametrize("collection_type", [VolcengineCollection, VolcengineApiKeyCollection])
@pytest.mark.parametrize(
    ("since", "until", "expected"),
    [
        (
            "2026-11-24T15:22:40.123000Z",
            None,
            {"op": "time_range", "field": "updated_at", "gte": "2026-11-24T15:22:40.123Z"},
        ),
        (
            "2026-11-24T15:22:40.123456Z",
            None,
            {"op": "time_range", "field": "updated_at", "gt": "2026-11-24T15:22:40.123Z"},
        ),
        (
            "2026-11-24T15:22:40.123456Z",
            "2026-11-24T15:22:40.123999Z",
            {
                "op": "time_range",
                "field": "updated_at",
                "gt": "2026-11-24T15:22:40.123Z",
                "lte": "2026-11-24T15:22:40.123Z",
            },
        ),
    ],
)
def test_cloud_collection_sends_compiled_time_filter(collection_type, since, until, expected):
    collection = collection_type.__new__(collection_type)
    collection.project_name = "test"
    collection.collection_name = "test"
    collection.data_client = RecordingClient()
    collection._parse_search_result = lambda data: data
    query = merge_time_filter(None, since=since, until=until)

    collection.search_by_scalar("default", "updated_at", filters=query)

    assert collection.data_client.path == "/api/vikingdb/data/search/scalar"
    assert collection.data_client.body["filter"] == expected
