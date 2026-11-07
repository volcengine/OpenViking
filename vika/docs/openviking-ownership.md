# OpenViking belongs to Vika

Read this before you change how Vika syncs, recalls or exposes OpenViking memory, and before you add or upgrade an agent runtime.


Vika owns OpenViking memory for every agent it runs:

- The daemon writes the conversation (`ov-session-context.mjs`) and shows memory before a turn (`ov-recall.mjs`).
- The model reads and writes through the Core's `openviking_*` tools (`ov-tools.mjs`, `actions/`).
- Nothing else may touch the instance's OV memory.

Keep these rules when you change any of it:

- **No peers.** Synced lines carry no `peer_id`. An OV line's `from=` is a stable `person-<hash>` derived from source, connector instance, tenant, actor type and the complete actor ID. Chat and non-chat events use the same attribution. Extraction is instructed to use that key in `memories/preferences/<person-key>/` and profile headings; `display=` is only a readable label. Events are kept once, in the instance's own memory.
- **Extraction never writes the agent itself.** History sessions are created with `memory_policy.memory_types` = `[profile, entities, events]`; explicit `openviking_remember` sessions use `[preferences]`. These separate policies prevent routine history commits from also extracting preferences. Never add `soul` or `identity`: a line that talks the agent into a new name or a global rule would rewrite who it is. Its name and role come from the configuration, and its standing rules are the owner's preferences.
- **No words in anyone's mouth.** `openviking_remember` submits a real inbound message with an identifiable sender, rendered exactly as the sync renders it (`formatOVJournalLine`). The optional note is assistant context. Use it for persistent preferences stated or confirmed by that sender; quotes, reported wishes and assistant notes do not establish a personal preference. General facts use ordinary history extraction. Preserve stated applicability; do not promote a one-time instruction into a global rule.
- **What the daemon keeps is not a tool target.** `add_resource` refuses mail, meeting notes, timer state, memories, sessions, peers and skills (`reservedTarget`). Normalize repeated slashes before checking either `to` or `parent`; reject traversal and ambiguous encodings before uploads. An exact replacement of their resource ancestor is also refused.

## Preference capture and remaining attribution risk

History sync and explicit remember previously shared the same four memory types. Remember was an additional immediate extraction, so later threshold or epoch-finalization commits could extract preferences again from mixed history. Their policies are now disjoint: history extracts profile/entities/events, and remember extracts only preferences. Conversation history still contains the original messages.

The current OV API can allow memory types and self/peer namespaces. It has no target-person constraint for the `user` field that determines `preferences/<person-key>/`: `from=` is text, and the extraction model still chooses that field. Peer routing would change the storage model and is not used as a substitute. Single-input capture reduces ambiguity but does not guarantee correct attribution, prevent prompt injection, or prove that quoted content belongs to the sender. Profile extraction remains automatic and can still attribute facts to the wrong person.

## Request attribution and private mail

The four `openviking_*` read tools and `read_email` require `decision_id`, with `decision_instance` to disambiguate connector instances. The model declares the input it is serving. Core resolves it to one accepted event, compares its authenticated sender to that connector instance's owner, and logs the event sequence and resulting mail access. Reads neither settle a decision nor apply the outbound freshness fence.

For a non-owner request, find/search use an OV path exclusion filter for the authenticated user's mail archive and discard private mail and ancestor-summary hits before returning either text or structured data. Direct read/list calls cannot target that archive; recursive listings filter its entries and advance pagination by the original server page. Optional semantic auto-recall uses the same exclusion. Resolve the OV user through the API key's authenticated identity, not the configuration's user hint.

An incoming `email.received` event may use `read_email` for its own mail and attachments only. It grants no general mailbox access, even if its From header claims to be the owner. Owner requests can read mail; disclosure remains limited by the prompt to the owner's direct chat.

This association is a model declaration, not an isolation boundary: a model can select a genuine but unrelated owner event. Prompts explicitly forbid borrowing another request's authority in a mixed turn. Already-loaded context, session history and memories derived from mail are shared and are governed by disclosure prompts; this change does not add provenance-based access control to them or isolate turns.

### Existing memories

Old name-only preference directories and profile headings remain available as historical memory, but are never automatically identified as owner/sender preferences. No migration guesses identity from a display name. New sync and explicit remember calls use stable keys. Built-in extraction templates instruct the model to preserve these keys; installations with custom profile/preference templates must adopt the same rule. Extraction is model-based, so a malformed or name-based output is not promoted to owner guidance.

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
