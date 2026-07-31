#!/usr/bin/env bash
#
# OpenViking Memory Plugin for Codex — interactive installer.
#
# One-liner:
#   bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
#
# UX mirrors the claude-code installer (colored step output + interactive
# ovcli.conf setup). When stdin is not a TTY (e.g. `curl | bash`) the
# interactive prompts are skipped and existing config / env vars are used.
#
# Env overrides:
#   OPENVIKING_HOME, OPENVIKING_REPO_DIR, OPENVIKING_REPO_URL,
#   OPENVIKING_REPO_REF / OPENVIKING_REPO_BRANCH, OPENVIKING_CLI_CONFIG_FILE.
#   OPENVIKING_REPO_ARCHIVE_URL  when set, fetch the source from this zip instead
#                                of git clone (used by the TOS bootstrap for users
#                                who can't reach GitHub). Requires `unzip`.

set -euo pipefail

SHARED_REL="../../memory-plugin-shared/install.sh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
LOCAL_SHARED="$SCRIPT_DIR/$SHARED_REL"

if [ -f "$LOCAL_SHARED" ]; then
  exec bash "$LOCAL_SHARED" --harness codex "$@"
fi

SHARED_URL="${OPENVIKING_SHARED_INSTALL_URL:-https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh}"
tmp="$(mktemp "${TMPDIR:-/tmp}/ov-memory-install.XXXXXX")" || { echo "mktemp failed" >&2; exit 1; }
trap 'rm -f "$tmp"' EXIT
curl -fsSL -o "$tmp" "$SHARED_URL"
exec bash "$tmp" --harness codex "$@"
