# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import shutil
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.utils.skill_processor import SkillProcessor


def test_resolve_skill_zip_normalizes_windows_separators(tmp_path):
    skill_zip = tmp_path / "pdf.zip"
    with zipfile.ZipFile(skill_zip, "w") as zf:
        zf.writestr(
            "SKILL.md",
            """---
name: pdf
description: PDF helper
---

# PDF
""",
        )
        zf.writestr("scripts\\check_bounding_boxes.py", "print('ok')\n")

    resolved_path, cleanup_path = SkillProcessor._resolve_skill_path(skill_zip)
    try:
        assert (resolved_path / "SKILL.md").exists()
        assert (resolved_path / "scripts" / "check_bounding_boxes.py").read_text() == (
            "print('ok')\n"
        )
    finally:
        if cleanup_path:
            shutil.rmtree(cleanup_path, ignore_errors=True)


def test_resolve_skill_zip_preserves_posix_separators(tmp_path):
    skill_zip = tmp_path / "pdf.zip"
    with zipfile.ZipFile(skill_zip, "w") as zf:
        zf.writestr(
            "SKILL.md",
            """---
name: pdf
description: PDF helper
---

# PDF
""",
        )
        zf.writestr("scripts/check_bounding_boxes.py", "print('ok')\n")

    resolved_path, cleanup_path = SkillProcessor._resolve_skill_path(skill_zip)
    try:
        assert (resolved_path / "SKILL.md").exists()
        assert (resolved_path / "scripts" / "check_bounding_boxes.py").read_text() == (
            "print('ok')\n"
        )
    finally:
        if cleanup_path:
            shutil.rmtree(cleanup_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_write_auxiliary_files_normalizes_windows_separators(tmp_path):
    base_path = tmp_path / "pdf"
    base_path.mkdir()
    aux_file = base_path / "scripts\\check_bounding_boxes.py"
    aux_file.write_text("print('ok')", encoding="utf-8")

    viking_fs = SimpleNamespace(
        write_file=AsyncMock(),
        write_file_bytes=AsyncMock(),
    )
    processor = SkillProcessor(vikingdb=None)

    await processor._write_auxiliary_files(
        viking_fs,
        [aux_file],
        base_path,
        "viking://user/default/skills/pdf",
        ctx=None,
    )

    viking_fs.write_file.assert_awaited_once_with(
        "viking://user/default/skills/pdf/scripts/check_bounding_boxes.py",
        "print('ok')",
        ctx=None,
    )
    viking_fs.write_file_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_auxiliary_files_preserves_posix_separators(tmp_path):
    base_path = tmp_path / "pdf"
    scripts_dir = base_path / "scripts"
    scripts_dir.mkdir(parents=True)
    aux_file = scripts_dir / "check_bounding_boxes.py"
    aux_file.write_text("print('ok')", encoding="utf-8")

    viking_fs = SimpleNamespace(
        write_file=AsyncMock(),
        write_file_bytes=AsyncMock(),
    )
    processor = SkillProcessor(vikingdb=None)

    await processor._write_auxiliary_files(
        viking_fs,
        [aux_file],
        base_path,
        "viking://user/default/skills/pdf",
        ctx=None,
    )

    viking_fs.write_file.assert_awaited_once_with(
        "viking://user/default/skills/pdf/scripts/check_bounding_boxes.py",
        "print('ok')",
        ctx=None,
    )
    viking_fs.write_file_bytes.assert_not_awaited()
