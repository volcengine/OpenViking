# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Relation expansion through the real write -> index -> search path (#4645).

Fixed corpus, deterministic keyword embedder and keyword reranker:

* A is the only document the query's vocabulary reaches; it links to B and C.
* B uses different words (low recall similarity), but the reranker judges it
  relevant, as a cross-encoder would.
* C is linked from A but irrelevant to the query.
* Fillers soak up the recall budget so baseline retrieval never sees B.
"""

import math
from pathlib import Path

import pytest
import pytest_asyncio

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import set_task_tracker
from openviking.storage import viking_fs as viking_fs_module
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.config.vlm_config import VLMConfig

VOCAB = ["jwt", "login", "filler", "signature", "clock", "garden", "secret"]
QUERY = "jwt login"
ROOT = "viking://resources/kb"
A, B, C = f"{ROOT}/a.md", f"{ROOT}/b.md", f"{ROOT}/c.md"
A_BODY = "jwt login flow."
B_BODY = "signature clock skew rules."
C_BODY = "garden notes."
A2_URI = f"{ROOT}/a2.md"
A2_BODY = "jwt notes."
B2_BODY = "signature rotation."
FILLERS = ["login filler" + " filler" * i + "." for i in range(8)]
BODIES = [A_BODY, B_BODY, C_BODY, A2_BODY, B2_BODY, *FILLERS]


def _vector(text: str) -> list[float]:
    words = text.lower().replace(".", " ").replace(",", " ").split()
    counts = [float(words.count(term)) for term in VOCAB] + [0.05]
    norm = math.sqrt(sum(v * v for v in counts))
    return [v / norm for v in counts]


class KeywordEmbedder(DenseEmbedderBase):
    def __init__(self):
        super().__init__(model_name="keyword-test-embedder")

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        return EmbedResult(dense_vector=_vector(text))

    def get_dimension(self) -> int:
        return len(VOCAB) + 1


class KeywordReranker:
    """Deterministic stand-in for a cross-encoder: judges abstracts, not vectors."""

    def __init__(self):
        self.calls = []

    def rerank_batch(self, query, documents):
        self.calls.append(list(documents))
        return [
            0.95 if "jwt login" in d else 0.9 if "jwt" in d else 0.8 if "signature" in d else 0.1
            for d in documents
        ]


def _doc(body: str, related=()) -> str:
    if not related:
        return body
    links = "".join(f"  - {uri}\n" for uri in related)
    return f"---\nrelated:\n{links}---\n{body}"


async def _make_service(workspace: Path, monkeypatch, *, enabled: bool, **limits):
    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: KeywordEmbedder())
    reranker = KeywordReranker()

    async def summarize(self, prompt, thinking=False):
        # A faithful summarizer: the abstract restates the file body.
        return next((body for body in BODIES if body in prompt), "unrelated summary.")

    monkeypatch.setattr(VLMConfig, "is_available", lambda self: True)
    monkeypatch.setattr(VLMConfig, "get_completion_async", summarize)
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: reranker,
    )
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {
                "workspace": str(workspace),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": len(VOCAB) + 1,
                }
            },
            "rerank": {
                "provider": "openai",
                "api_key": "k",
                "api_base": "http://rerank.invalid",
                "model": "m",
                "threshold": 0.5,
            },
            "retrieval": {"relation_expansion": {"enabled": enabled, **limits}},
        }
    )
    service = OpenVikingService(path=str(workspace), user=UserIdentifier.the_default_user())
    await service.initialize()
    return service, reranker


CTX = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)


@pytest_asyncio.fixture
async def make_service(tmp_path, monkeypatch):
    previous = viking_fs_module._instance
    services = []

    async def factory(enabled=True, **limits):
        for service in services:
            await service.close()
        services.clear()
        workspace = tmp_path / f"ws{len(list(tmp_path.iterdir()))}"
        service, reranker = await _make_service(workspace, monkeypatch, enabled=enabled, **limits)
        services.append(service)
        return service, reranker

    yield factory
    for service in services:
        await service.close()
    set_task_tracker(None)
    viking_fs_module._instance = previous
    OpenVikingConfigSingleton.reset_instance()


async def _write(service, uri, content, ctx=CTX, **kwargs):
    await service.fs.write(uri, content, ctx=ctx, wait=True, **kwargs)


async def _corpus(service, *, fillers=8, a_links=(B, C)):
    await _write(service, A, _doc(A_BODY, related=a_links))
    await _write(service, B, B_BODY)
    await _write(service, C, C_BODY)
    for i in range(fillers):
        await _write(service, f"{ROOT}/f{i}.md", FILLERS[i])


def _uris(result):
    return [m.uri for m in result.resources]


async def _search(service, ctx=CTX, **kwargs):
    kwargs.setdefault("limit", 3)
    return await service.search.search(QUERY, ctx=ctx, level=[2], **kwargs)


def _reranked(reranker):
    return [doc for call in reranker.calls for doc in call]


async def test_baseline_finds_a_but_misses_b(make_service):
    service, reranker = await make_service(enabled=False)
    await _corpus(service)

    result = await _search(service)

    assert _uris(result) == [A]
    assert B_BODY not in _reranked(reranker)


async def test_linked_relevant_b_is_added_and_irrelevant_c_is_not(make_service):
    service, reranker = await make_service(enabled=False)
    await _corpus(service)
    baseline = (await _search(service)).resources

    service, reranker = await make_service(enabled=True)
    await _corpus(service)
    result = await _search(service)

    assert _uris(result) == [A, B]
    a, b = result.resources
    assert (a.uri, a.score, a.related_from) == (baseline[0].uri, baseline[0].score, None)
    assert b.related_from == A and b.score == pytest.approx(0.8)
    # C was fetched and judged like any candidate; being linked was not enough.
    assert C_BODY in _reranked(reranker)
    assert C not in _uris(result)
    # Provenance reaches the JSON API, nothing else is attached to the hit.
    payload = result.to_dict()["resources"]
    assert [r.get("related_from") for r in payload] == [None, A]


async def test_expanded_hit_reaches_llm_facing_search_tool(make_service):
    from openviking.server.dependencies import set_service
    from openviking.server.mcp_endpoint import _mcp_ctx, search

    service, _ = await make_service(enabled=True)
    await _corpus(service)
    set_service(service)
    token = _mcp_ctx.set(CTX)
    try:
        text = await search(QUERY, level=[2], limit=3, min_score=0.5, read_content=True)
    finally:
        _mcp_ctx.reset(token)
        set_service(None)

    assert B in text and B_BODY in text
    # A's own body (read_content) lists its links; C itself is never a hit.
    assert f"] {C}" not in text and C_BODY not in text
    # Provenance stays out of LLM-facing text: no new injection surface.
    assert "related_from" not in text


async def test_quick_find_is_unchanged_by_expansion(make_service):
    service, _ = await make_service(enabled=False)
    await _corpus(service)
    baseline = [
        (m.uri, m.score)
        for m in (await service.search.find(QUERY, ctx=CTX, level=[2], limit=3)).resources
    ]

    service, _ = await make_service(enabled=True)
    await _corpus(service)
    found = (await service.search.find(QUERY, ctx=CTX, level=[2], limit=3)).resources

    assert [(m.uri, m.score) for m in found] == baseline
    assert all(m.related_from is None for m in found)
    assert B not in [m.uri for m in found]


async def test_only_relevance_qualified_seeds_are_expanded(make_service):
    service, reranker = await make_service(enabled=True)
    await _corpus(service, a_links=())
    # A filler is recalled but fails the rerank threshold, so its link is never followed.
    await _write(service, f"{ROOT}/f0.md", _doc(FILLERS[0], related=[B]))

    result = await _search(service)

    assert _uris(result) == [A]
    assert B_BODY not in _reranked(reranker)


@pytest.mark.parametrize(("max_seeds", "expected"), [(1, [A, A2_URI]), (2, [A, A2_URI, B])])
async def test_seed_budget(make_service, max_seeds, expected):
    service, _ = await make_service(enabled=True, max_seeds=max_seeds)
    await _corpus(service, a_links=())
    await _write(service, A2_URI, _doc(A2_BODY, related=[B]))

    result = await _search(service)

    assert _uris(result) == expected


async def test_edge_budget(make_service):
    service, reranker = await make_service(enabled=True, max_edges=1)
    await _corpus(service, a_links=(C, B))

    result = await _search(service)

    assert _uris(result) == [A]
    assert B_BODY not in _reranked(reranker)


async def test_added_candidate_budget(make_service):
    service, _ = await make_service(enabled=True, max_added=1)
    b2 = f"{ROOT}/b2.md"
    await _corpus(service, a_links=(B, b2))
    await _write(service, b2, B2_BODY)

    result = await _search(service)

    linked = [m.uri for m in result.resources if m.related_from]
    assert len(linked) == 1 and linked[0] in {B, b2}


async def test_missing_repeated_self_and_cyclic_links(make_service):
    service, _ = await make_service(enabled=True)
    await _corpus(service, a_links=(B, B, A, f"{ROOT}/missing.md", "not-a-uri"))
    await _write(service, B, _doc(B_BODY, related=[A]))

    result = await _search(service)

    assert _uris(result) == [A, B]


@pytest.mark.parametrize("target_uri", [ROOT, [ROOT]])
async def test_links_never_widen_requested_scope(make_service, target_uri):
    service, reranker = await make_service(enabled=True)
    outside = "viking://resources/other/b.md"
    await _corpus(service, a_links=(outside,))
    await _write(service, outside, B_BODY)

    scoped = await _search(service, target_uri=target_uri)
    unscoped = await _search(service)

    assert _uris(scoped) == [A]
    assert outside in _uris(unscoped)


async def test_links_respect_caller_filter(make_service):
    from openviking.utils.tags import build_search_tags_filter

    service, _ = await make_service(enabled=True)
    await _write(service, A, _doc(A_BODY, related=[B]), tags=["team=kb"])
    await _write(service, B, B_BODY)

    result = await _search(service, filter=build_search_tags_filter(["team=kb"]))

    assert _uris(result) == [A]


async def test_links_to_another_users_private_space_are_invisible(make_service):
    service, _ = await make_service(enabled=True)
    other_ctx = RequestContext(user=UserIdentifier("default", "bob"), role=Role.USER)
    private = "viking://user/bob/resources/b.md"
    await _write(service, private, B_BODY, ctx=other_ctx)
    await _corpus(service, a_links=(private,))

    result = await _search(service)

    assert private not in _uris(result)
    assert private not in str(result.to_dict())
    # The owner still reaches it through ordinary retrieval.
    owner = await service.search.search("signature", ctx=other_ctx, level=[2], limit=5)
    assert private in _uris(owner)


async def test_links_travel_with_the_file_on_move_and_copy(make_service):
    service, _ = await make_service(enabled=True)
    await _corpus(service)
    moved, copied = f"{ROOT}/a-moved.md", f"{ROOT}/a-copied.md"

    await service.fs.mv(A, moved, ctx=CTX)
    result = await _search(service)
    assert _uris(result)[:1] == [moved]
    assert B in _uris(result) and result.resources[_uris(result).index(B)].related_from == moved

    await service.fs.cp(moved, copied, recursive=False, ctx=CTX)
    result = await _search(service, limit=5)
    assert {moved, copied, B} <= set(_uris(result))


async def test_deleted_or_moved_target_leaves_no_stale_hit(make_service):
    service, _ = await make_service(enabled=True)
    await _corpus(service)

    await service.fs.rm(B, ctx=CTX, wait=True)

    assert _uris(await _search(service)) == [A]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_doc("x", related=[B, "  viking://resources/kb/c.md/  "]), [B, C]),
        ("related: [viking://resources/kb/b.md]\n", []),  # not frontmatter
        ("---\nrelated: viking://resources/kb/b.md\n---\n", []),  # not a list
        ("---\nrelated: [1, https://x.y, viking://, '../b.md']\n---\n", []),
        ("---\nrelated: [unterminated\n---\n", []),
        ("---\nrelated:\n  - viking://resources/kb/b.md\n", []),  # truncated head
    ],
)
def test_parse_related_links(text, expected):
    from openviking.retrieve.relation_links import parse_related_links

    assert parse_related_links(text) == expected


async def test_links_survive_ovpack_export_and_import(make_service, tmp_path):
    service, _ = await make_service(enabled=True)
    await _corpus(service)
    pack = await service.pack.export_ovpack(ROOT, str(tmp_path / "kb.ovpack"), ctx=CTX)
    await service.fs.rm(A, ctx=CTX, wait=True)

    root = await service.pack.import_ovpack(pack, "viking://resources/restored", ctx=CTX)
    await service.resources.wait_processed()
    restored = f"{root.rstrip('/')}/a.md"

    result = await _search(service)
    assert _uris(result)[0] == restored
    assert B in _uris(result)
    assert result.resources[_uris(result).index(B)].related_from == restored
