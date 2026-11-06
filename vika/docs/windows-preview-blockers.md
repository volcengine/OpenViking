# Native Windows preview: blockers for a single-command install

Status measured on a non-elevated Windows 11 workstation (10.0.22631, Windows
PowerShell 5.1.22621.6133, Node.js v24.19.0, npm 12.0.2) at \`vika-win\`
commit \`d417579cb4b2c19e0d2eb48f5388d3d8dcac876e\`.

## What works

- \`scripts/install-windows.ps1 -SkipSetup -SkipPathUpdate\` installs a release
  under \`%USERPROFILE%\\.vika\\releases\\...\`, creates the \`current\` junction and both
  launchers, without Administrator rights and without changing PATH.
- The release bundles Pi and Durable (both \`1.4.0\`, pinned in
  \`packages/daemon/package.json\`) and validates the bundled Pi with
  \`check-pi.mjs\`. The daemon always runs the bundled Pi, never the one on PATH.
- Model access comes from the existing \`~/.pi/agent\` config: \`pi auth check\`
  returned \`ready\`, and a non-interactive managed-Pi turn answered in ~2.8 s.
- \`~/.openviking/ovcli.conf\` was detected and the OV server on
  \`127.0.0.1:1933\` was reachable.

## Blockers

1. **No single-command install.** Windows has no published artifact and no
   \`install.sh\` equivalent. \`install-windows.ps1\` copies the current checkout, so
   a release is only installable from a working tree and there is no
   \`--version\`-style install. Automatic upgrades are disabled on Windows and
   \`WINDOWS.md\` documents a manual stopped-installation update only.

2. **npm 12 blocks the bundled Lark CLI.** \`npm ci\` in the release prints
   \`1 package had install scripts blocked ... @larksuite/cli@1.0.94 (postinstall: node scripts/install.js)\`.
   The installer then reports success while
   \`current\\node_modules\\@larksuite\\cli\\bin\\lark-cli.exe\` is absent, so no Lark
   connector can start. \`test-install-windows.ps1\` checks the binary, but CI runs
   on an npm version where lifecycle scripts still execute, so CI stays green.
   Workaround: \`node scripts\\install.js\` inside the installed package puts
   \`lark-cli 1.0.94\` in place.

3. **No bundled model configuration.** Pi and Durable are bundled, but model
   credentials still come from the user's \`~/.pi/agent\`. On a clean machine,
   \`vika setup\` cannot complete the model part, so "only App ID and App Secret"
   is not achievable. \`~/.vika/secrets.env\` supports built-in provider API keys,
   but the preview neither documents nor offers that during setup.

## Acceptance criteria for the Windows release

- One command, e.g. \`irm <release-url>/install.ps1 | iex\`, installing a versioned
  artifact built from a commit (the Windows counterpart of the TOS Unix
  \`install.sh -s -- --version ...\` flow).
- Bundle the Node runtime, \`node_modules\` (Pi, Durable, Lark CLI binary), verify
  the bundled Pi and Lark CLI versions after extraction, and leave PATH
  untouched; no \`npm ci\` on the user side and no Administrator rights.
- Detect \`~/.openviking/ovcli.conf\` automatically, then run setup and keep the
  manual App ID / App Secret entry path working.
- Make model bootstrap explicit: either add a documented Pi login step to setup
  or accept API keys in a release-provided secrets file, so a clean Windows
  machine can finish setup with Lark credentials plus one model credential.
