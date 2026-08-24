# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared model retry helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openviking.utils.model_retry as model_retry
from openviking.models.vlm.base import FailoverVLM, MultiCredentialVLM
from openviking.utils.exceptions import AllCredentialsFailedError
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_CONTENT_SAFETY,
    ERROR_CLASS_INPUT_TOO_LARGE,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_QUOTA_EXCEEDED,
    ERROR_CLASS_TRANSIENT,
    classify_api_error,
    retry_async,
    retry_sync,
)


def test_classify_api_error_recognizes_request_burst_too_fast():
    assert classify_api_error(RuntimeError("RequestBurstTooFast")) == ERROR_CLASS_TRANSIENT


def test_classify_all_credentials_failed_prefers_transient_over_auth():
    err = AllCredentialsFailedError(
        [
            ("cred-a", ERROR_CLASS_AUTH, RuntimeError("401 Unauthorized"), 0),
            ("cred-b", ERROR_CLASS_TRANSIENT, RuntimeError("500 server error"), 1),
        ]
    )
    assert classify_api_error(err) == ERROR_CLASS_TRANSIENT


def test_retry_sync_retries_transient_error_until_success():
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("429 TooManyRequests")
        return "ok"

    assert retry_sync(_call, max_retries=3) == "ok"
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_retry_async_does_not_retry_unknown_error():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        raise RuntimeError("some unexpected validation failure")

    with pytest.raises(RuntimeError):
        await retry_async(_call, max_retries=3)

    assert attempts["count"] == 1


# --- quota_exceeded classification ---


def test_classify_account_quota_exceeded():
    """AccountQuotaExceeded is classified as quota_exceeded, not transient."""
    error = RuntimeError(
        'API Error: 429 {"error":{"code":"AccountQuotaExceeded",'
        '"message":"You have exceeded the 5-hour usage quota"}}'
    )
    assert classify_api_error(error) == ERROR_CLASS_QUOTA_EXCEEDED


def test_classify_quota_limit():
    """'quota limit' is classified as quota_exceeded."""
    assert classify_api_error(RuntimeError("quota limit reached")) == ERROR_CLASS_QUOTA_EXCEEDED


def test_classify_quota_exceed():
    """'quota exceed' is classified as quota_exceeded."""
    assert classify_api_error(RuntimeError("quota exceed")) == ERROR_CLASS_QUOTA_EXCEEDED


def test_classify_usage_quota():
    """'usage quota' is classified as quota_exceeded."""
    assert classify_api_error(RuntimeError("usage quota exceeded")) == ERROR_CLASS_QUOTA_EXCEEDED


def test_quota_exceeded_takes_precedence_over_transient():
    """A 429 with AccountQuotaExceeded is quota_exceeded, not transient."""
    error = RuntimeError(
        '429 {"error":{"code":"AccountQuotaExceeded","message":"TooManyRequests"}}'
    )
    assert classify_api_error(error) == ERROR_CLASS_QUOTA_EXCEEDED


def test_auth_takes_precedence_over_quota():
    """Auth errors (e.g. 403) take precedence over the quota substring."""
    assert classify_api_error(RuntimeError("403 AccountQuotaExceeded")) == ERROR_CLASS_AUTH


# --- permanent vs auth split (400 vs 401/403) ---


def test_classify_400_is_permanent():
    """A 400 parameter error is request-level permanent (fail-fast)."""
    error = RuntimeError("Error code: 400 - invalid parameter `model`")
    assert classify_api_error(error) == ERROR_CLASS_PERMANENT


def test_classify_401_is_auth():
    """A 401 is a credential-level auth error (advances in multi-credential mode)."""
    assert classify_api_error(RuntimeError("Error code: 401 - Incorrect API key")) == (
        ERROR_CLASS_AUTH
    )


def test_classify_403_is_auth():
    """A 403 forbidden is a credential-level auth error."""
    assert classify_api_error(RuntimeError("403 forbidden")) == ERROR_CLASS_AUTH


