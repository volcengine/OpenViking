#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
STAGE="${1:?usage: stage-memory-plugin-marketplace.sh <stage-dir>}"

# The plugins published as packages build their shared copies at pack time and
# keep none in git, so the archive is only complete once the generator has run.
node "${ROOT}/examples/memory-plugin-shared/sync.mjs" >/dev/null

rm -rf "${STAGE}"
mkdir -p "${STAGE}"

# The archive ships these directories whole. What each of them has to contain is
# derived below, not listed here.
DIRS=(
  .claude-plugin
  .agents
  claude-code-memory-plugin
  codex-memory-plugin
  agent-hook-plugin
  opencode-plugin
  pi-coding-agent-extension
  memory-plugin-shared
)

# Tar drops the plugins' node_modules on the way through, over a hundred
# megabytes the archive has no use for. Copying only tracked files would drop
# the generated shared copies the archive does need.
tar -cf - \
  --exclude=node_modules \
  --exclude=.git \
  -C "${ROOT}/examples" \
  "${DIRS[@]}" \
  | tar -xf - -C "${STAGE}"

for dir in "${DIRS[@]}"; do
  test -d "${STAGE}/${dir}" || {
    echo "Marketplace archive is missing ${dir}/" >&2
    exit 1
  }
done

node "${ROOT}/.github/scripts/check-marketplace-archive.mjs" "${STAGE}" "${DIRS[@]}"
