# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Plugin hook commands must quote ${PLUGIN_ROOT} (#4623).

An unquoted interpolation word-splits under plugin directories containing
spaces (macOS Orca installs under ~/Library/Application Support/...), so
node loads /Users/<user>/Library/Application and every hook exits 1.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PLUGIN_HOOK_FILES = [
    "examples/codex-memory-plugin/hooks/hooks.json",
    "examples/zcode-memory-plugin/hooks/hooks.json",
    "examples/claude-code-memory-plugin/hooks/hooks.json",
    "examples/cursor-memory-plugin/hooks/hooks.json",
]

# The whole command must be `node "${VAR}/scripts/<name>.mjs"` — anchored so
# unquoted interpolations, stray arguments, or trailing shell cannot pass.
_COMMAND_SHAPE = re.compile(r'^node "\$\{[A-Z_]+\}/scripts/[^"\n]+\.mjs"$')


def _hook_commands(rel_path: str):
    path = REPO_ROOT / rel_path
    data = json.loads(path.read_text(encoding="utf-8"))
    for event_entries in data.get("hooks", {}).values():
        for entry in event_entries:
            # Claude/Codex/ZCode schema: entry -> hooks[] -> {type: command}.
            for hook in entry.get("hooks", []):
                if hook.get("type") == "command":
                    yield hook["command"]
            # Cursor schema: the entry itself carries the command.
            if "command" in entry:
                yield entry["command"]


def test_extractor_finds_commands_in_every_plugin():
    # cursor's hooks.json uses a flat event[].command schema; without this
    # guard a schema drift would silently extract zero commands.
    for rel_path in _PLUGIN_HOOK_FILES:
        assert sum(1 for _ in _hook_commands(rel_path)) >= 1, (
            f"{rel_path}: no hook commands extracted"
        )


def test_every_hook_command_is_a_quoted_node_invocation():
    for rel_path in _PLUGIN_HOOK_FILES:
        for command in _hook_commands(rel_path):
            assert _COMMAND_SHAPE.match(command), (
                f'{rel_path}: command must match node "${{VAR}}/scripts/<name>.mjs": {command}'
            )


def test_codex_plugin_covers_all_five_lifecycle_hooks():
    data = json.loads(
        (REPO_ROOT / "examples/codex-memory-plugin/hooks/hooks.json").read_text(encoding="utf-8")
    )
    events = set(data.get("hooks", {}))
    assert {
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
        "SessionEnd",
        "PreCompact",
    } <= events


def test_codex_hook_commands_execute_as_single_shell_word_with_spaces():
    # Simulate what the shell receives: substituting a spaced PLUGIN_ROOT into
    # the quoted command keeps the script path a single word.
    for command in _hook_commands(_PLUGIN_HOOK_FILES[0]):
        rendered = command.replace("${PLUGIN_ROOT}", "/Users/x/Library/Application Support/orca/p")
        # The script path sits inside double quotes: exactly one token after `node `.
        body = rendered.split("node ", 1)[1]
        assert body.startswith('"') and body.endswith('"'), rendered
        assert " " in body[1:-1]  # the spaced path is inside the quotes
