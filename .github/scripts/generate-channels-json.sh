#!/usr/bin/env bash
# Write channels.json: the version and immutable artifacts each harness
# installs from under `install.sh --dist tos`. Codex clones the TOS git
# marketplace; every other harness downloads the marketplace zip and falls back
# to the source zip.

set -euo pipefail

TAG="${1:?usage: generate-channels-json.sh <tag> <tos-base> <marketplace-zip> <source-zip> <out>}"
TOS_BASE="${2%/}"
MARKETPLACE_ZIP="$3"
SOURCE_ZIP="$4"
OUT="$5"

sha256() { sha256sum "$1" | cut -d' ' -f1; }

jq -n \
  --arg version "${TAG#v}" \
  --arg git_url "${TOS_BASE}/plugins/memory-plugins.git" \
  --arg bundle_url "${TOS_BASE}/releases/${TAG}/memory-plugin-marketplace.zip" \
  --arg sha256 "$(sha256 "${MARKETPLACE_ZIP}")" \
  --arg source_url "${TOS_BASE}/releases/${TAG}/openviking-${TAG}-source.zip" \
  --arg source_sha256 "$(sha256 "${SOURCE_ZIP}")" \
  '{version: $version, bundle_url: $bundle_url, sha256: $sha256,
    source_url: $source_url, source_sha256: $source_sha256} as $archive
  | {schema: 1, harnesses: (
      {codex: {version: $version, git_url: $git_url}}
      + (["claude", "cursor", "trae", "trae-cn", "trae-cli", "zcode", "kimicode",
          "opencode", "pi", "dsh"] | map({key: ., value: $archive}) | from_entries)
    )}' > "${OUT}"
