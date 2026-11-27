# Memory Plugin Shared Tooling

This directory holds the memory-plugin installer, the sync script, release tooling tests and the shared test suites. The shared library itself is [`examples/agent-hook-plugin/lib`](../agent-hook-plugin/lib). For contributor guidance, see the [Agent plugin development and maintenance standard](../../docs/en/agent-integrations/18-plugin-development.md) ([中文](../../docs/zh/agent-integrations/18-plugin-development.md)).

## Contents

- `install.sh`: the installer for every harness. Its header comment lists the hosts it contacts and the files it writes; `--help` lists every option and environment variable. `lib/install/` holds its own JavaScript (the JSONC editor, the hooks/MCP config merge, the Kimi Code plugin assembly, skill ownership), which runs from `install.sh` and never from a hook.
- `bootstrap.sh`: served at `https://openviking.ai/install` and `https://openviking.net/install`; downloads `install.sh` and runs it with the arguments it was given.
- `sync.mjs`: copies the modules of `examples/agent-hook-plugin/lib` each in-process plugin imports into that plugin's generated `shared/` copy. Run `node examples/memory-plugin-shared/sync.mjs` once in a fresh checkout before running those plugins' tests.
- `import-history.mjs`: replays recent Claude Code or Codex sessions from this machine into OpenViking; `--help` lists the options.
- `host-table.mjs`: prints the host table of the capability reference from the host modules (`node host-table.mjs --lang en|zh`).
- Tests: `flow.test.mjs` (the flow), `hosts.test.mjs` (every host module), `hook-entry.test.mjs`, the library's unit tests, the installer tests (`install-*.test.mjs`), and `release-marketplace.test.mjs` for the release scripts under `.github/scripts/`. `testing/support.mjs` holds their helpers.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.

# Without prompts:
curl -fsSL https://openviking.ai/install | bash -s -- --yes --harness claude,codex --url <server-url> --api-key <api-key>
```