def test_classify_unauthorized_is_auth():
    assert classify_api_error(RuntimeError("Unauthorized")) == ERROR_CLASS_AUTH


def test_classify_account_overdue_is_auth():
    assert classify_api_error(RuntimeError("AccountOverdue")) == ERROR_CLASS_AUTH


# --- content safety classification ---


def test_classify_content_filter_is_content_safety():
    assert classify_api_error(RuntimeError("content_filter triggered")) == (
        ERROR_CLASS_CONTENT_SAFETY
    )


def test_classify_content_policy_is_content_safety():
    error = RuntimeError("The response was rejected by the content policy")
    assert classify_api_error(error) == ERROR_CLASS_CONTENT_SAFETY


def test_content_safety_takes_precedence_over_400():
    """A moderation rejection containing '400' is content_safety, not permanent."""
    error = RuntimeError("Error code: 400 - content_filter: sensitive content detected")
    assert classify_api_error(error) == ERROR_CLASS_CONTENT_SAFETY


def test_retry_sync_does_not_retry_quota_exceeded():
    """Quota-exceeded errors should NOT be retried."""
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        raise RuntimeError("AccountQuotaExceeded")

    with pytest.raises(RuntimeError, match="AccountQuotaExceeded"):
        retry_sync(_call, max_retries=5)

    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_retry_async_does_not_retry_quota_exceeded():
    """Quota-exceeded errors should NOT be retried (async)."""
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        raise RuntimeError("AccountQuotaExceeded")

    with pytest.raises(RuntimeError, match="AccountQuotaExceeded"):
        await retry_async(_call, max_retries=5)

    assert attempts["count"] == 1


def test_quota_exceeded_case_insensitive():
    """Quota detection is case-insensitive."""
    assert classify_api_error(RuntimeError("QUOTA LIMIT")) == ERROR_CLASS_QUOTA_EXCEEDED
    assert classify_api_error(RuntimeError("Quota Exceed")) == ERROR_CLASS_QUOTA_EXCEEDED


@pytest.mark.parametrize(
    "message",
    [
        "BadRequestError: 400 maximum context length is 8192 tokens",
        "Error code: 413 - Payload Too Large",
        (
            "Error code: 500 - {'error': {'code': 500, 'message': "
            "'input (8525 tokens) is too large to process. increase the physical batch size "
            "(current batch size: 2048)', 'type': 'server_error'}}"
        ),
    ],
)
def test_classify_input_too_large_errors(message):
    assert classify_api_error(RuntimeError(message)) == ERROR_CLASS_INPUT_TOO_LARGE


def test_retry_sync_does_not_retry_input_too_large():
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        raise RuntimeError("expected maxLength: 50000, actual: 75000")

    with pytest.raises(RuntimeError, match="expected maxLength"):
        retry_sync(_call, max_retries=5)

    assert attempts["count"] == 1


# --- numeric pattern word-boundary tests ---


def test_429_with_request_id_containing_413_is_transient():
    """A 429 error whose request ID happens to contain '413' must NOT be
    misclassified as INPUT_TOO_LARGE (the original bug)."""
    error = RuntimeError(
        "Volcengine hybrid embedding failed: Error code: 429 - "
        "{'error': {'code': 'ModelAccountRpmRateLimitExceeded', "
        "'message': 'RPM limit exceeded', 'param': '', "
        "'type': 'TooManyRequests'}, "
        "'request_id': '0217801248873024288fe53d7c9130f34413480585e683685bc95'}"
    )
    assert classify_api_error(error) == ERROR_CLASS_TRANSIENT


