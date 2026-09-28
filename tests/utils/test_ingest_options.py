from openviking.utils.ingest_options import IngestOptions


def test_replace_with_empty_tags_has_no_tag_intent():
    assert IngestOptions.from_search_tags([], mode="replace") == IngestOptions()


def test_clear_without_tags_becomes_explicit_empty_replacement():
    assert IngestOptions.from_search_tags(None, mode="clear") == IngestOptions(
        search_tags=[],
        search_tag_mode="clear",
    )


def test_clear_ignores_supplied_tags():
    assert IngestOptions.from_search_tags(["team=search"], mode="clear") == IngestOptions(
        search_tags=[],
        search_tag_mode="clear",
    )


def test_deserialized_empty_replace_has_no_tag_intent():
    assert IngestOptions.from_value(
        {"search_tags": [], "search_tag_mode": "replace"}
    ) == IngestOptions()


def test_direct_empty_replace_is_normalized_when_consumed():
    assert IngestOptions.from_value(
        IngestOptions(search_tags=[], search_tag_mode="replace")
    ) == IngestOptions()
