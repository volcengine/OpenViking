import pytest

from openviking.utils.embedding_input import (
    EMBEDDING_TRUNCATION_SUFFIX,
    estimate_embedding_input_tokens,
    truncate_embedding_input,
)


@pytest.mark.parametrize(
    "text",
    [
        "plain text " * 200,
        "向量记忆" * 200,
        "mixed 向量 text " * 200,
    ],
)
def test_truncated_embedding_input_reserves_budget_for_suffix(text):
    max_tokens = 64

    result = truncate_embedding_input(text, max_tokens)

    assert result.endswith(EMBEDDING_TRUNCATION_SUFFIX)
    assert estimate_embedding_input_tokens(result) <= max_tokens
    assert truncate_embedding_input(result, max_tokens) == result


def test_embedding_input_within_budget_is_unchanged():
    text = "already small"

    assert truncate_embedding_input(text, 64) == text


def test_embedding_input_zero_budget_preserves_existing_marker_behavior():
    assert truncate_embedding_input("content", 0) == EMBEDDING_TRUNCATION_SUFFIX.lstrip()


def test_embedding_input_tight_budget_truncates_suffix_itself():
    result = truncate_embedding_input("content that does not fit", 1)

    assert result
    assert estimate_embedding_input_tokens(result) <= 1


def test_embedding_input_custom_suffix_stays_inside_budget():
    result = truncate_embedding_input("x" * 500, 20, suffix=" [cut]")

    assert result.endswith(" [cut]")
    assert estimate_embedding_input_tokens(result) <= 20