def test_429_with_hyphenated_request_id_containing_413_is_transient():
    """Numeric status codes must not match hyphen-delimited request ID fragments."""
    error = RuntimeError(
        "Volcengine hybrid embedding failed: Error code: 429 - "
        "{'error': {'code': 'ModelAccountRpmRateLimitExceeded', "
        "'message': 'RPM limit exceeded', 'type': 'TooManyRequests'}, "
        "'request_id': 'req-413-abcd'}"
    )
    assert classify_api_error(error) == ERROR_CLASS_TRANSIENT


def test_numeric_status_code_inside_longer_number_is_not_matched():
    """Status code patterns must not match inside longer numbers
    (e.g. '400' must not match '1400')."""
    assert classify_api_error(RuntimeError("status: 1400 OK")) == "unknown"
    assert classify_api_error(RuntimeError("status: 5020 OK")) == "unknown"


def test_numeric_status_code_with_compact_error_code_context_still_matches():
    assert classify_api_error(RuntimeError("Error code:413-Payload Too Large")) == (
        ERROR_CLASS_INPUT_TOO_LARGE
    )


def _marker_helpers():
    """Resolve the production marker API at test time so missing code is a RED assertion."""
    mark = getattr(model_retry, "mark_vlm_error_non_retryable", None)
    check = getattr(model_retry, "is_vlm_error_non_retryable", None)
    assert callable(mark), "model_retry must define mark_vlm_error_non_retryable"
    assert callable(check), "model_retry must define is_vlm_error_non_retryable"
    return mark, check


def test_non_retryable_marker_round_trips_without_replacing_exception():
    mark, check = _marker_helpers()
    error = RuntimeError("partial stream")

    assert mark(error) is error
    assert check(error) is True


class _MarkerAssignmentRejectingError(RuntimeError):
    def __setattr__(self, name, value):
        if name == "_openviking_vlm_non_retryable":
            raise RuntimeError("instance marker assignment denied")
        super().__setattr__(name, value)


def test_non_retryable_marker_wraps_assignment_rejecting_exception_without_leak():
    mark, check = _marker_helpers()
    original = _MarkerAssignmentRejectingError("SENTINEL-MARKER-SECRET")

    wrapped = mark(original)

    assert wrapped is not original
    assert wrapped.__cause__ is original
    assert vars(type(wrapped)).get("_openviking_vlm_non_retryable") is True
    assert "_openviking_vlm_non_retryable" not in vars(wrapped)
    assert check(wrapped) is True
    assert "SENTINEL-MARKER-SECRET" not in str(wrapped)
    assert "SENTINEL-MARKER-SECRET" not in repr(wrapped)


def test_non_retryable_marker_traverses_both_edges_and_cycles_by_identity():
    mark, check = _marker_helpers()
    root = RuntimeError("root")
    cause = RuntimeError("cause")
    context = RuntimeError("context")
    marked = RuntimeError("marked")
    root.__cause__ = cause
    root.__context__ = context
    cause.__context__ = root
    context.__cause__ = marked
    marked.__context__ = cause
    mark(marked)

    assert check(root) is True


@pytest.mark.parametrize("child_index", [0, 1])
def test_non_retryable_marker_traverses_all_nested_aggregate_children(child_index):
    mark, check = _marker_helpers()
    leaf = RuntimeError(f"marked-child-{child_index}")
    mark(leaf)
    nested = AllCredentialsFailedError([("nested", ERROR_CLASS_TRANSIENT, leaf, 1)])
    children = [RuntimeError("plain"), nested]
    if child_index == 0:
        children.reverse()
    root = AllCredentialsFailedError(
        [
            ("first", ERROR_CLASS_TRANSIENT, children[0], 1),
            ("second", ERROR_CLASS_AUTH, children[1], 0),
        ]
    )

    assert check(root) is True
    assert (
        check(AllCredentialsFailedError([("plain", ERROR_CLASS_AUTH, RuntimeError("x"), 0)]))
        is False
    )


def test_non_retryable_marker_makes_both_boolean_classifiers_fail_closed():
    mark, _check = _marker_helpers()
    error = RuntimeError("429 TooManyRequests")
    mark(error)

    assert model_retry.is_retryable_api_error(error) is False
    assert model_retry.is_retryable_rate_limit_error(error) is False
    assert classify_api_error(error) == ERROR_CLASS_TRANSIENT


