"""Hermes plugin manifest contract tests."""

from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_manifest_does_not_use_legacy_hooks_key():
    text = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    top_level_keys = {
        line.partition(":")[0]
        for line in text.splitlines()
        if line and not line[0].isspace() and ":" in line
    }

    assert "hooks" not in top_level_keys
