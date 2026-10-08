#!/usr/bin/env bash
# Build the downloads the docs site serves under /dl: the installer, the plugin
# bundle, the Claude Code URL marketplace, the Codex git marketplace and the
# channel data of the version check, laid out like the TOS release bucket. Each docs host publishes the tree built from
# the commit it deploys, with its own address in the marketplace manifest.

set -euo pipefail

USAGE="usage: build-plugin-downloads.sh <out-dir> <base-url>"
OUT="${1:?${USAGE}}"
BASE="${2:?${USAGE}}"
BASE="${BASE%/}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SCRIPTS="${ROOT}/.github/scripts"
SHARED="${ROOT}/examples/memory-plugin-shared"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ov-downloads.XXXXXX")"
trap 'rm -rf "${WORK}"' EXIT

# The commit the tree was built from, so a published installer can be compared
# with the repository copy at that commit.
VERSION="$(TZ=UTC git -C "${ROOT}" log -1 --abbrev=10 --format=%cd-%h --date=format-local:%Y%m%d)"
# Docs deploy on every change, so a zip must only change when its content does:
# the Claude Code zip keeps its name for as long as the plugin version does.
export ZIP_STAMP=200001010000.00

STAGE="${WORK}/memory-plugin-marketplace"
bash "${SCRIPTS}/stage-memory-plugin-marketplace.sh" "${STAGE}"
PLUGIN_VERSION="$(jq -r .version "${STAGE}/claude-code-memory-plugin/.claude-plugin/plugin.json")"
CLAUDE_ZIP="plugins/claude/openviking-memory-${PLUGIN_VERSION}.zip"

rm -rf "${OUT}"
mkdir -p "${OUT}/memory-plugin-shared" "${OUT}/releases/latest" "${OUT}/plugins/claude"
case "${OUT}" in /*) ;; *) OUT="${PWD}/${OUT}" ;; esac

bash "${SCRIPTS}/stamp-installer-version.sh" "${SHARED}/install.sh" "${VERSION}" "${OUT}/memory-plugin-shared/install.sh"
cp "${SHARED}/bootstrap.sh" "${OUT}/memory-plugin-shared/bootstrap.sh"

# The git marketplace is committed before the zips are built: zipping resets
# the staged files' times. Like the zips, the repository keeps its bytes until
# the plugins change: a new commit and pack name on every deploy leaves the CDN
# serving refs and packs of different deploys for a while, failing git clients.
SRC="${WORK}/memory-plugins-src"
cp -R "${STAGE}" "${SRC}"
git -C "${SRC}" init -q -b main
git -C "${SRC}" add -A
# Background auto-maintenance would repack while the clone below reads.
GIT_AUTHOR_DATE="2000-01-01T00:00:00Z" GIT_COMMITTER_DATE="2000-01-01T00:00:00Z" \
  git -C "${SRC}" -c maintenance.auto=false -c user.email=release@openviking.org -c user.name="OpenViking Release" \
  commit -qm "OpenViking memory plugins"
git clone -q --bare "${SRC}" "${OUT}/plugins/memory-plugins.git"
git -C "${OUT}/plugins/memory-plugins.git" -c pack.threads=1 repack -adq
git -C "${OUT}/plugins/memory-plugins.git" update-server-info
# Static hosts serve files only; the sample hooks are of no use to a client.
rm -rf "${OUT}/plugins/memory-plugins.git/hooks"

bash "${SCRIPTS}/reproducible-zip.sh" "${OUT}/releases/latest/memory-plugin-marketplace.zip" "${WORK}" memory-plugin-marketplace
bash "${SCRIPTS}/reproducible-zip.sh" "${OUT}/${CLAUDE_ZIP}" "${STAGE}" claude-code-memory-plugin
bash "${SCRIPTS}/generate-claude-marketplace-json.sh" "${BASE}/${CLAUDE_ZIP}" \
  "${OUT}/${CLAUDE_ZIP}" "${STAGE}" "${OUT}/plugins/claude/marketplace.json"

# What the install site's version check answers with. No checksum: the two
# hosts deploy one after the other and keep no earlier build, so a pinned
# bundle would fail to verify while they differ.
jq -n \
  --arg version "${VERSION}" \
  --arg git_url "${BASE}/plugins/memory-plugins.git" \
  --arg bundle_url "${BASE}/releases/latest/memory-plugin-marketplace.zip" \
  '{schema: 1, harnesses: (
      {codex: {version: $version, git_url: $git_url}}
      + (["claude", "cursor", "trae", "trae-cli", "trae-cn", "zcode", "kimicode",
          "opencode", "pi", "dsh"] | map({key: ., value: {version: $version, bundle_url: $bundle_url}}) | from_entries)
    )}' > "${OUT}/releases/latest/channels.json"

echo "Built plugin downloads ${VERSION} for ${BASE}"
