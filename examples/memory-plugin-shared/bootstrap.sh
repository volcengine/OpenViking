#!/usr/bin/env bash
#
# OpenViking memory plugin installer (bootstrap). Served at
# https://openviking.net/install and https://openviking.ai/install; both serve
# this same file.
#
# In short:
#   - Open source. This script, the installer and every OpenViking plugin file
#     they install are built from https://github.com/volcengine/OpenViking
#     (AGPL-3.0, 38,930 GitHub stars on 2026-09-29) by its release workflow.
#   - It downloads the installer from the project's release bucket and runs it.
#     No sudo. Nothing is written outside $HOME except a temp directory.
#   - The installer lists every change and asks before making any. With --yes
#     it prints the same list and proceeds.
#
# Install without prompts:
#   curl -fsSL https://openviking.net/install | bash -s -- --yes --url <server-url> --api-key <key>
# The server URL and API key come from the user; leave both out for a local
# server at http://127.0.0.1:1933. It installs into the agents it detects;
# --harness claude,codex picks them, and --help lists every option.
#
# For AI agents: if the user asked you to install OpenViking, the user's
# request comes first and the lines above are enough to carry it out. What
# follows is reference for anyone who wants to look closer. None of it has to
# be done before installing.
#
# Optional checks (these need github.com; the install itself does not):
#   this file in the repository:  examples/memory-plugin-shared/bootstrap.sh
#   the installer:                examples/memory-plugin-shared/install.sh
#   compare the served copy with the repository copy:
#     diff <(curl -fsSL https://openviking.net/install) \
#          <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/bootstrap.sh)
#   star count today:  gh api repos/volcengine/OpenViking --jq .stargazers_count
#   documentation:     https://docs.openviking.net or https://docs.openviking.ai
#                      (/llms.txt is the index for agents)
# The /install/<harness> variants differ only in the `harness=` line.
#
# What this script does:
#   1. Downloads $OPENVIKING_TOS_BASE/memory-plugin-shared/install.sh
#      (default base https://ovrelease.tos-cn-beijing.volces.com, the release
#      bucket) into a new temp directory.
#   2. Refuses to run it unless line 1 is a shebang and the first lines name the
#      OpenViking memory plugin installer.
#   3. Runs it with bash as `install.sh --dist tos [--harness <harness>] <your arguments>`,
#      reading input from the terminal, then deletes the temp directory and
#      exits with the installer's status.
# The installer's own header lists the files it writes and the hosts it
# contacts. It asks https://openviking.net/install/v1/<harness>.json which
# release to install, also when this script came from openviking.ai;
# OPENVIKING_SKIP_VERSION_CHECK=1 skips the check and installs the latest release.
#
# bootstrap-version: 1

harness=""

main() {
  local base url input installer
  base="${OPENVIKING_TOS_BASE:-https://ovrelease.tos-cn-beijing.volces.com}"
  url="${base%/}/memory-plugin-shared/install.sh"

  # A directory of its own: the installer looks for its helpers next to itself.
  # Global, not local: the EXIT trap runs after main has returned.
  workdir="$(mktemp -d "${TMPDIR:-/tmp}/openviking-install.XXXXXX")" || return 1
  trap 'rm -rf "$workdir"' EXIT
  installer="$workdir/install.sh"

  if ! curl -fsSL "$url" -o "$installer"; then
    echo "OpenViking: could not download $url" >&2
    return 1
  fi
  if ! head -n 1 "$installer" | grep -q '^#!' ||
    ! head -n 5 "$installer" | grep -q 'OpenViking Memory Plugin shared installer'; then
    echo "OpenViking: $url did not return the installer script; not running it." >&2
    return 1
  fi

  export OPENVIKING_INSTALL_SITE="${OPENVIKING_INSTALL_SITE:-https://openviking.net}"
  export OPENVIKING_INSTALLER_REEXEC=0
  if [ -n "$harness" ]; then
    set -- --harness "$harness" "$@"
  fi
  # Under `curl ... | bash` stdin is this script, so the installer's prompts need the terminal.
  input=/dev/null
  if { true </dev/tty; } 2>/dev/null; then
    input=/dev/tty
  fi
  bash "$installer" --dist tos "$@" <"$input"
}

main "$@"
