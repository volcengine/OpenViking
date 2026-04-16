"""Schema coverage for rebuild support."""

from openviking.storage.collection_schemas import CollectionSchemas


def test_context_collection_does_not_contain_rebuild_content_snapshot_field():
    schema = CollectionSchemas.context_collection("ctx", 8)
    field_names = {field["FieldName"] for field in schema["Fields"]}
    assert "content" not in field_names
    assert "embedding_content" not in field_names
