# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Output validation, cache revalidation and Wiki publication without model inference."""

from typing import Any

import pytest
from vikingbot.compile import file_ops
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.ops import finalize as finalize_op
from vikingbot.compile.results import FileDraft, FileResponse, Group
from vikingbot.providers.base import LLMResponse, ToolCallRequest

from bot.tests.test_compile_sources import runtime as runtime
from bot.tests.test_compile_sources import seed


def test_wiki_requires_body_but_metadata_files_remain_valid(runtime):
    pipeline, _ = runtime
    response = FileResponse(files=[FileDraft(path="index.md", content="---\ntype: index\n---\n")])
    group = Group("direct", [])
    pipeline.direct_output_format = "wiki"
    with pytest.raises(ValueError, match="requires a Markdown body"):
        file_ops.validate_files(pipeline, response, group, [], {})
    assert response.files[0].content is not None
    response.files[0].content += "# Knowledge index\n\n[Project 01](entity/project-01.md)\n"
    file_ops.validate_files(pipeline, response, group, [], {})
    response.files[0].content = "---\n---\n# Knowledge index\n"
    file_ops.validate_files(pipeline, response, group, [], {})
    pipeline.direct_output_format = "files"
    response.files[0].content = "---\nsetting: true\n---\n"
    file_ops.validate_files(pipeline, response, group, [], {})


@pytest.mark.parametrize(
    "link,valid",
    [
        ("[Project 01](entity/Project 01.md)", False),
        ("[Project 01](entity/Project%2001.md)", True),
        ("[Project 01](<entity/Project 01.md>)", True),
    ],
)
def test_compile_wiki_navigation_requires_valid_markdown_destinations(runtime, link, valid):
    pipeline, _ = runtime
    pipeline.direct_output_format = "wiki"
    response = FileResponse(
        files=[FileDraft(path="index.md", content=f"---\ntype: index\n---\n# Index\n\n{link}\n")]
    )
    if valid:
        file_ops.validate_files(pipeline, response, Group("direct", []), [], {})
    else:
        with pytest.raises(ValueError, match="unescaped whitespace.*|Encode spaces"):
            file_ops.validate_files(pipeline, response, Group("direct", []), [], {})
    # Ordinary file output can preserve literal Markdown source unchanged.
    pipeline.direct_output_format = "files"
    file_ops.validate_files(pipeline, response, Group("direct", []), [], {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("# Index\n\n[Project 01](entity/Project 01.md)\n", "unescaped whitespace"),
        ("---\ntitle: [\n---\n# Index\n", "invalid YAML frontmatter"),
        ("---\ntitle: [\n# Index\n", "unterminated YAML frontmatter"),
        ("---\ntype: index\n---\n", "requires a Markdown body"),
    ],
)
async def test_invalid_wiki_output_is_rejected_before_staging_and_publication(runtime, body, error):
    pipeline, _ = runtime
    pipeline.direct_output_format = "wiki"
    response = FileResponse(files=[FileDraft(path="index.md", content=body)])
    with pytest.raises(ValueError, match=error):
        file_ops.validate_files(pipeline, response, Group("direct", []), [], {})
    await pipeline.files.put(
        "accepted/index",
        {
            "path": "index.md",
            "content": body,
            "sha256": content_hash(body),
            "owner": "direct",
            "base_hash": None,
            "source_refs": [],
        },
    )
    with pytest.raises(ValueError, match=error):
        await finalize_op.run(pipeline, ["accepted/index"])
    assert "finalize" not in pipeline.files.values


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_kind", ["missing", "duplicate"])
async def test_compile_rejects_invalid_file_provenance(runtime, bad_kind):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    first_id = records[0].record_id
    inputs = [] if bad_kind == "missing" else [first_id, first_id]
    response = FileResponse(files=[FileDraft(path="page.md", content="# Page", inputs=inputs)])
    with pytest.raises(ValueError, match="inputs must"):
        file_ops.validate_files(pipeline, response, Group("files", records), records, {})


@pytest.mark.asyncio
async def test_compile_files_repair_invalid_provenance_and_revalidate_cache(runtime):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    ids = [record.record_id for record in records]
    group = Group("files", records)
    pipeline.direct_output_format = "wiki"
    valid: dict[str, Any] = {
        "path": "entity/cedar.md",
        "content": "# Cedar\n\nRetry limit: 7.\n",
        "inputs": ids,
    }
    invalid = {**valid, "inputs": ["foreign"]}

    def response(file):
        return LLMResponse(
            content="",
            tool_calls=[ToolCallRequest("submit", "emit", {"files": [file]}, tokens=0)],
        )

    pipeline.model.provider.chat.side_effect = [
        response(invalid),
        response(valid),
        response(valid),
    ]
    data = {"inputs": [{"id": key, "payload": {"fact": "Retry limit: 7"}} for key in ids]}

    def validate(result):
        file_ops.validate_files(pipeline, result, group, records, {})

    result = await pipeline.model.ask(
        "reduce", "Write supported Wiki pages", data, FileResponse, validate
    )
    assert result.files[0].inputs == ids
    assert pipeline.model.provider.chat.await_count == 2
    definition = pipeline.model.provider.chat.call_args.kwargs["tools"][0]["function"][
        "parameters"
    ]["$defs"]["FileDraft"]
    assert "inputs" in definition["required"]
    assert definition["properties"]["inputs"]["minItems"] == 1
    assert definition["properties"]["inputs"]["items"]["enum"] == ids
    cached = next(value for key, value in pipeline.files.values.items() if key.startswith("cache/"))
    cached["result"]["files"] = [invalid]
    result = await pipeline.model.ask(
        "reduce", "Write supported Wiki pages", data, FileResponse, validate
    )
    assert pipeline.model.provider.chat.await_count == 3
    refs = await file_ops.save_files(pipeline, result, group, records, {})
    assert set((await pipeline.files.get(refs[0]))["source_refs"]) == set(ids)


@pytest.mark.asyncio
async def test_empty_local_file_response_retains_unreferenced_accounting(runtime):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    group = Group("no-local-contribution", records)
    response = FileResponse(files=[])
    file_ops.validate_files(pipeline, response, group, records, {})
    assert await file_ops.save_files(pipeline, response, group, records, {}) == []
    assert all(pipeline.status[record.record_id] == "unreferenced" for record in records)
    assert not pipeline.artifacts
