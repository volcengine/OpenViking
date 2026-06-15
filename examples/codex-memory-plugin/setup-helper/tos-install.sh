#!/usr/bin/env bash
#
# OpenViking Memory Plugin for Codex — TOS bootstrap (China-friendly).
#
# For users who can't reach github.com / raw.githubusercontent.com. Pulls both
# the installer and the source archive from Volcengine TOS instead of GitHub,
# then hands off to the standard interactive installer.
#
# One-liner:
#   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/codex-memory-plugin/tos-install.sh)
#
# Env overrides:
#   OPENVIKING_TOS_BASE          default: https://ovrelease.tos-cn-beijing.volces.com
#   OPENVIKING_REPO_ARCHIVE_URL  default: $OPENVIKING_TOS_BASE/releases/latest/openviking-source.zip
#   (every install.sh env override applies too.)

set -euo pipefail

TOS_BASE="${OPENVIKING_TOS_BASE:-https://ovrelease.tos-cn-beijing.volces.com}"
TOS_BASE="${TOS_BASE%/}"
export OPENVIKING_REPO_ARCHIVE_URL="${OPENVIKING_REPO_ARCHIVE_URL:-$TOS_BASE/releases/latest/openviking-source.zip}"

# Fetch the real installer to a file (not a pipe) so it keeps the terminal on
# stdin for its interactive prompts. It then sources the repo from the archive
# URL above instead of git clone.
installer=$(mktemp "${TMPDIR:-/tmp}/ov-codex-install.XXXXXX") || { echo "mktemp failed" >&2; exit 1; }
trap 'rm -f "$installer"' EXIT
curl -fsSL -o "$installer" "$TOS_BASE/codex-memory-plugin/install.sh"
bash "$installer"
