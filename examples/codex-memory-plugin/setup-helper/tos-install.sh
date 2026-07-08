#!/usr/bin/env bash
#
# OpenViking Memory Plugin for Codex — TOS bootstrap (China-friendly).
#
# For users who can't reach github.com / raw.githubusercontent.com. Pulls the
# shared installer from Volcengine TOS and runs it with the TOS distribution
# channel preselected (Codex preselected as the harness). Codex installs from
# a TOS-hosted git repo (dumb HTTP) and keeps remote update support.
#
# One-liner:
#   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/codex-memory-plugin/tos-install.sh)
#
# Env overrides:
#   OPENVIKING_TOS_BASE  default: https://ovrelease.tos-cn-beijing.volces.com
#   (every install.sh env override applies too.)

set -euo pipefail

TOS_BASE="${OPENVIKING_TOS_BASE:-https://ovrelease.tos-cn-beijing.volces.com}"
TOS_BASE="${TOS_BASE%/}"
export OPENVIKING_TOS_BASE="$TOS_BASE"
export OPENVIKING_SHARED_INSTALL_URL="${OPENVIKING_SHARED_INSTALL_URL:-$TOS_BASE/memory-plugin-shared/install.sh}"

# Fetch the real installer to a file (not a pipe) so it keeps the terminal on
# stdin for its interactive prompts.
installer=$(mktemp "${TMPDIR:-/tmp}/ov-codex-install.XXXXXX") || { echo "mktemp failed" >&2; exit 1; }
trap 'rm -f "$installer"' EXIT
curl -fsSL -o "$installer" "$OPENVIKING_SHARED_INSTALL_URL"
bash "$installer" --harness codex --dist tos "$@"
