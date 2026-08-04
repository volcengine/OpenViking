# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise the installed base distribution without LangGraph dependencies."""

from __future__ import annotations

import sys

import langchain_openviking
from langchain_openviking import InMemoryOpenVikingClient, OpenVikingRetriever


def main() -> None:
    """Verify base exports, retrieval, and the optional-dependency error."""
    assert "OpenVikingContextMiddleware" not in langchain_openviking.__all__

    namespace: dict[str, object] = {}
    exec("from langchain_openviking import *", namespace)
    assert "OpenVikingRetriever" in namespace
    assert "OpenVikingContextMiddleware" not in namespace

    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Deploy production with ArgoCD."}
    )
    documents = OpenVikingRetriever(client=client, target_uri="viking://resources").invoke(
        "How do we deploy production?"
    )
    assert documents
    assert "ArgoCD" in documents[0].page_content

    try:
        from langchain_openviking import OpenVikingContextMiddleware  # noqa: F401
    except ImportError as exc:
        assert "langchain-openviking[langgraph]" in str(exc)
    else:
        raise AssertionError("middleware import succeeded without the langgraph extra")

    assert "openviking" not in sys.modules
    assert not any(
        name == "openviking_cli" or name.startswith("openviking_cli.") for name in sys.modules
    )


if __name__ == "__main__":
    main()
