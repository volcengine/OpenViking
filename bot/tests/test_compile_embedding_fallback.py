import pytest
from vikingbot.compile.hashing import digest
from vikingbot.compile.ops.shuffle import Shuffle
from vikingbot.compile.plan import parse_plan
from vikingbot.compile.results import Document, RouteResponse

from bot.tests.test_compile_markdown import MERGE, contract
from bot.tests.test_compile_markdown import pipeline as pipeline
from openviking_cli.exceptions import OpenVikingError


@pytest.mark.parametrize("status", [None, 451, 429, 503])
async def test_shuffle_keeps_ranking_after_provider_chain_failure(pipeline, status):
    pipeline.contract = contract(True)
    texts = ["orchard seeds", "river depth", "orchard seeds warning"] + [
        f"polar depth {i}" for i in range(32)
    ]
    docs = [Document(f"{i}.md", f"drafts/{i}.md") for i in range(len(texts))]
    for doc, text in zip(docs, texts, strict=True):
        await pipeline.files.sandbox.write_file(doc.workspace_path, text)
    for text in texts[:2]:
        await pipeline.files.put(f"embeddings/{digest(['m', text])}", [1.0, 0.0])
    unavailable = OpenVikingError(
        "Synthetic upstream failure",
        code={451: "INVALID_ARGUMENT", 429: "RESOURCE_EXHAUSTED"}.get(status, "UNAVAILABLE"),
        details=(
            {"upstream_status_code": status, "upstream_error_type": "ModelError"}
            if status is not None
            else {"upstream_error_type": "AllCredentialsFailedError"}
        ),
    )
    batches = []

    async def embeddings(batch, *, target_uri, expected_model):
        if batch:
            batches.append(batch)
        if batch == texts[2:34]:
            raise unavailable
        return {"model": "m", "vectors": [[0.0, 1.0] for _ in batch]}

    candidates = {}

    async def ask(stage, system, data, schema, validate, **kwargs):
        for row in data["assignments"]:
            candidates[row["document"]] = row["candidates"]
        response = RouteResponse(
            decisions=[{"document": row["document"]} for row in data["assignments"]]
        )
        validate(response)
        return response

    pipeline.client.compile_embeddings = embeddings
    pipeline.model.ask = ask
    node = parse_plan(MERGE, pipeline.contract)[1]
    shuffle = Shuffle(pipeline)
    if status is not None:
        with pytest.raises(OpenVikingError) as caught:
            await shuffle.run(node, docs)
        assert caught.value is unavailable
        assert not candidates
    else:
        assert len(await shuffle.run(node, docs)) == len(docs)
        assert candidates["drafts/0.md"] == ["drafts/1.md", "drafts/34.md"]
        assert "drafts/0.md" in candidates["drafts/2.md"]
        assert [len(batch) for batch in batches] == [32, 1]
        assert pipeline.warnings
