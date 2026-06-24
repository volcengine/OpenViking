"""Schema coverage for rebuild support."""

from openviking.storage.collection_schemas import CollectionSchemas


def test_context_collection_contains_content_field_for_fulltext():
    schema = CollectionSchemas.context_collection("ctx", 8)
    field_names = {field["FieldName"] for field in schema["Fields"]}
    # content field is required for VikingDB FullText (bm25) search
    assert "content" in field_names
    # embedding_content is not a schema field
    assert "embedding_content" not in field_names
    # FullText config must reference the content field
    fulltext_cfg = schema.get("FullText", [])
    fulltext_fields = [ft["Field"] for ft in fulltext_cfg]
    assert "content" in fulltext_fields

    # Analyzer config must include tokenizer + stopwords filter
    content_cfg = next(ft for ft in fulltext_cfg if ft.get("Field") == "content")
    analyzer = content_cfg.get("Analyzer") or {}
    assert analyzer.get("Tokenizer") == "standard"
    assert analyzer.get("StopWordsFilters") == ["symbol"]