def test_retry_sync_rethrows_marked_error_before_custom_callback_or_side_effects():
    mark, _check = _marker_helpers()
    error = RuntimeError("503 partial stream")
    mark(error)
    operation = MagicMock(side_effect=error)
    callback = MagicMock(return_value=True)
    logger = MagicMock()

    with (
        patch.object(model_retry, "_compute_delay") as compute_delay,
        patch.object(model_retry.time, "sleep") as sleep,
        pytest.raises(RuntimeError) as raised,
    ):
        retry_sync(
            operation,
            max_retries=3,
            is_retryable=callback,
            logger=logger,
        )

    assert raised.value is error
    operation.assert_called_once_with()
    callback.assert_not_called()
    compute_delay.assert_not_called()
    logger.warning.assert_not_called()
    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_retry_async_rethrows_marked_error_before_custom_callback_or_side_effects():
    mark, _check = _marker_helpers()
    error = RuntimeError("503 partial async stream")
    mark(error)
    operation = MagicMock()

    async def _operation():
        operation()
        raise error

    callback = MagicMock(return_value=True)
    logger = MagicMock()

    with (
        patch.object(model_retry, "_compute_delay") as compute_delay,
        patch.object(asyncio, "sleep") as sleep,
        pytest.raises(RuntimeError) as raised,
    ):
        await retry_async(
            _operation,
            max_retries=3,
            is_retryable=callback,
            logger=logger,
        )

    assert raised.value is error
    operation.assert_called_once_with()
    callback.assert_not_called()
    compute_delay.assert_not_called()
    logger.warning.assert_not_called()
    sleep.assert_not_called()


class _CountedGraphError(RuntimeError):
    edge_reads = 0
    marker_reads = 0

    def __getattribute__(self, name):
        if name in {"__cause__", "__context__"}:
            type(self).edge_reads += 1
        elif "non_retryable" in name:
            type(self).marker_reads += 1
        return super().__getattribute__(name)


class _ThrowingGraphError(_CountedGraphError):
    def __init__(self, edge):
        super().__init__(f"unreadable {edge}")
        self.edge = edge

    def __getattribute__(self, name):
        if name == object.__getattribute__(self, "edge"):
            raise RuntimeError(f"cannot read {name}")
        return super().__getattribute__(name)


class _CountedErrors(list):
    def __init__(self, values):
        super().__init__(values)
        self.reads = 0

    def __iter__(self):
        for item in super().__iter__():
            self.reads += 1
            yield item


class _DeceptiveErrors(list):
    def __init__(self):
        self.reads = 0

    def __len__(self):
        return 1

    def __iter__(self):
        for index in range(258):
            self.reads += 1
            if self.reads == 258:
                raise AssertionError("aggregate child 258 must not be read")
            yield (str(index), ERROR_CLASS_TRANSIENT, RuntimeError(str(index)), index)


class _UnreadableAggregate(AllCredentialsFailedError):
    @property
    def errors(self):
        raise RuntimeError("aggregate errors are unreadable")

    @errors.setter
    def errors(self, _value):
        pass


def _aggregate_with_errors(errors):
    aggregate = AllCredentialsFailedError.__new__(AllCredentialsFailedError)
    Exception.__init__(aggregate, "synthetic aggregate")
    aggregate.errors = errors
    return aggregate


def _deep_graph(size):
    nodes = [_CountedGraphError(str(index)) for index in range(size)]
    for parent, child in zip(nodes, nodes[1:], strict=False):
        parent.__cause__ = child
    return nodes[0]


def _reset_graph_counters():
    _CountedGraphError.edge_reads = 0
    _CountedGraphError.marker_reads = 0


