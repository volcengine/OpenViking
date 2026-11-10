import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import TypedQuery
from openviking_cli.session.user_id import UserIdentifier


class FailingEmbedder:
    async def embed_async(self, *_args, **_kwargs):
        raise AssertionError("keywords search must not call the embedder")


class FixedReranker:
    def rerank_batch(self, _query, documents):
        return [0.9] * len(documents)


class KeywordStorage:
    def __init__(self):
        self.calls = []

    async def get_account_backend(self, _account_id):
        return self

    async def collection_exists(self):
        return True

    async def search_by_keywords_in_tenant(self, ctx, **kwargs):
        self.calls.append({"ctx": ctx, **kwargs})
        return [
            {
                "uri": "viking://resources/oauth.md",
                "context_type": "resource",
                "level": 2,
                "abstract": "OAuth token",
                "_score": 3.5,
            }
        ]


def _ctx():
    return RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)


@pytest.mark.asyncio
async def test_keywords_quick_search_skips_embedding_and_uses_keyword_recall():
    storage = KeywordStorage()
    retriever = HierarchicalRetriever(storage=storage, embedder=FailingEmbedder())

    result = await retriever.retrieve(
        TypedQuery("OAuth token", None, ""),
        ctx=_ctx(),
        limit=5,
        mode=RetrieverMode.QUICK,
        search_type="keywords",
    )

    assert [item.uri for item in result.matched_contexts] == ["viking://resources/oauth.md"]
    assert storage.calls[0]["query"] == "OAuth token"
    assert storage.calls[0]["limit"] == 5


@pytest.mark.asyncio
async def test_keywords_thinking_search_uses_global_keyword_recall_and_rerank():
    storage = KeywordStorage()
    retriever = HierarchicalRetriever(storage=storage, embedder=FailingEmbedder())
    retriever._rerank_client = FixedReranker()

    result = await retriever.retrieve(
        TypedQuery("OAuth token", None, ""),
        ctx=_ctx(),
        limit=5,
        mode=RetrieverMode.THINKING,
        search_type="keywords",
    )

    assert [item.uri for item in result.matched_contexts] == ["viking://resources/oauth.md"]
    assert len(storage.calls) == 1
    assert storage.calls[0]["query"] == "OAuth token"
    assert storage.calls[0]["limit"] == 10
