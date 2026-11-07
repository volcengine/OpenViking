# OpenViking belongs to Vika

Read this before you change how Vika syncs, recalls or exposes OpenViking memory, and before you add or upgrade an agent runtime.


Vika owns OpenViking memory for every agent it runs:

- The daemon writes the conversation (`ov-session-context.mjs`) and shows memory before a turn (`ov-recall.mjs`).
- The model reads and writes through the Core's `openviking_*` tools (`ov-tools.mjs`, `actions/`).
- Nothing else may touch the instance's OV memory.

Keep these rules when you change any of it:

- **No peers.** Synced lines carry no `peer_id`. A line's `from=` names its speaker, and extraction files a person's preferences under that name (`memories/preferences/<name>/`). Events are kept once, in the instance's own memory.
- **Extraction never writes the agent itself.** Every session (sync and `openviking_remember`) is created with `memory_policy.memory_types` = `OV_MEMORY_TYPES` (profile, preferences, entities, events). Never add `soul` or `identity`: a line that talks the agent into a new name or a global rule would rewrite who it is. Its name and role come from the configuration, and its standing rules are the owner's preferences.
- **No words in anyone's mouth.** `openviking_remember` submits a real inbound message, rendered exactly as the sync renders it (`formatCognitionJournalLine`), never text the model wrote as a user line.
- **What the daemon keeps is not a tool target.** `add_resource` refuses mail, meeting notes, timer state, memories, sessions, peers and skills (`reservedTarget`).

## Every runtime keeps its own OpenViking plugin out

Codex, TraeX, Claude Code, Pi and OpenCode each have an OpenViking plugin of their own, and the owner may have installed it. In a runtime Vika launches it must not load. It speaks as the whole process, with no idea who sent a line, and its hooks recall and capture on their own.

A runtime is registered in `runtime-catalog.mjs` with a required `ovIsolation`:

- `launch-args`: the launcher passes arguments that switch the plugin off.
- `config-home`: the launcher points the runtime at a Vika-owned config home.
- `closed`: the runtime loads no plugins at all (Durable).
- `remote`: it runs elsewhere (Ark).

The registration throws without one.

**Adding or upgrading a runtime:**

1. Find how that runtime discovers plugins, hooks, MCP servers, extensions and skills, and how its own CLI flags or environment can turn OpenViking's off. Never rely on the plugin honoring a switch: Vika does not control its version. Only OpenViking's pieces go; the owner's other plugins stay.
2. Implement it in `ov-isolation.mjs`, which only reads the owner's files and never writes them. Call it from the runtime's launcher.
3. Test it in `test/ov-isolation.test.mjs` and in the runtime's own launch test.
4. Verify against the real CLI before you rely on it. Install the OpenViking plugin into a throwaway home (`CODEX_HOME`, `TRAE_HOME` with `TRAECLI_HOME`, `CLAUDE_CONFIG_DIR`, `PI_CODING_AGENT_DIR`, `XDG_CONFIG_HOME`) and check what actually loaded:
   - Codex/TraeX: app-server `hooks/list` and `mcpServerStatus/list`.
   - Claude Code: the stream-json `init` event (`plugins`, `mcp_servers`) and hook events.
   - Pi: RPC `get_commands`.
   - OpenCode: a marker file plugin; `opencode debug config` alone is not proof, since a plugins-folder file loads even when the config lists none.

   Never point these at the owner's real homes: some CLIs write to the real config even when only part of the home is redirected. TraeX, for example, needs `TRAE_HOME`, not just `TRAECLI_HOME`.

   CLIs differ from their help text. Two examples:
   - Pi 1.4.0 ignores `--no-extensions` once an `--extension` is present, but honors `-ne`.
   - Codex merges `-c mcp_servers={}` instead of replacing the table.

   Only the real binary tells you which way it behaves.

The `OPENVIKING_*` variables `openVikingAgentEnv` sets (`deployment-config.cjs`) keep a plugin that loads anyway inert. They are a second line, not the mechanism.