def _edge_overflow_graph():
    nodes = [_CountedGraphError(str(index)) for index in range(128)]
    errors = _CountedErrors(
        [(str(index), ERROR_CLASS_TRANSIENT, nodes[index % 128], index) for index in range(256)]
    )
    root = _aggregate_with_errors(errors)
    root.__cause__, root.__context__ = nodes[:2]
    for node in nodes:
        node.__cause__ = root
        node.__context__ = root
    return root, errors


def test_non_retryable_marker_graph_depth_over_256_fails_closed_with_bounded_reads():
    _mark, check = _marker_helpers()
    _reset_graph_counters()

    assert check(_deep_graph(257)) is True
    assert 0 < _CountedGraphError.marker_reads <= 256
    assert _CountedGraphError.edge_reads <= 512


def test_non_retryable_marker_graph_cycle_exactly_at_256_nodes_is_bounded_and_unmarked():
    _mark, check = _marker_helpers()
    nodes = [_CountedGraphError(str(index)) for index in range(256)]
    for parent, child in zip(nodes, nodes[1:] + nodes[:1], strict=True):
        parent.__cause__ = child
        parent.__context__ = child
    _reset_graph_counters()

    assert check(nodes[0]) is False
    assert 0 < _CountedGraphError.marker_reads <= 256
    assert _CountedGraphError.edge_reads == 512


def test_non_retryable_marker_aggregate_exactly_256_children_is_allowed():
    _mark, check = _marker_helpers()
    leaf = _CountedGraphError("shared leaf")
    errors = _CountedErrors(
        [(str(index), ERROR_CLASS_TRANSIENT, leaf, index) for index in range(256)]
    )
    _reset_graph_counters()

    assert check(_aggregate_with_errors(errors)) is False
    assert errors.reads == 256
    assert 0 < _CountedGraphError.marker_reads <= 256


def test_non_retryable_marker_aggregate_257_children_fails_closed_without_overread():
    _mark, check = _marker_helpers()
    errors = _CountedErrors(
        [
            (str(index), ERROR_CLASS_TRANSIENT, RuntimeError(str(index)), index)
            for index in range(257)
        ]
    )

    assert check(_aggregate_with_errors(errors)) is True
    assert errors.reads <= 256


def test_non_retryable_marker_deceptive_aggregate_stops_at_child_257():
    _mark, check = _marker_helpers()
    errors = _DeceptiveErrors()

    assert len(errors) == 1
    assert check(_aggregate_with_errors(errors)) is True
    assert errors.reads == 257


def test_non_retryable_marker_real_graph_over_512_edges_fails_closed_at_hard_bound():
    _mark, check = _marker_helpers()
    root, root_errors = _edge_overflow_graph()
    _reset_graph_counters()

    assert check(root) is True
    assert root_errors.reads > 0
    assert _CountedGraphError.marker_reads <= 256
    assert root_errors.reads + _CountedGraphError.edge_reads <= 512


@pytest.mark.parametrize(
    "aggregate",
    [
        _aggregate_with_errors([("too", "short")]),
        _aggregate_with_errors([("id", ERROR_CLASS_TRANSIENT, "not-an-exception", 0)]),
        _UnreadableAggregate([]),
        _ThrowingGraphError("__cause__"),
        _ThrowingGraphError("__context__"),
    ],
    ids=[
        "malformed-tuple",
        "non-exception-child",
        "unreadable-errors",
        "throwing-cause",
        "throwing-context",
    ],
)
def test_non_retryable_marker_unreadable_or_malformed_graph_fails_closed(aggregate):
    _mark, check = _marker_helpers()
    assert check(aggregate) is True


