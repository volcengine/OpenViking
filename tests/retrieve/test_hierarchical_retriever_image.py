import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve.types import TypedQuery
from openviking_cli.session.user_id import UserIdentifier


class FakeProxy:
    captured = {}

    def __init__(self, _storage, _ctx):
        pass

    @property
    def collection_name(self):
        return "test"

    async def collection_exists_bound(self):
        return True

    async def search_in_tenant(self, **kwargs):
        self.captured.update(kwargs)
        return [
            {
                "uri": "viking://resources/photos/cat.png",
                "context_type": "resource",
                "level": 2,
                "_score": 0.9,
                "abstract": "cat",
            },
            {
                "uri": "viking://resources/docs/cat.md",
                "context_type": "resource",
                "level": 2,
                "_score": 0.8,
                "abstract": "cat doc",
            },
        ]


class MultimodalEmbedder:
    supports_multimodal = True

    def __init__(self):
        self.seen = None

    def prepare_embedding_input(self, content):
        self.seen = content
        return content

    async def embed_async(self, content, is_query=False):
        return EmbedResult(dense_vector=[1.0])


class TextOnlyEmbedder(MultimodalEmbedder):
    supports_multimodal = False


def _ctx():
    return RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)


@pytest.mark.asyncio
async def test_image_query_uses_multimodal_input_without_filtering_non_images(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        FakeProxy,
    )
    embedder = MultimodalEmbedder()
    retriever = HierarchicalRetriever(storage=object(), embedder=embedder)
    query_input = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]

    result = await retriever.retrieve(
        TypedQuery(
            query="",
            context_type=None,
            intent="",
            embedding_input=query_input,
            image_query=True,
        ),
        ctx=_ctx(),
        limit=2,
    )

    assert embedder.seen == query_input
    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/photos/cat.png",
        "viking://resources/docs/cat.md",
    ]
    assert FakeProxy.captured["context_type"] == "resource"
    assert FakeProxy.captured["level"] == [2]
    assert FakeProxy.captured["limit"] == 50


@pytest.mark.asyncio
async def test_image_query_requires_multimodal_embedder(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        FakeProxy,
    )
    retriever = HierarchicalRetriever(storage=object(), embedder=TextOnlyEmbedder())

    with pytest.raises(InvalidArgumentError, match="multimodal embedding"):
        await retriever.retrieve(
            TypedQuery(
                query="",
                context_type=None,
                intent="",
                embedding_input=[
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
                ],
                image_query=True,
            ),
            ctx=_ctx(),
        )
