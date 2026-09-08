# Copilot Memory Plugin — Design

This document records the **researched facts**, the **assumptions**, and the **decision** behind the Copilot memory plugin. It is intentionally honest about what Copilot's extension surface does and does not provide, because issue #2842 asked for "Copilot support" without specifying which Copilot surface, and the honest answer turns out to be different in kind from the Claude Code / Codex / Cursor / TRAE / ZCode plugins.

## TL;DR

- **GitHub Copilot exposes no Claude-Code-style lifecycle hooks.** Verified 2026-07-28 against the official GitHub Copilot docs (see [RESEARCH.md](./RESEARCH.md)). There is no `SessionStart`, `UserPromptSubmit`, `PreToolUse`, or `Stop` event that an external plugin can subscribe to. So the auto-recall / auto-capture / URI-guard model used by the other plugins in this repo **cannot be implemented for Copilot**.
- **GitHub Copilot does support MCP servers everywhere that matters**: VSCode Copilot Chat / agent mode (GA), `gh copilot` CLI (GA), JetBrains/Xcode/Eclipse/Cursor/Windsurf IDEs (GA for local, growing for remote), and the GitHub.com Copilot cloud agent + code review (public preview). Configured via three different JSON shapes — see [build-configs.mjs](./scripts/build-configs.mjs).
- **The decision**: ship an **MCP-only plugin** plus an **Agent Skill** that supplies the recall/remember policy the missing hooks would have supplied. This is the maximum useful, non-fake plugin that can be written for Copilot today. It is not a hook adapter because there is no hook surface to adapt to.

## Why this is not a hook adapter

Every other `examples/*-memory-plugin/` in this repo hooks the agent's lifecycle:

