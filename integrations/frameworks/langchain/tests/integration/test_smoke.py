from __future__ import annotations

import runpy
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples"


def test_langchain_quick_app_runs():
    namespace = runpy.run_path(
        str(EXAMPLES_ROOT / "langchain/rag/quick_app.py"),
        run_name="openviking_langchain_quick",
    )

    answer = namespace["main"]()
    assert "azure" in answer.lower()


def test_langchain_context_backend_quick_app_runs():
    namespace = runpy.run_path(
        str(EXAMPLES_ROOT / "langchain/context-backend/quick_app.py"),
        run_name="openviking_langchain_context_backend_quick",
    )

    answer = namespace["main"]()
    assert "openviking" in answer.lower()
    assert "azure" in answer.lower()


def test_langchain_message_history_quick_app_runs():
    namespace = runpy.run_path(
        str(EXAMPLES_ROOT / "langchain/message-history/quick_app.py"),
        run_name="openviking_langchain_message_history_quick",
    )

    answer = namespace["main"]()
    assert "history" in answer.lower()
    assert "azure" in answer.lower()


def test_langgraph_quick_app_runs():
    namespace = runpy.run_path(
        str(EXAMPLES_ROOT / "langgraph/agent/quick_app.py"),
        run_name="openviking_langgraph_quick",
    )

    answer = namespace["main"]()
    assert "openviking" in answer.lower()
    assert "azure" in answer.lower()


def test_langgraph_middleware_quick_app_runs():
    namespace = runpy.run_path(
        str(EXAMPLES_ROOT / "langgraph/middleware/quick_app.py"),
        run_name="openviking_langgraph_middleware_quick",
    )

    answer = namespace["main"]()
    assert "middleware" in answer.lower()
    assert "azure" in answer.lower()
