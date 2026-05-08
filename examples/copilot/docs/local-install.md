# Local Copilot plugin install

Issue: <https://github.com/jwayong/OpenViking/issues/39>

This flow installs the Copilot memory plugins from this repo or from local artifacts. It does not require the VS Code Marketplace or the public npm registry for the OpenViking plugin packages.

## One command

```bash
bash examples/copilot/setup-helper/install.sh
```

The setup helper defaults to `OPENVIKING_INSTALL_SOURCE=local`:

- builds or reuses a local VS Code `.vsix`, then runs `code --install-extension <vsix> --force` when the `code` CLI exists;
- builds or reuses a local `@openviking/copilot-cli-memory` `.tgz`, then runs `npm i -g <tgz>`;
- merges the OpenViking MCP server entry into `${COPILOT_HOME:-$HOME/.copilot}/mcp-config.json`;
- backs up existing config files with `.bak.YYYYMMDD-HHMMSS` before mutation.

## Artifact install

Build artifacts once and reuse them across machines:

```bash
OPENVIKING_COPILOT_ARTIFACT_DIR=$PWD/.openviking-artifacts \
  bash examples/copilot/setup-helper/install.sh
```

Then install from prebuilt local files:

```bash
OPENVIKING_COPILOT_VSIX=/path/to/openviking-copilot.vsix \
OPENVIKING_COPILOT_CLI_TGZ=/path/to/openviking-copilot-cli-memory-0.0.0.tgz \
  bash examples/copilot/setup-helper/install.sh
```

## Repo install

For development, point the helper at a local checkout and let it build artifacts there:

```bash
OPENVIKING_REPO_DIR=/path/to/OpenViking \
OPENVIKING_REPO_BRANCH=feature/copilot-memory-plugin-plan \
  bash examples/copilot/setup-helper/install.sh
```

If the checkout has local changes, the helper fetches but does not reset it.

## Optional registry mode

Public publishing is tracked separately in #31 and #32. If a future private or public registry is available, opt into registry install explicitly:

```bash
OPENVIKING_INSTALL_SOURCE=registry bash examples/copilot/setup-helper/install.sh
```

## Private extension galleries

Standard Microsoft VS Code users should use `.vsix` install for local/private testing. Custom extension galleries require a host that supports overriding `extensionsGallery` (for example an Open VSX-compatible gallery in OSS/VSCodium-style builds), so this repo does not depend on that path for local installs.

## Validate

```bash
openviking-copilot-mcp --check
code --list-extensions | grep -i openviking
```
