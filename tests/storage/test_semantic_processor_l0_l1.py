# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.core.context import ContextLevel
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    AbstractOverviewMetadataError,
    parse_abstract_overview,
    prepare_abstract_overview_write,
    render_abstract_overview,
    write_abstract_overview,
)
from openviking.storage.queuefs.semantic_ops.directory_generation import (
    generate_directory_semantics,
)
from openviking_cli.utils.config.parser_config import SemanticConfig
from tests.storage.test_abstract_overview_freshness import _FakeFS


@pytest.mark.asyncio
async def test_directory_generation_uses_the_same_summary_for_l0_and_l1():
    full_abstract = "Documentation for the search service."
    complete = AsyncMock(return_value=full_abstract)
    result = await generate_directory_semantics(
        "viking://resources/product docs",
        {
            "guide #1.md": {"kind": "file", "summary": "Getting started."},
            "api": {"kind": "directory", "summary": "API references."},
        },
        previous=None,
        total_entries=513,
        config=SemanticConfig(abstract_max_chars=17),
        language="en",
        complete=complete,
    )
    assert result.abstract == "Documentation for"
    assert f"\n\n{full_abstract}\n\n" in result.overview
    assert "viking://resources/product%20docs/guide%20%231.md" in result.overview
    assert result.overview.index("[api]") < result.overview.index("[guide #1.md]")
    assert "Only some entries are listed" in result.overview
    assert "Access guide" in result.overview
    complete.assert_awaited_once()
    assert "Coverage: 2 of 513" in complete.await_args.args[0]
    assert "at most 17 characters" in complete.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["user_edit", "preset", "existing_preset"])
async def test_manual_l0_survives_generation_and_rejects_public_metadata_changes(origin):
    uri = "viking://resources/docs"
    if origin == "user_edit":
        raw = render_abstract_overview(ContextLevel.ABSTRACT, uri, "Generated description.")
        manual_raw = prepare_abstract_overview_write(
            f"{uri}/.abstract.md", raw, "Curated description."
        )
    else:
        metadata = {
            "generated_by": {"component": "VikingFS.write_context", "trigger": "context_write"}
        }
        if origin == "preset":
            metadata["maintenance"] = "manual"
        manual_raw = render_abstract_overview(
            ContextLevel.ABSTRACT, uri, "Curated description.", metadata
        )
    previous = parse_abstract_overview(manual_raw)
    assert previous.is_manual_abstract
    complete = AsyncMock(return_value="New automatic description.")
    entries = {"new.md": {"kind": "file", "summary": "New content."}}
    result = await generate_directory_semantics(
        uri,
        entries,
        previous=previous,
        total_entries=1,
        config=SemanticConfig(),
        language="en",
        complete=complete,
    )
    complete.assert_not_awaited()
    assert result.abstract == "Curated description."
    assert "new.md" in result.overview
    fs = _FakeFS({f"{uri}/.abstract.md": manual_raw})
    await write_abstract_overview(
        viking_fs=fs,
        dir_uri=uri,
        abstract="Must not overwrite manual text.",
        overview=result.overview,
        ctx=None,
    )
    written = parse_abstract_overview(fs.files[f"{uri}/.abstract.md"])
    assert written.body.strip() == result.abstract
    assert written.metadata["maintenance"] == "manual"
    assert "maintenance" not in parse_abstract_overview(fs.files[f"{uri}/.overview.md"]).metadata
    without_marker = render_abstract_overview(
        ContextLevel.ABSTRACT,
        uri,
        written.body,
        {key: value for key, value in written.metadata.items() if key != "maintenance"},
    )
    with pytest.raises(AbstractOverviewMetadataError):
        prepare_abstract_overview_write(
            f"{uri}/.abstract.md", fs.files[f"{uri}/.abstract.md"], without_marker
        )
    with pytest.raises(AbstractOverviewFormatError, match="maintenance must be manual"):
        prepare_abstract_overview_write(
            f"{uri}/.abstract.md",
            fs.files[f"{uri}/.abstract.md"],
            fs.files[f"{uri}/.abstract.md"].replace("maintenance: manual", "maintenance: auto"),
        )


@pytest.mark.asyncio
async def test_overview_truncates_an_overlong_summary_and_keeps_the_access_guide():
    abstract = "Short abstract."
    full_abstract = (
        f"{abstract} " + "This directory contains implementation notes for the service. " * 80
    ).strip()
    config = SemanticConfig(abstract_max_chars=len(abstract))
    result = await generate_directory_semantics(
        "viking://resources/docs",
        {
            "api.py": {"kind": "file", "summary": "x" * 12_000},
            "readme.md": {"kind": "file", "summary": "Getting started."},
        },
        previous=None,
        total_entries=3,
        config=config,
        language="en",
        complete=AsyncMock(return_value=full_abstract),
    )
    assert result.abstract == abstract
    assert len(result.overview) <= config.overview_max_chars
    summary = result.overview.split("\n\n", 2)[1]
    assert summary.startswith(abstract)
    assert full_abstract.startswith(summary)
    assert len(summary) < len(full_abstract)
    lines = [line for line in result.overview.splitlines() if line.startswith("- [")]
    assert lines == []
    assert "Only some entries are listed" in result.overview
    assert result.overview.endswith("support these operations.")


@pytest.mark.asyncio
async def test_empty_directory_skips_model_and_renders_navigation():
    complete = AsyncMock()
    result = await generate_directory_semantics(
        "viking://resources/empty",
        {},
        previous=None,
        total_entries=0,
        config=SemanticConfig(),
        language="zh-CN",
        complete=complete,
    )
    complete.assert_not_awaited()
    assert result.abstract == ""
    assert "目录导航" in result.overview
    assert "使用指引" in result.overview


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["", RuntimeError("model unavailable")])
async def test_generation_failure_is_not_published_as_placeholder(response):
    complete = (
        AsyncMock(side_effect=response)
        if isinstance(response, Exception)
        else AsyncMock(return_value=response)
    )
    with pytest.raises((ValueError, RuntimeError)):
        await generate_directory_semantics(
            "viking://resources/docs",
            {"a.md": {"kind": "file", "summary": "Summary."}},
            previous=None,
            total_entries=1,
            config=SemanticConfig(),
            language="en",
            complete=complete,
        )
