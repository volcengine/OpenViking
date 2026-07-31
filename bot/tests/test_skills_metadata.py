from vikingbot.agent.skills import SkillsLoader


def test_skill_requirements_support_nested_yaml(tmp_path):
    skill_dir = tmp_path / "skills" / "lark-ov-compile-progress"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: lark-ov-compile-progress
description: Track OV Compile progress
metadata:
  requires:
    bins:
      - lark-cli
---

Use lark-cli.
""",
        encoding="utf-8",
    )

    assert SkillsLoader(tmp_path)._get_skill_meta("lark-ov-compile-progress") == {
        "requires": {"bins": ["lark-cli"]}
    }
