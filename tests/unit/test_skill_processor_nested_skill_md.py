# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import shutil
import zipfile

from openviking.utils.skill_processor import SkillProcessor

SKILL_MD = """---
name: demo
description: demo skill
---

# Demo
"""

NESTED_SKILL_MD = """---
name: demo-notes
description: demo notes sub-skill
---

# Notes
"""


def _build_nested_skill_dir(tmp_path):
    skill_dir = tmp_path / "demo"
    (skill_dir / "notes").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skill_dir / "notes" / "SKILL.md").write_text(NESTED_SKILL_MD, encoding="utf-8")
    (skill_dir / "notes" / "api.md").write_text("api reference", encoding="utf-8")
    (skill_dir / "scripts" / "helper.py").write_text("print('ok')\n", encoding="utf-8")
    return skill_dir


def test_parse_skill_directory_keeps_nested_skill_md(tmp_path):
    skill_dir = _build_nested_skill_dir(tmp_path)

    processor = SkillProcessor(vikingdb=None)
    skill_dict, auxiliary_files, base_path, cleanup_path = processor._parse_skill(
        skill_dir, allow_local_path_resolution=True
    )

    assert skill_dict["name"] == "demo"
    assert base_path == skill_dir
    relative = sorted(str(path.relative_to(skill_dir)) for path in auxiliary_files)
    # The top-level SKILL.md is the skill body and must stay excluded, while
    # nested SKILL.md files (sub-skills) must be collected as auxiliary files.
    assert relative == [
        "notes/SKILL.md",
        "notes/api.md",
        "scripts/helper.py",
    ]


def test_parse_skill_zip_keeps_nested_skill_md(tmp_path):
    skill_zip = tmp_path / "demo.zip"
    with zipfile.ZipFile(skill_zip, "w") as zf:
        zf.writestr("demo/SKILL.md", SKILL_MD)
        zf.writestr("demo/notes/SKILL.md", NESTED_SKILL_MD)
        zf.writestr("demo/notes/api.md", "api reference")

    processor = SkillProcessor(vikingdb=None)
    cleanup_path = None
    try:
        skill_dict, auxiliary_files, base_path, cleanup_path = processor._parse_skill(
            skill_zip, allow_local_path_resolution=True
        )

        assert skill_dict["name"] == "demo"
        relative = sorted(str(path.relative_to(base_path)) for path in auxiliary_files)
        assert relative == ["notes/SKILL.md", "notes/api.md"]
    finally:
        if cleanup_path:
            shutil.rmtree(cleanup_path, ignore_errors=True)