def test_non_retryable_marker_noniterable_errors_fails_closed():
    _mark, check = _marker_helpers()
    assert check(_aggregate_with_errors(None)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "depth",
        "edges",
        "aggregate",
        "unreadable",
        "malformed",
        "non-exception",
        "cause",
        "context",
        "noniterable",
    ],
)
async def test_graph_failure_rethrows_before_callbacks_logs_delays_or_second_operation(kind):
    _mark, _check = _marker_helpers()
    error = {
        "depth": lambda: _deep_graph(257),
        "edges": lambda: _edge_overflow_graph()[0],
        "aggregate": lambda: _aggregate_with_errors(
            [("id", ERROR_CLASS_TRANSIENT, RuntimeError(), 0)] * 257
        ),
        "unreadable": lambda: _UnreadableAggregate([]),
        "malformed": lambda: _aggregate_with_errors([("too", "short")]),
        "non-exception": lambda: _aggregate_with_errors([("id", ERROR_CLASS_TRANSIENT, "bad", 0)]),
        "cause": lambda: _ThrowingGraphError("__cause__"),
        "context": lambda: _ThrowingGraphError("__context__"),
        "noniterable": lambda: _aggregate_with_errors(None),
    }[kind]()
    sync_operation = MagicMock(side_effect=error)
    async_operation = AsyncMock(side_effect=error)
    callback = MagicMock(return_value=True)
    logger = MagicMock()

    with (
        patch.object(model_retry, "_compute_delay") as delay,
        patch.object(model_retry.time, "sleep") as sync_sleep,
        patch.object(asyncio, "sleep") as async_sleep,
        pytest.raises(type(error)) as sync_raised,
    ):
        retry_sync(sync_operation, max_retries=3, is_retryable=callback, logger=logger)
    with pytest.raises(type(error)) as async_raised:
        await retry_async(async_operation, max_retries=3, is_retryable=callback, logger=logger)

    assert sync_raised.value is error
    assert async_raised.value is error
    sync_operation.assert_called_once_with()
    async_operation.assert_awaited_once_with()
    callback.assert_not_called()
    delay.assert_not_called()
    logger.warning.assert_not_called()
    sync_sleep.assert_not_called()
    async_sleep.assert_not_called()


def _preflight_target(name, stream):
    target = MagicMock(model=name, provider="openai", thinking=False, stream=stream)
    for method in ("get_completion", "get_vision_completion"):
        setattr(target, method, MagicMock())
        setattr(target, f"{method}_async", AsyncMock())
    target._build_text_kwargs = MagicMock()
    target._build_vision_kwargs = MagicMock()
    target.get_client = MagicMock()
    target.get_async_client = MagicMock()
    target._prepare_image = MagicMock()
    return target


class _UnreadablePreflightTarget:
    model = "unreadable"
    provider = "openai"
    thinking = False

    def __init__(self, attribute):
        self.attribute = attribute

    def __getattribute__(self, name):
        if name == object.__getattribute__(self, "attribute"):
            raise RuntimeError(f"cannot read {name}")
        return object.__getattribute__(self, name)


class _UnreadableValidatorFailover(FailoverVLM):
    @property
    def _validate_stream_request(self):
        raise RuntimeError("cannot read _validate_stream_request")


def _preflight_wrapper(kind, targets):
    if kind == "failover":
        return FailoverVLM(targets[0], targets[1])
    return MultiCredentialVLM(targets, [str(index) for index in range(len(targets))])


def _preflight_state(wrapper):
    switcher = wrapper._switcher
    names = ("_using_backup", "_switch_to_backup_time", "_backup_request_count")
    if isinstance(wrapper, MultiCredentialVLM):
        names = ("_active_idx", "_last_switch_time", "_active_request_count")
    return tuple(getattr(switcher, name) for name in names)


def _safe_preflight_graph(kind, cyclic):
    targets = [_preflight_target(str(index), False) for index in range(4)]
    tracked = list(targets)
    deep = MultiCredentialVLM(targets[2:], ["deep-a", "deep-b"])
    nested = FailoverVLM(targets[1], deep)
    wrapper = _preflight_wrapper(kind, [targets[0], nested])
    if cyclic:
        deep._vlm_instances[1] = wrapper
    return wrapper, tracked


