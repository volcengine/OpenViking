from pathlib import Path

from openviking.core.skill_loader import SkillLoader, validate_skill_format

SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "skills"
    / "memory_consolidation"
    / "SKILL.md"
)


def test_memory_consolidation_skill_has_valid_strict_format():
    content = SKILL_PATH.read_text(encoding="utf-8")

    validation = validate_skill_format(
        content,
        strict=True,
        skill_dir_name="memory_consolidation",
        source_path=str(SKILL_PATH),
    )
    parsed = SkillLoader.parse(content, source_path=str(SKILL_PATH))

    assert validation["valid"], validation["errors"]
    assert parsed["name"] == "memory_consolidation"


def test_memory_consolidation_skill_keeps_operator_scope_and_sources():
    body = " ".join(SkillLoader.load(str(SKILL_PATH))["content"].split())

    for contract in (
        "do not infer additional aliases or broaden either scope",
        "must belong to the same authenticated user and peer scope",
        "retain the alternatives with their source attribution",
        "Submit Wiki `pages` only",
        "include all relevant supplied `source_ids`",
        "use its exact catalog URI as `update_uri` and omit `path_hint`",
        "Do not delete, move, or rewrite source memories",
        "Do not submit writes outside the canonical target",
    ):
        assert contract in body
