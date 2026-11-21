# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

import pytest

from openviking.service.skill_sources import resolve_skill_source
from openviking_cli.exceptions import InvalidArgumentError


def _write_skill(directory: Path, *, name: str) -> None:
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"""---
name: {name}
description: Duplicate-name test fixture
---

Test instructions.
""",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_resolve_skill_source_rejects_duplicate_declared_names(tmp_path: Path) -> None:
    _write_skill(tmp_path / "configured-copy", name="duplicate-name")
    _write_skill(tmp_path / "project-copy", name="duplicate-name")

    with pytest.raises(InvalidArgumentError, match="duplicate-name") as exc_info:
        async with resolve_skill_source(tmp_path, allow_local_path_resolution=True):
            pytest.fail("duplicate declared Skill names must fail before installation")

    assert exc_info.value.details == {
        "field": "name",
        "name": "duplicate-name",
        "source_directories": ["configured-copy", "project-copy"],
    }


@pytest.mark.asyncio
async def test_resolve_skill_source_allows_one_selected_duplicate(tmp_path: Path) -> None:
    _write_skill(tmp_path / "configured-copy", name="duplicate-name")
    _write_skill(tmp_path / "project-copy", name="duplicate-name")

    async with resolve_skill_source(
        tmp_path,
        names=["project-copy"],
        allow_local_path_resolution=True,
    ) as targets:
        assert [path.name for path, _metadata in targets] == ["project-copy"]


@pytest.mark.asyncio
async def test_resolve_skill_source_allows_distinct_declared_names(tmp_path: Path) -> None:
    _write_skill(tmp_path / "first-directory", name="first-skill")
    _write_skill(tmp_path / "second-directory", name="second-skill")

    async with resolve_skill_source(tmp_path, allow_local_path_resolution=True) as targets:
        assert [path.name for path, _metadata in targets] == [
            "first-directory",
            "second-directory",
        ]
