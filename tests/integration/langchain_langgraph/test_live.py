from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("openai")

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("OPENVIKING_LANGGRAPH_LIVE") != "1",
    reason="set OPENVIKING_LANGGRAPH_LIVE=1 and ARK_API_KEY to run the live lane",
)
def test_langgraph_live_lane_runs():
    assert os.environ.get("ARK_API_KEY"), "ARK_API_KEY is required for the live lane"
    namespace = runpy.run_path(
        str(PROJECT_ROOT / "examples/langgraph-agent/live_app.py"),
        run_name="openviking_langgraph_live",
    )

    answer = namespace["main"]()
    assert _looks_like_openviking_context_answer(answer)


def _looks_like_openviking_context_answer(answer: str) -> bool:
    normalized = answer.lower()
    context_terms = ("context", "recall", "memory", "agent", "llm", "上下文", "记忆", "召回")
    return "openviking" in normalized and any(term in normalized for term in context_terms)
