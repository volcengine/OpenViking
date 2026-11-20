import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig


class FailingEmbedder:
    async def embed_async(self, *_args, **_kwargs):
        raise AssertionError("keywords search must not call the embedder")


class FixedReranker:
    def rerank_batch(self, query, documents):
        assert query == "OAuth token"
        assert documents == ["OAuth token"]
        return [0.9]


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
@pytest.mark.parametrize(
    ("mode", "recall_limit", "expected_score"),
    [(RetrieverMode.QUICK, 5, 3.5), (RetrieverMode.THINKING, 10, 0.9)],
)
async def test_keywords_retrieval_uses_keyword_scores_or_reranks(
    monkeypatch, mode, recall_limit, expected_score
):
    storage = KeywordStorage()
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: FixedReranker(),
    )
    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=FailingEmbedder(),
        rerank_config=RerankConfig(ak="test", sk="test"),
    )

    result = await retriever.retrieve(
        TypedQuery("OAuth token", None, ""),
        ctx=_ctx(),
        limit=5,
        mode=mode,
        search_type="keywords",
    )

    assert [(hit.uri, hit.score) for hit in result.matched_contexts] == [
        ("viking://resources/oauth.md", expected_score)
    ]
    assert [(call["query"], call["limit"]) for call in storage.calls] == [
        ("OAuth token", recall_limit)
    ]