| Plugin | Hook surface | How memory gets in/out |
|---|---|---|
| `claude-code-memory-plugin` | `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `Stop` | auto-injected via `hookSpecificOutput.additionalContext`; auto-captured on `Stop` |
| `codex-memory-plugin` | same shape (codex-rs hooks) | same |
| `cursor-memory-plugin` | `sessionStart` / `beforeSubmitPrompt` / `stop` / `preCompact` | same |
| `trae-memory-hooks` | same shape as Cursor | same |
| `zcode-memory-plugin` | assumed Cursor/TRAE-compatible (ZCode's docs are not public; see its DESIGN.md) | same (assumed) |
| `opencode-plugin` | OpenCode native hooks | same |
| **`copilot-plugin` (this one)** | **none** | **the model calls MCP tools itself, guided by an Agent Skill** |

For the first six rows, the plugin is the actor: it intercepts each turn, fetches recall, injects it as additional context, then captures the assistant's reply and commits it. For Copilot, that is impossible — the equivalent events do not exist. The model itself has to be the actor, calling `recall` and `remember` as tools. The Agent Skill in [`skills/openviking-memory/SKILL.md`](./skills/openviking-memory/SKILL.md) is the closest equivalent of the hook policy: it tells the model "recall at the start of substantive work, remember when the user commits to a durable fact".

This is the same conclusion the OpenViking docs already reach for any MCP-only client — see the existing [MCP Clients](../../docs/en/agent-integrations/06-mcp-clients.md) page, which lists Cursor / Trae / ChatGPT / Codex / Claude Desktop as "use the standard `mcpServers` config". Copilot is just another entry in that list, with the difference that Copilot does not also have a hooks plugin the way Cursor / Trae / Codex do.

## What was researched (and is not assumed)

The following are **facts** sourced from official GitHub / VSCode / MCP docs on 2026-07-28. Full source links in [RESEARCH.md](./RESEARCH.md).

1. **VSCode Copilot Chat / agent mode reads `.vscode/mcp.json`** with top-level `servers` (NOT `mcpServers`). HTTP entries are `{ "type": "http", "url": "...", "headers": {...} }`. There is also a user-profile location via the `MCP: Open User Configuration` command. (Source: code.visualstudio.com/docs/agent-customization/mcp-servers)
2. **`gh copilot` CLI reads `~/.copilot/mcp-config.json`** with top-level `mcpServers`. HTTP entries take `type: "http"`, `url`, `headers`, and `tools: ["*"] | ["tool1", "tool2"]`. Also configurable via `copilot mcp add` / `/mcp add`. (Source: docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers)
3. **GitHub.com repo-level cloud agent + code review** read a JSON pasted into repo Settings → Copilot → MCP servers. Required keys are `type` and `tools`. Secrets MUST be referenced as `${COPILOT_MCP_*}` (Agents secrets). Status: **public preview**. Supports tools only (no resources / prompts); does not support OAuth-protected remote servers (API-key auth only). (Source: docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers)
4. **Agent Skills are an open standard** (https://github.com/agentskills/agentskills) that Copilot cloud agent, code review, CLI, app, and IDE agent mode all consume. Locations: `.github/skills`, `.claude/skills`, `~/.copilot/skills`, `~/.agents/skills`. (Sources: docs.github.com/en/copilot/concepts/agents/about-agent-skills, github.com/agentskills/agentskills)
5. **The enterprise "MCP servers in Copilot" policy is disabled by default**, but only affects Copilot Business / Enterprise subscriptions from organizations that configure it. Copilot Free / Pro / Pro+ / Max are NOT affected. (Source: docs.github.com/en/copilot/concepts/context/mcp)
6. **There are no `SessionStart` / `Stop` / `UserPromptSubmit` equivalents** in any of the four official docs pages above, nor in the GitHub Copilot Chat extensions / participant API documentation. The closest thing is "automatic tool use" — the model decides to invoke an MCP tool based on context.

## What is assumed (and needs confirmation)

Each of these is plausible but should be validated by a real user / maintainer who has Copilot in front of them. They do not affect the shipped code's correctness; they affect the **documentation accuracy** and the **Agent Skill effectiveness**.

1. **The skill's recall-on-start-of-turn policy actually gets followed by the model.** Copilot is documented to call MCP tools automatically when relevant; whether "automatically when relevant" extends to "every substantive turn" is a model-behavior question, not a docs question. If users report that recall is not happening, the skill's `description` field should be tuned (Agent Skills are loaded based on description matching). The fix is one paragraph in `SKILL.md`, not a code change.
2. **The exact set of OpenViking tools that Copilot cloud agent / code review accept at repo level.** The GitHub docs say "tools only"; whether every OV tool surfaces correctly is a server-side question. The plugin defaults to a conservative read-only allowlist (`recall, search, find, read, list, grep, glob`) for repo-level configs precisely because cloud agent runs tools autonomously without approval.
3. **VSCode auto-discovery of Claude Desktop config** (`chat.mcp.discovery.enabled`) means a user who already has OpenViking wired into Claude Desktop may not need this plugin at all. Documented in INSTALL.md as a shortcut.
4. **Whether `~/.copilot/skills/` is the canonical personal-skill path on every Copilot CLI version.** The docs list `.github/skills`, `.claude/skills`, `~/.copilot/skills`, `~/.agents/skills`. The installer writes to `~/.copilot/skills` by default but accepts `COPILOT_SKILLS_DIR` to override.

## What is intentionally NOT shipped

- **No fake hook adapter.** A `hooks/hooks.json` modelled on the ZCode plugin would not work — Copilot would simply never invoke it. Shipping one would mislead users into thinking auto-recall was wired up. The plugin says so explicitly in its post-install message.
- **No Chat Participant VSCode extension.** VSCode Copilot Chat does support a `@participant` extension API (a compiled VSCode extension, not a JSON config), and a participant could in principle inject context into every chat turn. That is a much larger artifact (a TypeScript VSCode extension with a build/publish pipeline) and would still not give us a `Stop` capture hook. It is documented in [RESEARCH.md](./RESEARCH.md) as a possible future direction; not implemented.
- **No marketplace entry.** Unlike Codex's `codex plugin` marketplace, Copilot does not have a JSON-catalog plugin model that this plugin could register in. The closest is the GitHub MCP Registry (public preview), which is for MCP servers generally, not for memory plugins. Out of scope.

## Reuse map (what is NOT duplicated)

- The optional [`servers/mcp-proxy.mjs`](./servers/mcp-proxy.mjs) is a 30-line config adapter over [`memory-plugin-shared/lib/mcp-proxy-core.mjs`](../memory-plugin-shared/lib/mcp-proxy-core.mjs) (same `createOpenVikingMcpProxy` used by the ZCode / Codex / Cursor plugins). Credentials are resolved by [`memory-plugin-shared/lib/agent-hook-runtime.mjs`](../memory-plugin-shared/lib/agent-hook-runtime.mjs) → [`credentials.mjs`](../memory-plugin-shared/lib/credentials.mjs). No copy is made into this plugin.
- The Agent Skill follows the same YAML-frontmatter convention as the OpenViking server-side skills under [`examples/skills/`](../skills/), and is compatible with the [agentskills open standard](https://github.com/agentskills/agentskills).

## Testing

[`scripts/build-configs.test.mjs`](./scripts/build-configs.test.mjs) (run with `node --test`) covers:

- `normalizeSpec` rejects non-`http(s)` URLs and trims whitespace.
- VSCode config uses top-level `servers` (NOT `mcpServers`) with `type: "http"`, and omits headers when no API key is provided.
- Copilot CLI config uses top-level `mcpServers` with `type`, `url`, `headers`, and a non-empty `tools` array that includes `recall` and `remember`.
- GitHub repo config always substitutes `${COPILOT_MCP_*}` for the API key (never a hard-coded value), and defaults to a read-only tool allowlist (no `remember`) because cloud agent runs tools autonomously.
- The stdio proxy config points at `servers/mcp-proxy.mjs` via a `__OPENVIKING_COPILOT_ROOT__` token.
- `serverName` override propagates to all configs.

The installer was end-to-end tested against all three targets (`--cli`, `--vscode`, `--repo`) in a throwaway `HOME`; the resulting JSON matched the official-doc shapes byte-for-byte.

## Open questions / future work

- **A Copilot Chat `@openviking` participant extension** (compiled VSCode extension) that auto-injects recall into every chat turn. This is the only path to "real" hook parity on VSCode, and it is a meaningful new artifact rather than a config snippet. Out of scope for the first cut.
- **Toolset-aware skill policy**: once OpenViking exposes its tool list as a Copilot toolset, the skill could explicitly reference `@openviking/recall` etc. rather than relying on automatic tool selection.
- **GitHub MCP Registry entry**: list OpenViking in the public registry so users can install with `/mcp search` once that feature is GA.

## Provenance

provenance: claude-opus-4-8 | high(extended) | 2026-07-28 | GitHub issue #2842; Copilot extension surface researched live against official GitHub / VSCode / MCP docs (see RESEARCH.md).
