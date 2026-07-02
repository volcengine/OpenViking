# Copyright (c) 2026 njuboy11
# SPDX-License-Identifier: AGPL-3.0
"""Regression test for empty-document handling in OpenAIRerankClient.

SiliconFlow rerank API silently drops empty documents from results. Before the
fix, this caused length mismatch and fallback to vector scores. After the fix,
empty docs are filtered out before the call and mapped back as 0.0 scores in
their original positions.
"""

from unittest.mock import MagicMock, patch

from openviking.models.rerank import OpenAIRerankClient


def _make_client() -> OpenAIRerankClient:
    return OpenAIRerankClient(
        api_key="test-key",
        api_base="https://siliconflow.example/rerank",
        model_name="BAAI/bge-reranker-v2-m3",
    )


def _mock_post(json_body):
    resp = MagicMock()
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def test_rerank_batch_with_empty_doc_keeps_original_length():
    """24 docs with one empty at index 7 — expect 24 scores, that index = 0.0."""
    client = _make_client()
    docs = [f"doc-{i}" if i != 7 else "" for i in range(24)]

    # SiliconFlow returns only 23 results (silently drops empty).
    mock_resp = _mock_post(
        {
            "results": [
                {"index": i, "relevance_score": 0.5 + i * 0.01}
                for i in range(23)
            ]
        }
    )

    with patch(
        "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_resp
    ):
        scores = client.rerank_batch("test query", docs)

    assert len(scores) == 24, f"expected 24 scores, got {len(scores)}"
    assert scores[7] == 0.0, f"empty doc at idx 7 should score 0.0, got {scores[7]}"
    # Other indices preserve their original positions.
    for i in range(24):
        if i == 7:
            continue
        assert scores[i] != 0.0, f"non-empty doc at idx {i} should not be 0.0"


def test_rerank_batch_top_n_set_to_non_empty_count():
    """Ensure top_n matches non_empty_docs length to avoid SiliconFlow truncation."""
    client = _make_client()
    docs = [f"doc-{i}" for i in range(24)]
    docs[3] = ""
    docs[11] = ""
    docs[19] = ""

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["body"] = json
        return _mock_post(
            {"results": [{"index": i, "relevance_score": 0.5} for i in range(21)]}
        )

    with patch(
        "openviking.models.rerank.openai_rerank.requests.post",
        side_effect=fake_post,
    ):
        scores = client.rerank_batch("q", docs)

    assert captured["body"]["top_n"] == 21, (
        f"top_n should be 21 (after filtering 3 empties), "
        f"got {captured['body']['top_n']}"
    )
    assert len(scores) == 24
    for empty_idx in (3, 11, 19):
        assert scores[empty_idx] == 0.0


def test_rerank_batch_all_empty_returns_zero_scores():
    """Defensive: every doc empty -> return 0.0 per original index, no API call."""
    client = _make_client()
    docs = ["", "  ", "\n\t", ""]

    with patch(
        "openviking.models.rerank.openai_rerank.requests.post"
    ) as mock_p:
        scores = client.rerank_batch("q", docs)

    # Short-circuit: no API call wasted, all slots get 0.0.
    assert scores == [0.0, 0.0, 0.0, 0.0]
    mock_p.assert_not_called()
