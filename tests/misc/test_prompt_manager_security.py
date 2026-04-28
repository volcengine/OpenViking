from pathlib import Path

import pytest
from jinja2.exceptions import SecurityError

from openviking.prompts.manager import PromptManager


def _write_template(base: Path, body: str) -> None:
    target = base / "memory"
    target.mkdir(parents=True, exist_ok=True)
    indented_body = "\n".join(f"  {line}" for line in body.splitlines()) or "  "
    (target / "profile.yaml").write_text(
        """
metadata:
  id: memory.profile
  name: Profile
  description: Test template
  version: "1.0"
  language: en
  category: memory
variables:
  - name: user_name
    type: string
    description: user name
    required: false
template: |
""".lstrip()
        + indented_body
        + "\n",
        encoding="utf-8",
    )


def test_prompt_manager_renders_safe_template(tmp_path: Path) -> None:
    _write_template(tmp_path, "Hello {{ user_name }}")
    manager = PromptManager(templates_dir=tmp_path, enable_caching=False)

    rendered = manager.render("memory.profile", {"user_name": "alice"})

    assert rendered.strip() == "Hello alice"


def test_prompt_manager_blocks_unsafe_jinja_attribute_access(tmp_path: Path) -> None:
    _write_template(
        tmp_path,
        '{{ cycler.__init__.__globals__.os.system("echo should_not_run") }}',
    )
    manager = PromptManager(templates_dir=tmp_path, enable_caching=False)

    with pytest.raises(SecurityError):
        manager.render("memory.profile")