def _unsafe_preflight_graph(kind, case):
    safe = _preflight_target("safe", False)
    tracked = [safe]
    if case in {"deep-unsafe", "deep-heterogeneous"}:
        unsafe = _preflight_target("deep-unsafe", True)
        sibling = _preflight_target("deep-safe", False)
        tracked.extend((unsafe, sibling))
        deep = (
            FailoverVLM(sibling, unsafe)
            if case == "deep-unsafe"
            else MultiCredentialVLM([sibling, unsafe], ["deep-safe", "deep-unsafe"])
        )
        middle = FailoverVLM(safe, deep)
        targets = [safe, middle]
    elif case == "unreadable-stream":
        targets = [safe, _UnreadablePreflightTarget("stream")]
    elif case == "unreadable-validator":
        extra = _preflight_target("validator-child", False)
        tracked.append(extra)
        target = _UnreadableValidatorFailover(safe, extra)
        targets = [safe, target]
    elif case == "malformed-target":
        targets = [safe, None]
    else:
        many = [_preflight_target(str(index), False) for index in range(257)]
        tracked.extend(many)
        targets = [safe, MultiCredentialVLM(many, [str(index) for index in range(257)])]
    return _preflight_wrapper(kind, targets), tracked


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["failover", "multi"])
@pytest.mark.parametrize(
    "method",
    [
        "get_completion",
        "get_completion_async",
        "get_vision_completion",
        "get_vision_completion_async",
    ],
)
@pytest.mark.parametrize("cyclic", [False, True], ids=["deep-safe", "cyclic-safe"])
async def test_wrapper_tool_stream_preflight_all_safe_graph_allows_exactly_one_provider(
    kind, method, cyclic
):
    wrapper, targets = _safe_preflight_graph(kind, cyclic)
    before = _preflight_state(wrapper)
    kwargs = {"prompt": "x", "tools": [{"type": "function"}]}
    if "vision" in method:
        kwargs["images"] = []

    result = getattr(wrapper, method)(**kwargs)
    if method.endswith("_async"):
        await result

    assert sum(getattr(target, method).call_count for target in targets) == 1
    assert _preflight_state(wrapper) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["failover", "multi"])
@pytest.mark.parametrize(
    "method",
    [
        "get_completion",
        "get_completion_async",
        "get_vision_completion",
        "get_vision_completion_async",
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        "deep-unsafe",
        "deep-heterogeneous",
        "unreadable-stream",
        "unreadable-validator",
        "malformed-target",
        "target-overflow",
    ],
)
async def test_wrapper_tool_stream_preflight_rejects_unsafe_deep_or_malformed_graph_before_side_effects(
    kind, method, case
):
    wrapper, targets = _unsafe_preflight_graph(kind, case)
    if kind == "failover":
        selection = MagicMock(side_effect=AssertionError("selector reached"))
        wrapper._switcher.should_try_primary = selection
        credential_index = None
    else:
        selection = MagicMock(side_effect=AssertionError("selector reached"))
        credential_index = MagicMock(side_effect=AssertionError("credential index reached"))
        wrapper._switcher.maybe_failback = selection
        wrapper._switcher.get_active_index = credential_index
    kwargs = {"prompt": "x", "tools": [{"type": "function"}]}
    if "vision" in method:
        kwargs["images"] = [object()]

    with pytest.raises(NotImplementedError, match="stream.*tools|tools.*stream"):
        result = getattr(wrapper, method)(**kwargs)
        if method.endswith("_async"):
            await result

    selection.assert_not_called()
    if credential_index is not None:
        credential_index.assert_not_called()
    for target in targets:
        getattr(target, method).assert_not_called()
        target._build_text_kwargs.assert_not_called()
        target._build_vision_kwargs.assert_not_called()
        target.get_client.assert_not_called()
        target.get_async_client.assert_not_called()
        target._prepare_image.assert_not_called()
