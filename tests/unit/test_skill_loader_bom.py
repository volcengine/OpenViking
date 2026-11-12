# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for UTF-8 BOM handling in SKILL.md input."""

from openviking.core.skill_loader import SkillLoader, validate_skill_format

SKILL_MD = (
    "---\n"
    "name: bom-test\n"
    "description: Verify BOM handling\n"
    "allowed-tools: Read\n"
    "---\n\n"
    "# Instructions\n\nRead the evidence.\n"
)


def test_parse_accepts_utf8_bom_without_changing_skill():
    assert SkillLoader.parse("\ufeff" + SKILL_MD) == SkillLoader.parse(SKILL_MD)


def test_load_accepts_utf8_bom(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("\ufeff" + SKILL_MD, encoding="utf-8")

    parsed = SkillLoader.load(str(path))

    assert parsed["name"] == "bom-test"
    assert parsed["content"] == "# Instructions\n\nRead the evidence."


def test_validation_accepts_utf8_bom():
    result = validate_skill_format(
        "\ufeff" + SKILL_MD,
        strict=True,
        skill_dir_name="bom-test",
        source_path="SKILL.md",
    )

    assert result["valid"] is True
    assert result["errors"] == []


def test_only_leading_bom_is_removed():
    parsed = SkillLoader.parse(SKILL_MD.replace("Read the evidence.", "Read \ufeffthe evidence."))

    assert parsed["content"] == "# Instructions\n\nRead \ufeffthe evidence."
