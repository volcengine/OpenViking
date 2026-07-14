# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.workspace_kind import load_workspace_kind
from openviking_cli.utils.config.memory_config import MemoryConfig


def test_builtin_workspace_kinds_are_available():
    for kind in ("personal", "team", "project", "organization"):
        assert load_workspace_kind(kind).kind == kind

    personal = load_workspace_kind("personal")
    team = load_workspace_kind("team")

    assert personal.shared_scope_label == "personal workspace"
    assert team.shared_scope_label == "shared team workspace"
    assert "team facts" in team.shared_scope_instruction


def test_custom_workspace_kind_definition_overrides_builtin(tmp_path):
    (tmp_path / "team.yaml").write_text(
        """kind: team
display_name: Custom team workspace
shared_scope_label: custom shared team workspace
shared_scope_instruction: Store custom shared team knowledge without peer_id.
private_scope_instruction: Store custom private actor knowledge with peer_id.
resource_scope_instruction: Keep custom canonical documents in resources.
""",
        encoding="utf-8",
    )

    definition = load_workspace_kind("team", str(tmp_path))

    assert definition.display_name == "Custom team workspace"
    assert definition.shared_scope_label == "custom shared team workspace"


def test_memory_config_defaults_to_personal_workspace():
    config = MemoryConfig()

    assert config.workspace_kind == "personal"
    assert config.workspace_kinds_dir == ""


def test_memory_config_normalizes_workspace_kind():
    assert MemoryConfig(workspace_kind=" Team ").workspace_kind == "team"


def test_unknown_workspace_kind_has_actionable_error():
    with pytest.raises(ValueError, match="Unknown memory.workspace_kind"):
        load_workspace_kind("unknown")


def test_team_workspace_semantics_are_added_to_extraction_instruction(monkeypatch):
    memory = SimpleNamespace(
        eager_prefetch=False,
        prefetch_search_topn=5,
        link_enabled=False,
        workspace_kind="team",
        workspace_kinds_dir="",
    )
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: SimpleNamespace(memory=memory),
    )

    instruction = SessionExtractContextProvider(messages=[]).instruction()

    assert "Workspace kind: Team workspace" in instruction
    assert "shared team workspace" in instruction
    assert "Treat the configured user identifier as the team identity" in instruction
    assert "When a memory belongs to the shared team workspace, omit peer_id." in instruction
    assert "When a memory is private to one actor, set peer_id" in instruction
