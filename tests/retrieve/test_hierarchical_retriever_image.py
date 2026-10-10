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
    retriever._rerank_client = object()
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
    assert FakeProxy.captured["limit"] == 2


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


class _ProviderEmbedder(MultimodalEmbedder):
    """Fake provider client whose capability follows the account config."""

    def __init__(self, supports_multimodal: bool):
        super().__init__()
        self.supports_multimodal = supports_multimodal
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
async def account_embedding_provider(monkeypatch):
    from openviking.config.binding import manager_over_source
    from openviking.config.embedding import AccountEmbeddingProvider
    from openviking.config.source import MemoryConfigSource
    from openviking.config.vector import AccountVectorConfigResolver
    from openviking_cli.utils.config import set_openviking_config
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig
    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    clients = []

    def create(config):
        client = _ProviderEmbedder(config.dense.input == "multimodal")
        clients.append(client)
        return client

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", create)
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.VikingDBManagerProxy",
        FakeProxy,
    )
    base = OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "model": "fake-multimodal",
                    "dimension": 1,
                    "provider": "openai",
                    "api_key": "cluster-key",
                    "input": "multimodal",
                }
            }
        }
    )
    set_openviking_config(base)
    manager = manager_over_source(MemoryConfigSource(), base_config=base)
    provider = AccountEmbeddingProvider(AccountVectorConfigResolver(manager), manager)
    await manager.initialize()
    try:
        yield manager, provider, clients
    finally:
        await provider.close()
        OpenVikingConfigSingleton.reset_instance()


def _image_query():
    return TypedQuery(
        query="",
        context_type=None,
        intent="",
        embedding_input=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}],
        image_query=True,
    )


@pytest.mark.asyncio
async def test_image_query_through_account_bound_multimodal_embedder(account_embedding_provider):
    _manager, provider, clients = account_embedding_provider
    retriever = HierarchicalRetriever(storage=object(), embedder=provider.bind("acc"))
    retriever._rerank_client = object()

    await retriever.retrieve(_image_query(), ctx=_ctx(), limit=1)

    assert clients[-1].seen == _image_query().embedding_input


@pytest.mark.asyncio
async def test_image_query_capability_follows_account_embedding_config(
    account_embedding_provider,
):
    manager, provider, clients = account_embedding_provider
    embedder = provider.bind("acc")
    retriever = HierarchicalRetriever(storage=object(), embedder=embedder)
    retriever._rerank_client = object()
    await retriever.retrieve(_image_query(), ctx=_ctx(), limit=1)

    await manager.patch_account(
        "acc",
        {
            "embedding": {
                "dense": {
                    "model": "fake-text",
                    "dimension": 1,
                    "input": "text",
                    "credentials": [{"provider": "openai", "api_key": "acc-key"}],
                }
            }
        },
        creating=True,
    )

    with pytest.raises(InvalidArgumentError, match="multimodal embedding"):
        await retriever.retrieve(_image_query(), ctx=_ctx(), limit=1)
    assert clients[0].closed
    assert not clients[-1].seen
