#!/bin/bash
set -e

cat >&2 <<'EOF'
[DEPRECATED] crates/ov_cli/install.sh no longer installs OpenViking CLI.

Install the CLI from npm instead:

  npm i -g @openviking/cli

Or run it without installing:

  npx @openviking/cli --help

The old raw GitHub installer path is kept only to avoid a silent 404 for
existing links. It intentionally does not download binaries from GitHub or TOS.
EOF

exit 1
