# Copilot setup helper

Interactive installer for the OpenViking memory plugins for GitHub Copilot.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/copilot/setup-helper/install.sh)
```

It can install both targets:

- VS Code extension: packages or uses an existing `.vsix`, then runs `code --install-extension`.
- GitHub Copilot CLI: installs `@openviking/copilot-cli-memory`, merges the user-level `mcp-config.json` server entry, and can add the optional `copilot()` wrapper.

The installer is safe to re-run. It updates marker-managed shell snippets in place and writes a timestamped `.bak.YYYYMMDD-HHMMSS` file before changing any existing config or shell file.

## Environment overrides

| Variable | Default |
| --- | --- |
| `OPENVIKING_HOME` | `$HOME/.openviking` |
| `OPENVIKING_REPO_DIR` | `$OPENVIKING_HOME/openviking-repo` |
| `OPENVIKING_REPO_URL` | `https://github.com/volcengine/OpenViking.git` |
| `OPENVIKING_REPO_BRANCH` | `main` |
| `OPENVIKING_CLI_CONFIG_FILE` | `$OPENVIKING_HOME/ovcli.conf` |
| `OPENVIKING_COPILOT_VSIX` | unset; package from source when needed |
| `COPILOT_MCP_JSON` | `${COPILOT_HOME:-$HOME/.copilot}/mcp-config.json` |

## After install

```bash
openviking-copilot-mcp --check
copilot
```
