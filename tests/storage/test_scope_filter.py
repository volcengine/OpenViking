from openviking.storage.expr import And, Eq, In, Or, PathScope, RawDSL
from openviking.storage.scope_filter import build_scope_filter, merge_filters


def test_build_scope_filter_preserves_order_and_depth():
    result = build_scope_filter(
        context_type="resource",
        base_filter=Eq("account_id", "account"),
        target_directories=["viking://resources/docs"],
        target_depth=3,
        wrap_single_target=True,
        extra_filter={"op": "must", "field": "search_tags", "conds": ["env=prod"]},
        level=[2],
    )

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "account"),
            Or([PathScope("uri", "viking://resources/docs", depth=3)]),
            RawDSL({"op": "must", "field": "search_tags", "conds": ["env=prod"]}),
            In("level", [2]),
        ]
    )


def test_build_scope_filter_keeps_single_grep_target_unwrapped():
    assert build_scope_filter(
        target_directories=["viking://resources"],
        target_depth=4,
    ) == PathScope("uri", "viking://resources", depth=4)


def test_merge_filters_preserves_existing_empty_and_behavior():
    assert merge_filters(
        None,
        RawDSL({"op": "and", "conds": []}),
        {"op": "must", "field": "uri", "conds": ["viking://resources/a.md"]},
    ) == RawDSL({"op": "must", "field": "uri", "conds": ["viking://resources/a.md"]})
