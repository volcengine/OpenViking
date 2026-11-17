"""Opt-in DeepSeek worker smoke test using local synthetic files only.

Set OV_TEST_VLM_CONFIG to a private OpenAI-compatible VLM JSON configuration,
and optionally OV_COMPILE_SMOKE_OUTPUT to retain synthetic result receipts.
No server Compile is submitted. These five cases are not a recall benchmark.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.models import CompileLimits, utc_now
from vikingbot.compile.ops import finalize
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.results import Document
from vikingbot.compile.sources import split_source
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from bot.tests.test_compile_markdown import _human_conflict_page
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.storage.manual_conflicts import _CONFLICT_BLOCK, _parse_conflict_records
from openviking_cli.exceptions import NotFoundError

pytest_plugins = ["bot.tests.test_compile_markdown"]

BASE = Path(__file__).resolve().parents[1]
CASES = {
    "contradiction": [
        "Production v2: the export limit is 7 items.",
        "Production v2: the export limit is 9 items. No source establishes precedence.",
    ],
    "compatible": [
        "Production v2: the export limit is 7 items.",
        "Production v2: exports require authentication. This adds a condition and does not change the item limit.",
    ],
    "versions": [
        "Version v1: the export limit is 7 items.",
        "Version v2: the export limit is 9 items. The sources describe different versions.",
    ],
    "history": [
        "Production v2: the export limit is 9 items. No precedence over the existing Wiki is established."
    ],
    "human": [
        "Production v2: the export limit is 9 items, contradicting the manually approved limit of 7."
    ],
}


@pytest.mark.parametrize("case", list(CASES))
async def test_real_worker_comparison(pipeline, agent_loop, case):
    config_path = os.environ.get("OV_TEST_VLM_CONFIG")
    if not config_path:
        pytest.skip("Set OV_TEST_VLM_CONFIG to opt in to real DeepSeek calls")
    cfg = json.loads(Path(config_path).read_text())
    if "deepseek" not in cfg.get("model", "").lower():
        pytest.skip("This smoke test is scoped to a DeepSeek-compatible model")
    cfg.update(timeout=60, max_concurrent=1, max_tokens=6000, thinking=False)
    model = OpenAIVLM(cfg)
    provider = VLMProviderAdapter(
        model, cfg["model"], langfuse_client=SimpleNamespace(enabled=False)
    )
    pipeline.model.provider = provider
    pipeline.model.max_tokens = 6000
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    pipeline.timestamp = utc_now()
    pipeline.skill = (
        BASE / "openviking/system_assets/compile/example-skills/llm-wiki/SKILL.md"
    ).read_text()
    pipeline.request.instruction = "For this isolated subject assignment, write topic.md about export limits. Root navigation is handled separately. Cite the actual source URIs. No external facts."
    old = _human_conflict_page() if case == "human" else None
    if case == "history":
        old = "# Limits\n\nProduction v2: the export limit is 7 items.\n\n<!-- CONFLICTS_CONTENT\n-->\n"
    uri = pipeline.target + "/topic.md"

    async def stat(target):
        if old is not None and target == uri:
            return {"uri": target, "isDir": False}
        raise NotFoundError(target)

    async def download(target):
        if old is not None and target == uri:
            return old.encode()
        raise NotFoundError(target)

    pipeline.client.stat = AsyncMock(side_effect=stat)
    pipeline.client.download_bytes = AsyncMock(side_effect=download)
    loop, key, _ = agent_loop
    pipeline.model.agent_runner = agent_runner(
        loop, key, None, CompileLimits(subagent_iterations=16)
    )

    async def batches():
        for index, text in enumerate(CASES[case]):
            yield split_source(f"viking://resources/synthetic/{case}/{index}.md", text, 1000)

    sources = await pipeline.seed(batches())
    docs = []
    for index, source in enumerate(sources):
        path = f"synthetic-inputs/{index}.md"
        text = (
            "# Original source\n\n"
            + CASES[case][index]
            + f"\n\nOriginal: {source.metadata['uri']}\n"
        )
        await pipeline.files.sandbox.write_file(path, text)
        docs.append(Document(f"source-{index}.md", path, [source.source_id]))
    started = time.monotonic()
    output = await pipeline.generate(
        "reduce-live-" + case,
        "Compare every supplied source under matching subject, attribute and applicability. "
        "Keep differing versions separate. If no precedence resolves a genuine contradiction, "
        "record it as unfixed with both sources. Preserve human records and their visible anchor. "
        "Read topic.md with read_target_file before updating a supplied historical page. "
        "Write final topic.md through file tools with a valid trailing conflict ledger.",
        {
            "documents": [vars(doc) for doc in docs],
            "historical_files": ["topic.md"] if old else [],
            "inputs": [
                {"id": s.source_id, "payload": {**s.metadata, "source_ranges": [s.source_id]}}
                for s in sources
            ],
        },
        [s.source_id for s in sources],
    )
    bundle = await finalize.run(pipeline, output)
    contents = {op["uri"]: op["content"] for op in bundle.operations}
    records = [
        row
        for body in contents.values()
        for match in _CONFLICT_BLOCK.finditer(body)
        for row in _parse_conflict_records(match.group(1))
    ]
    E = Path(
        os.environ.get("OV_COMPILE_SMOKE_OUTPUT") or tempfile.mkdtemp(prefix="ov-conflict-smoke-")
    )
    E.mkdir(parents=True, exist_ok=True)
    (E / ("live-" + case + ".json")).write_text(
        json.dumps(
            {
                "case": case,
                "elapsed": time.monotonic() - started,
                "metrics": dict(pipeline.metrics),
                "usage": pipeline.model.usage,
                "contents": contents,
                "records": records,
                "local_only": True,
                "billing_completeness_claimed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if case in {"compatible", "versions"}:
        assert not records
    elif case in {"contradiction", "history"}:
        assert any(
            r.get("state") == "unfixed" and r.get("conflict_type") == "fact" for r in records
        )
        assert all(any(s.metadata["uri"] in body for body in contents.values()) for s in sources)
        if case == "history":
            assert uri in contents and "7 items" in contents[uri]
    else:
        assert any(
            r.get("state") == "unfixed" and r.get("conflict_type") == "human_conclusion"
            for r in records
        )
        if uri in contents:
            assert "The approved limit is 7." in contents[uri]
            assert any(
                r.get("fix_plan") == "用户手动解决内容冲突，修正后的内容如正文所示" for r in records
            )
    model.close()
