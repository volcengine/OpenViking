#!/usr/bin/env bash
#
# OpenViking Memory Plugin for Claude Code — TOS bootstrap (China-friendly).
#
# For users who can't reach github.com / raw.githubusercontent.com. Pulls both
# the installer and the source archive from Volcengine TOS instead of GitHub,
# then hands off to the shared memory plugin installer with Claude selected.
#
# One-liner:
#   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/claude-code-memory-plugin/tos-install.sh)
#
# Env overrides:
#   OPENVIKING_TOS_BASE                 default: https://ovrelease.tos-cn-beijing.volces.com
#   OPENVIKING_MARKETPLACE_ARCHIVE_URL  default: $OPENVIKING_TOS_BASE/releases/latest/memory-plugin-marketplace.zip
#   OPENVIKING_REPO_ARCHIVE_URL         default: $OPENVIKING_TOS_BASE/releases/latest/openviking-source.zip
#   (every install.sh env override applies too.)

set -euo pipefail

TOS_BASE="${OPENVIKING_TOS_BASE:-https://ovrelease.tos-cn-beijing.volces.com}"
TOS_BASE="${TOS_BASE%/}"
# Prefer the slim marketplace archive; the full source zip stays as a fallback
# for TOS buckets that predate it.
export OPENVIKING_MARKETPLACE_ARCHIVE_URL="${OPENVIKING_MARKETPLACE_ARCHIVE_URL:-$TOS_BASE/releases/latest/memory-plugin-marketplace.zip}"
export OPENVIKING_REPO_ARCHIVE_URL="${OPENVIKING_REPO_ARCHIVE_URL:-$TOS_BASE/releases/latest/openviking-source.zip}"
export OPENVIKING_SHARED_INSTALL_URL="${OPENVIKING_SHARED_INSTALL_URL:-$TOS_BASE/memory-plugin-shared/install.sh}"

# Fetch the real installer to a file (not a pipe) so it keeps the terminal on
# stdin for its interactive prompts. It then sources the plugins from the
# archive URLs above instead of GitHub.
installer=$(mktemp "${TMPDIR:-/tmp}/ov-cc-install.XXXXXX") || { echo "mktemp failed" >&2; exit 1; }
trap 'rm -f "$installer"' EXIT
curl -fsSL -o "$installer" "$OPENVIKING_SHARED_INSTALL_URL"
bash "$installer" --harness claude --source archive
