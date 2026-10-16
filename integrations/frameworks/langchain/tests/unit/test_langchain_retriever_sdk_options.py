from __future__ import annotations

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient

from langchain_openviking import OpenVikingRetriever


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("search_mode", ["find", "search"])
@pytest.mark.parametrize("session_id", [None, "review-session"])
async def test_retriever_passes_session_only_to_search(
    monkeypatch, asynchronous, search_mode, session_id
):
    requests = []

    async def request(self, method, path, **kwargs):
        requests.append((method, path, kwargs["json"]))
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "resources": [
                        {"uri": "viking://resources/release.md", "abstract": "Release checklist"}
                    ]
                },
            },
        )

    monkeypatch.setattr(AsyncHTTPClient, "_request", request)
    client_type = AsyncHTTPClient if asynchronous else SyncHTTPClient
    client = client_type(url="http://openviking.test", api_key="test")
    retriever = OpenVikingRetriever(
        **({"async_client": client} if asynchronous else {"client": client}),
        search_mode=search_mode,
        session_id=session_id,
        content_mode="abstract",
        score_threshold=0.5,
        tags=["env=test"],
    )
    try:
        documents = (
            await retriever.ainvoke("release") if asynchronous else retriever.invoke("release")
        )
    finally:
        await retriever.aclose()
        if asynchronous:
            await client.close()
        else:
            client.close()

    assert [document.page_content for document in documents] == ["Release checklist"]
    expected = {"query": "release", "target_uri": "", "limit": 10, "score_threshold": 0.5}
    expected["tags"] = ["env=test"]
    if search_mode == "search" and session_id is not None:
        expected["session_id"] = session_id
    assert requests == [("POST", f"/api/v1/search/{search_mode}", expected)]
