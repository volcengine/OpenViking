# OpenViking Memory Plugins for GitHub Copilot

Long-term semantic memory for GitHub Copilot, powered by
[OpenViking](https://github.com/volcengine/OpenViking).

This directory hosts **two** plugins that share one OpenViking server, one
config file, and one TypeScript codebase wherever surface differences allow:

| Target | Path | What it is |
|---|---|---|
| **VS Code Copilot Chat** | [`vscode-extension/`](./vscode-extension/) | A VS Code extension that registers an `@openviking` chat participant + language-model tools + an MCP server entry. |
| **GitHub Copilot CLI** | [`cli-plugin/`](./cli-plugin/) | An npm-distributed stdio MCP server (`openviking-copilot-mcp`) that the new agentic `copilot` CLI mounts via `~/.copilot/mcp-config.json` (or `COPILOT_HOME`). |
| _shared_ | [`packages/shared/`](./packages/shared/) | Common TypeScript: config loader, OpenViking HTTP client, recall ranker, capture sanitiser, async-writer, debug logger. |

Design and rationale: see [`PLAN.md`](../../PLAN.md) at the repo root.

> The legacy `gh copilot` extension (`gh copilot suggest` / `explain`) is
> **not** a target — it's one-shot with no session model, so there's nothing
> meaningful for a memory plugin to attach to.

## Status

Pre-release scaffold. Implementation tracked in the issues under the
`Copilot memory plugins` milestone (epic: #1). Phase 0 findings are recorded in
[`docs/phase-0-spike.md`](./docs/phase-0-spike.md).

Current VS Code support:

- Recall works in default Copilot Chat through the `openviking_recall` language-model tool.
- Capture is participant-scoped: completed `@openviking` participant turns are committed to OpenViking.
- Stable VS Code 1.99 chat APIs expose `vscode.chat.createChatParticipant(...)` for extension-owned participants, but do not expose a global default-chat response event such as `vscode.chat.onDidReceiveChatResponse`. Default `@workspace` turns therefore cannot be captured without VS Code adding a turn-level event.

Current CLI support:

- Recall works through the `openviking_recall` MCP tool.
- Capture works through the `openviking_capture` MCP tool, which the model is asked to call at the end of a turn with `{ user, assistant }`.
- CLI capture is model-discretion based. It can sanitize and commit a turn when called, but it cannot guarantee 100% coverage if the model declines or forgets to invoke the tool.

## Install (preview)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/copilot/setup-helper/install.sh)
```

The setup helper configures `~/.openviking/ovcli.conf`, installs the VS Code `.vsix` when the `code` CLI is available, installs `@openviking/copilot-cli-memory`, merges the Copilot CLI user-level `mcp-config.json` entry, and can optionally add the `copilot()` shell wrapper.

## Quickstart (development)

```bash
# from this directory
npm install            # resolves all three workspaces
npm run typecheck      # tsc across every workspace
npm run test           # Vitest across every workspace
```

Per-workspace commands:

```bash
npm run typecheck -w @openviking/copilot-shared
npm run test      -w openviking-copilot
npm run build     -w @openviking/copilot-cli-memory
```

## Layout

```
examples/copilot/
├── package.json            # workspace root (npm workspaces)
├── tsconfig.base.json      # shared TS config
├── packages/
│   └── shared/             # @openviking/copilot-shared
├── vscode-extension/       # openviking-copilot (VS Code extension)
└── cli-plugin/             # @openviking/copilot-cli-memory (CLI MCP server)
```

## License

Apache-2.0 — same as [OpenViking](../../LICENSE) and the
[Claude Code memory plugin](../claude-code-memory-plugin/).
