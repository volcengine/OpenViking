import pytest

from openviking.storage.expr import And, Eq, In, Or, PathScope, RawDSL
from openviking.storage.scope_filter import (
    build_scope_filter,
    infer_uri_scopes,
    merge_filters,
    specialize_visibility_filter,
)


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


@pytest.mark.parametrize("depth", [-1, 0, 1, 4])
def test_infer_uri_scopes_preserves_original_path_and_depth(depth):
    scope = PathScope("uri", "viking://resources/docs/", depth=depth)
    assert infer_uri_scopes(scope) == (scope,)


def test_infer_exact_uri_and_nested_positive_conditions():
    uri = "viking://resources/docs/a.md"
    raw = RawDSL({"op": "must_not", "field": "uri", "conds": ["/resources/private"]})
    request = And([raw, And([Eq("level", 2), Eq("uri", uri)])])
    assert infer_uri_scopes(request) == (PathScope("uri", uri, depth=0),)
    assert request.conds[0] is raw


def test_infer_or_requires_every_branch_and_deduplicates_bounds():
    first = PathScope("uri", "viking://resources/docs")
    second = PathScope("uri", "viking://user/alice/memories", depth=1)
    assert infer_uri_scopes(Or([first, And([Eq("level", 2), second]), first])) == (
        first,
        second,
    )
    assert infer_uri_scopes(Or([first, Eq("level", 2)])) is None
    assert infer_uri_scopes(Or([first, RawDSL({"op": "must", "field": "uri"})])) is None


@pytest.mark.parametrize(
    "filter_expr",
    [
        None,
        And([]),
        Or([]),
        Eq("account_id", "acct"),
        Eq("uri", 123),
        PathScope("parent_uri", "viking://resources/docs"),
        PathScope("uri", "viking://resources/docs", depth=-2),
        PathScope("uri", "viking://resources/docs", depth=True),
        PathScope("uri", "viking://resources/docs", depth=1.5),
        {"op": "must", "field": "uri", "conds": ["viking://resources/docs"]},
        RawDSL({"op": "must", "field": "uri", "conds": ["viking://resources/docs"]}),
    ],
)
def test_unknown_or_untyped_filters_do_not_prove_a_scope(filter_expr):
    assert infer_uri_scopes(filter_expr) is None


@pytest.mark.parametrize(
    "uri",
    [
        "/resources/docs",
        "viking://resources//docs",
        "viking://resources/../user/alice",
        "viking://resources/./docs",
        "viking://resources/docs?query=1",
        "viking://resources/docs#section",
        "viking://resources\\docs",
    ],
)
def test_noncanonical_uri_does_not_prove_a_scope(uri):
    assert infer_uri_scopes(PathScope("uri", uri)) is None
    assert infer_uri_scopes(Eq("uri", uri)) is None


def _visibility():
    return Or(
        [
            PathScope("uri", "viking://resources"),
            PathScope("uri", "viking://agent"),
            PathScope("uri", "viking://user/alice"),
        ]
    )


def test_specialization_removes_only_generated_paths():
    account = Eq("account_id", "acct")
    extra = Eq("search_tags", "team=one")
    request = And([PathScope("uri", "viking://resources/docs"), Eq("level", 2), extra])
    tenant = And([account, _visibility(), Eq("policy_version", "current")])
    specialized = specialize_visibility_filter(tenant, infer_uri_scopes(request))
    assert specialized == And([account, Eq("policy_version", "current")])
    merged = merge_filters(request, specialized)
    assert merged.conds[0] is request
    assert request.conds[2] is extra


def test_joint_root_coverage_keeps_account_for_multiple_namespaces():
    account = Eq("account_id", "acct")
    request = Or(
        [
            PathScope("uri", "viking://resources/docs"),
            PathScope("uri", "viking://user/alice/memories"),
        ]
    )
    assert (
        specialize_visibility_filter(And([account, _visibility()]), infer_uri_scopes(request))
        == account
    )


def test_shared_acl_is_retained_when_its_path_is_redundant():
    account = Eq("account_id", "acct")
    acl = RawDSL({"op": "must", "field": "acl_direct_grants", "conds": ["1:user:alice"]})
    tenant = And(
        [
            account,
            Or(
                [
                    PathScope("uri", "viking://user/alice"),
                    And([PathScope("uri", "viking://resources"), acl]),
                ]
            ),
        ]
    )
    result = specialize_visibility_filter(
        tenant, infer_uri_scopes(PathScope("uri", "viking://resources/docs"))
    )
    assert result == And([account, acl])
    assert result.conds[1] is acl


def test_false_unknown_and_finite_visibility_paths_do_not_become_unrestricted():
    tenant = And([Eq("account_id", "acct"), _visibility()])
    outside = infer_uri_scopes(PathScope("uri", "viking://resources-other/docs"))
    assert specialize_visibility_filter(tenant, outside) is tenant
    assert specialize_visibility_filter(tenant, None) is tenant
    ancestor = infer_uri_scopes(PathScope("uri", "viking://user"))
    result = specialize_visibility_filter(tenant, ancestor)
    assert result == And([Eq("account_id", "acct"), PathScope("uri", "viking://user/alice")])
    finite = PathScope("uri", "viking://resources/docs", depth=1)
    assert (
        specialize_visibility_filter(
            finite, infer_uri_scopes(Eq("uri", "viking://resources/docs/a.md"))
        )
        is finite
    )


def test_real_requested_root_is_retained_after_tenant_specialization():
    request = And([PathScope("uri", "viking://resources"), Eq("level", 2)])
    tenant = And([Eq("account_id", "acct"), _visibility()])
    result = merge_filters(request, specialize_visibility_filter(tenant, infer_uri_scopes(request)))
    assert result.conds[0] is request
    assert result.conds[0].conds[0] == PathScope("uri", "viking://resources")
