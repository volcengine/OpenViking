# OpenViking Memory Plugins for GitHub Copilot

Long-term semantic memory for GitHub Copilot, powered by
[OpenViking](https://github.com/volcengine/OpenViking).

This directory hosts **two** plugins that share one OpenViking server, one
config file, and one TypeScript codebase wherever surface differences allow:

| Target | Path | What it is |
|---|---|---|
| **VS Code Copilot Chat** | [`vscode-extension/`](./vscode-extension/) | A VS Code extension that registers an `@openviking` chat participant + language-model tools + an MCP server entry. |
| **GitHub Copilot CLI** | [`cli-plugin/`](./cli-plugin/) | An npm-distributed stdio MCP server (`openviking-copilot-mcp`) that the new agentic `copilot` CLI mounts via its `mcp.json`. |
| _shared_ | [`packages/shared/`](./packages/shared/) | Common TypeScript: config loader, OpenViking HTTP client, recall ranker, capture sanitiser, async-writer, debug logger. |

Design and rationale: see [`PLAN.md`](../../PLAN.md) at the repo root.

> The legacy `gh copilot` extension (`gh copilot suggest` / `explain`) is
> **not** a target — it's one-shot with no session model, so there's nothing
> meaningful for a memory plugin to attach to.

## Status

Pre-release scaffold. Implementation tracked in the issues under the
`Copilot memory plugins` milestone (epic: #1).

Current VS Code support:

- Recall works in default Copilot Chat through the `openviking_recall` language-model tool.
- Capture is participant-scoped: completed `@openviking` participant turns are committed to OpenViking.
- Stable VS Code 1.99 chat APIs expose `vscode.chat.createChatParticipant(...)` for extension-owned participants, but do not expose a global default-chat response event such as `vscode.chat.onDidReceiveChatResponse`. Default `@workspace` turns therefore cannot be captured without VS Code adding a turn-level event.

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
