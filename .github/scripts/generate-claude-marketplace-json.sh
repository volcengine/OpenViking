#!/usr/bin/env bash
# Write the Claude Code URL marketplace the installer registers: the staged
# directory marketplace's entry, re-pointed at the release's immutable plugin
# zip. Claude Code fetches both over HTTPS without git, and updates an
# installed plugin when the entry's version changes, so the version is the
# plugin manifest's own.

set -euo pipefail

TAG="${1:?usage: generate-claude-marketplace-json.sh <tag> <tos-base> <plugin-zip> <stage-dir> <out>}"
TOS_BASE="${2%/}"
PLUGIN_ZIP="$3"
STAGE="$4"
OUT="$5"

sha256() { sha256sum "$1" | cut -d' ' -f1; }

jq \
  --arg url "${TOS_BASE}/releases/${TAG}/openviking-memory-claude.zip" \
  --arg sha256 "$(sha256 "${PLUGIN_ZIP}")" \
  --arg version "$(jq -r .version "${STAGE}/claude-code-memory-plugin/.claude-plugin/plugin.json")" \
  '.description = "OpenViking plugins for Claude Code (TOS mirror)."
  | .plugins |= map(select(.name == "openviking-memory")
      | .version = $version
      | .source = {source: "archive", url: $url, sha256: $sha256})
  | if (.plugins | length) == 1 then . else error("openviking-memory entry not found") end' \
  "${STAGE}/.claude-plugin/marketplace.json" > "${OUT}"
