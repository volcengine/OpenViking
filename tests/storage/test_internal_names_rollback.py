"""Unit tests for rollback-safe internal entry helpers."""

from openviking.storage.internal_names import (
    is_rollback_content_gated_entry_name,
    is_rollback_safe_entry_name,
    is_rollback_safe_sidecar_content,
)


def test_name_only_markers_remain_safe_without_content_check():
    assert is_rollback_safe_entry_name(".path.ovlock")
    assert is_rollback_safe_entry_name(".exact.ovlock.abc")
    assert is_rollback_safe_entry_name("_system")
    assert is_rollback_safe_entry_name("tasks")
    assert is_rollback_safe_entry_name(".redirect.json")


def test_sidecars_are_content_gated_not_name_safe():
    for name in (".abstract.md", ".overview.md", ".relations.json"):
        assert not is_rollback_safe_entry_name(name)
        assert is_rollback_content_gated_entry_name(name)


def test_sidecar_empty_and_not_ready_are_safe():
    assert is_rollback_safe_sidecar_content(".abstract.md", "")
    assert is_rollback_safe_sidecar_content(".abstract.md", "   \n")
    assert is_rollback_safe_sidecar_content(".abstract.md", "[.abstract.md is not ready]")
    assert is_rollback_safe_sidecar_content(
        ".abstract.md",
        "# viking://resources/x [Directory abstract is not ready]",
    )
    assert is_rollback_safe_sidecar_content(
        ".overview.md",
        "# x\n\n[Directory overview is not ready]",
    )
    assert is_rollback_safe_sidecar_content(".relations.json", "")
    assert is_rollback_safe_sidecar_content(".relations.json", "{}")
    assert is_rollback_safe_sidecar_content(".relations.json", "[]")
    assert is_rollback_safe_sidecar_content(".relations.json", "  {\n  }\n")


def test_sidecar_filled_content_is_not_safe():
    assert not is_rollback_safe_sidecar_content(
        ".abstract.md",
        "# Summary\n\nActual abstract text.",
    )
    assert not is_rollback_safe_sidecar_content(
        ".overview.md",
        "# Overview\n\nActual overview text.",
    )
    # Mentions the marker phrase but also has real body → not a stub.
    assert not is_rollback_safe_sidecar_content(
        ".abstract.md",
        "# Summary\n\nMentions [Directory abstract is not ready] in body.",
    )
    assert not is_rollback_safe_sidecar_content(
        ".relations.json",
        '{"links":[{"uri":"viking://resources/other"}]}',
    )
    assert not is_rollback_safe_sidecar_content(".relations.json", "not-json")
