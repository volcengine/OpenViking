#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
STAGE="${1:?usage: stage-memory-plugin-marketplace.sh <stage-dir>}"

rm -rf "${STAGE}"
mkdir -p "${STAGE}"
cp -R \
  "${ROOT}/examples/.claude-plugin" \
  "${ROOT}/examples/.agents" \
  "${ROOT}/examples/claude-code-memory-plugin" \
  "${ROOT}/examples/codex-memory-plugin" \
  "${ROOT}/examples/cursor-memory-plugin" \
  "${ROOT}/examples/trae-memory-hooks" \
  "${ROOT}/examples/zcode-memory-plugin" \
  "${ROOT}/examples/opencode-plugin" \
  "${ROOT}/examples/pi-coding-agent-extension" \
  "${ROOT}/examples/memory-plugin-shared" \
  "${STAGE}/"

for required in \
  claude-code-memory-plugin/skills/ov-experience-memory/SKILL.md \
  codex-memory-plugin/skills/ov-experience-memory/SKILL.md \
  cursor-memory-plugin/.cursor-plugin/plugin.json \
  cursor-memory-plugin/hooks/hooks.json \
  cursor-memory-plugin/.mcp.json \
  cursor-memory-plugin/openviking.integration.json \
  cursor-memory-plugin/scripts/cursor-hook.mjs \
  cursor-memory-plugin/scripts/session-start.mjs \
  cursor-memory-plugin/scripts/auto-recall.mjs \
  cursor-memory-plugin/scripts/auto-capture.mjs \
  cursor-memory-plugin/scripts/pre-compact.mjs \
  cursor-memory-plugin/scripts/session-end.mjs \
  cursor-memory-plugin/scripts/cursor-transcript.mjs \
  cursor-memory-plugin/scripts/uri-guard.mjs \
  cursor-memory-plugin/servers/mcp-proxy.mjs \
  cursor-memory-plugin/rules/openviking-memory.mdc \
  cursor-memory-plugin/skills/openviking-memory/SKILL.md \
  trae-memory-hooks/hooks/hooks.json \
  trae-memory-hooks/.mcp.json \
  trae-memory-hooks/openviking.integration.json \
  trae-memory-hooks/scripts/trae-hook.mjs \
  trae-memory-hooks/scripts/session-start.mjs \
  trae-memory-hooks/scripts/auto-recall.mjs \
  trae-memory-hooks/scripts/auto-capture.mjs \
  trae-memory-hooks/scripts/trae-turns.mjs \
  trae-memory-hooks/scripts/uri-guard.mjs \
  trae-memory-hooks/servers/mcp-proxy.mjs \
  zcode-memory-plugin/.zcode-plugin/plugin.json \
  zcode-memory-plugin/hooks/hooks.json \
  zcode-memory-plugin/.mcp.json \
  zcode-memory-plugin/openviking.integration.json \
  zcode-memory-plugin/scripts/zcode-hook.mjs \
  zcode-memory-plugin/scripts/zcode-capture.mjs \
  zcode-memory-plugin/scripts/zcode-turns.mjs \
  zcode-memory-plugin/scripts/shared/async-writer.mjs \
  zcode-memory-plugin/scripts/shared/agent-hook-runtime.mjs \
  zcode-memory-plugin/scripts/shared/batch-send.mjs \
  zcode-memory-plugin/scripts/shared/retryable.mjs \
  zcode-memory-plugin/servers/mcp-proxy.mjs \
  memory-plugin-shared/lib/agent-hook-runtime.mjs \
  memory-plugin-shared/lib/agent-uri-guard.mjs \
  memory-plugin-shared/lib/async-writer.mjs \
  memory-plugin-shared/lib/retryable.mjs \
  memory-plugin-shared/lib/uri-guard.mjs \
  memory-plugin-shared/lib/mcp-proxy-core.mjs; do
  test -f "${STAGE}/${required}" || {
    echo "Marketplace archive is missing ${required}" >&2
    exit 1
  }
done
