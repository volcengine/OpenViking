import re
from pathlib import Path

WORKSPACE_SKILLS = Path(__file__).resolve().parents[1] / "workspace" / "skills"


def test_opencode_skill_only_advertises_packaged_helpers():
    skill_dir = WORKSPACE_SKILLS / "opencode"
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    advertised_helpers = re.findall(r"^### `([^`]+\.py)`", skill_text, flags=re.MULTILINE)

    assert advertised_helpers
    assert [name for name in advertised_helpers if not (skill_dir / name).is_file()] == []
