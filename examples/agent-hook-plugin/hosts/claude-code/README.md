# OpenViking Memory Plugin for Claude Code

Long-term semantic memory for Claude Code, powered by [OpenViking](https://github.com/volcengine/OpenViking). Recall happens automatically before every prompt and capture after every turn; the model does not need to call any MCP tool.

This README covers installing the plugin and what only the Claude Code integration has. Recall, capture, commits, session-start context, the offline queue and every setting are shared with the other hosts and documented in:

- [Claude Code integration guide](../../../../docs/en/agent-integrations/02-claude-code.md)
- [Capability reference](../../../../docs/en/agent-integrations/16-capability-reference.md) (shared behaviour, settings, host events)
- [Client configuration](../../../../docs/en/configuration/02-client.md) (plugin settings in `ovcli.conf`)

## Quick Start

### One-line installer (recommended)

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --harness claude
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

macOS / Linux only. Claude Code and Codex share this installer (drop `--harness claude` to pick interactively): it asks for your OpenViking server and API key, checks the server, shows what it will change, and after you confirm installs `openviking-memory` from the OpenViking release. Claude Code 2.1.224 and newer then updates the plugin on its own. The stdio MCP proxy reads `ovcli.conf` at runtime, so no shell wrapper or `.mcp.json` rendering is needed. Re-running is safe.

If you'd rather do it by hand, follow the four steps below.

### Manual setup

#### 1. Have an OpenViking server reachable

Either run one locally or point at a remote one. The [quickstart guide](../../../../docs/en/getting-started/02-quickstart.md) walks through both options, including how to issue API keys for remote use. Default port is `1933`; local mode runs without authentication.

Verify it's up:

```bash
curl http://localhost:1933/health   # or your remote URL
```

#### 2. Tell the plugin where the server is

Easiest path — write `~/.openviking/ovcli.conf` (the same file `ov` CLI uses):

```json
{
  "url": "https://your-openviking-server.example.com",
  "api_key": "<your-api-key>",
  "account": "my-team",
  "user": "alice"
}
```

For purely local mode (`http://127.0.0.1:1933` with no auth) you can skip this step entirely — the plugin will silently use the local default.

If `ov.conf` is what you already maintain, the plugin reads it too — see [Client configuration](../../../../docs/en/configuration/02-client.md) for the priority chain and every setting.

#### 3. Install the plugin

**Remote marketplace (recommended)** — no clone needed. The repo root ships a `.claude-plugin/marketplace.json` whose entry fetches this plugin via `git-subdir`:

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install openviking-memory@openviking
```

(`claude plugin marketplace add volcengine/OpenViking` works too, but clones the whole repo as the marketplace.)

If you skipped step 2, configure the connection afterwards: write `~/.openviking/ovcli.conf` by hand, run `node <plugin-dir>/hosts/claude-code/setup.mjs` (an interactive wizard bundled with the plugin), or just run the one-line installer.

**Local directory (development)** — registers this checkout so edits to `scripts/` and `hooks/` take effect on the next hook invocation without reinstalling. From the OpenViking repo root:

```bash
claude plugin marketplace add "$(pwd)/examples"
claude plugin install openviking-memory@openviking
```

> Both commands install at user scope by default — the plugin is active from any directory. We don't pass `--scope user` explicitly because older Claude Code 2.0.x builds (e.g. 2.0.76) reject the flag. On newer builds that do accept `--scope`, you can lift a local-scoped install to user scope with `claude plugin enable openviking-memory@openviking --scope user`.
>
> Directory-mode caveat: moving / renaming / deleting the source dir, or `git checkout`-ing to a branch without these files, breaks the plugin. Both modes register a marketplace named `openviking`, so the plugin id is always `openviking-memory@openviking`; switch modes by removing the marketplace and re-adding the other source (the installer does this automatically).

##### Legacy mode (Claude Code < 2.0)

`claude plugin` ships in Claude Code 2.0+ (Oct 2025). Older builds still have `claude mcp add` and the hooks system, so the same functionality can be wired up by hand:

```bash
PLUGIN_DIR="$(pwd)/examples/agent-hook-plugin"

# stdio MCP proxy — reads ovcli.conf / OPENVIKING_* itself, no header wiring needed.
claude mcp remove openviking -s user 2>/dev/null
claude mcp add --scope user -e OPENVIKING_HOOK_SOURCE=claude-code openviking -- node "$PLUGIN_DIR/servers/mcp-proxy.mjs"

# Merge plugin hooks into ~/.claude/settings.json (with backup).
mkdir -p ~/.claude && [ -f ~/.claude/settings.json ] || echo '{}' > ~/.claude/settings.json
cp -p ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%s)
sed "s|\${CLAUDE_PLUGIN_ROOT}|$PLUGIN_DIR|g" "$PLUGIN_DIR/hooks/hooks.json" > /tmp/ov-hooks.json
jq --slurpfile h /tmp/ov-hooks.json '.hooks = ((.hooks // {}) * $h[0].hooks)' \
  ~/.claude/settings.json > /tmp/ov-settings.json
jq -e . /tmp/ov-settings.json >/dev/null && mv /tmp/ov-settings.json ~/.claude/settings.json
rm -f /tmp/ov-hooks.json
```

The one-line installer does not set this up: when it finds a pre-2.0 build it skips Claude Code and asks you to upgrade.

#### 4. Start Claude Code

```bash
claude
```

If it doesn't seem to fire, set `OPENVIKING_DEBUG=1` and check `~/.openviking/logs/cc-hooks.log`.

## Verify

- Run `/openviking-memory:ov` in Claude Code: it prints the server, identity, last injection / recall and toggles (`hosts/claude-code/ov-status.mjs`).
- The statusline under the input box shows `OV ✓` once the server answers (see [Statusline](#statusline)).
- Run the doctor (see [Troubleshooting](#troubleshooting)).

## Hook timeouts

Defaults in `hooks/hooks.json`:

| Hook | Timeout | Notes |
|------|---------|-------|
| `SessionStart` | `120s` | Generous because resume/compact may pull a large archive overview |
| `UserPromptSubmit` | `60s` | Allows the local compressor to finish; its own timeout remains shorter so the hook can degrade safely |
| `PreToolUse` | `5s` | `scripts/uri-guard.mjs claude-code` |
| `PostToolUse` (`Read`) | `5s` | Skill experience, off by default |
| `Stop` | `45s` | Capture; detached, so the user does not wait |
| `PreCompact` | `30s` | Synchronous commit before Claude Code rewrites the transcript |
| `SessionEnd` | `30s` | Final commit; detached |
| `SubagentStop` | `45s` | Subagent capture and commit; detached |

## Statusline

The plugin renders a one-line status of OpenViking under your Claude Code input box. The installer registers it in `~/.claude/settings.json` (CC's plugin manifest doesn't accept a `statusLine` field, so this is the only way to wire it in).

Examples:

```text
OV ✓ │ Fable 5 · ctx 42% │ ↩ 6 mem · 50ms          6 memories injected; model + context usage
OV ⚠ slow                                  probe missed the 1 s budget (server may be lagging)
OV ✗ offline                               server unreachable
OV ⚡ bypass │ Fable 5 · ctx 42%            OPENVIKING_BYPASS_SESSION* matched
OV ✓ │ ✎ 573/20k · 2 arch                  pending capture, two archives produced this session
OV ✓ │ 🔗 resumed │ +3 today               session re-hydrated; 3 archives committed today
```

The `ctx` percentage reproduces Claude Code's native context indicator (a custom statusLine replaces it), with the native color thresholds: `<70%` dim, `70–89%` yellow, `≥90%` red. Hide it with `OPENVIKING_STATUSLINE_CTX=off`.

For the full segment glossary and personalization recipes (hide segments, recolor, compose with another statusline, add a custom segment), see [`STATUSLINE.md`](./STATUSLINE.md).

Data flow:

- After each event the hook (`hosts/claude-code/host.mjs`, through `state.mjs`) writes small snapshots to `~/.openviking/state/{last-recall,last-capture,last-session-event,daily-stats}.json` after each turn.
- `scripts/statusline.mjs` reads those snapshots plus a 5 s shared cache of `GET /health`.
- Network calls have a hard 1 s timeout. Cache is shared across CC sessions to prevent stampedes.

Disable / customize:

- `OPENVIKING_STATUSLINE=off` — silence without removing the registration.
- `NO_COLOR=1` (or non-TTY) — strip ANSI colors automatically.
- Remove entirely: `jq 'del(.statusLine)' ~/.claude/settings.json > t && mv t ~/.claude/settings.json`.
- Already had a custom statusline? The installer prompts replace / skip / manual-compose.


## Source cards

Under each answer, the plugin adds a card listing the OpenViking sources that answer drew on: what auto-recall added to the prompt, and what Claude searched for and read on its own. The card is collapsed by default to one line:

```
OV · 14 sources · 6 past events · 5 work memories · 2 team docs · 1 skill · 1 read in full  [Expand]
```

Expanded, it lists every source and Claude's own lookups:

```
OV · 14 sources · … · 1 read in full  [Collapse]
  ◷ 10/3 发版检查清单补充 · found by Claude · read in full
  ⚙ openviking-release · auto-recalled
  …
Claude's own lookups
  ⌕ Searched “lark-daemon release” · 6 results
  ▤ Read 10/3 发版检查清单补充
```

- Groups: ★ preferences, ◷ past events, ◆ work memories (notes, lessons, your agents' memories), ▤ team docs, ⚙ skills.
- **read in full**: Claude opened the file with `read`, through the MCP tool or `ov read`/`cat`/`abstract`/`overview`. A search hit doesn't count.
- An answer that used nothing from OpenViking gets no card.
- Expand or collapse one card with its button. Expand or collapse every card with `/openviking-usage expand` or `/openviking-usage collapse`.

**Requirements.** Cards are a Claude Code hooks module (`modules` in `hooks/hooks.json`, entry `mods/register.ts`, code in `mods/usage/`). They need Claude Code 2.1.286 or newer with hooks modules enabled. Claude Code ignores the module and runs the command hooks as before in these cases:

- On versions without modules.
- When modules are switched off, for example with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=0` or by the rollout flag.

On builds between the first modules release and 2.1.286 with modules switched on, the module fails to load because it uses `session.append`. Claude Code logs that failure and still registers the command hooks.

**Data.** The module reads the `<openviking-context>` block this plugin's `UserPromptSubmit` hook returns, and the OpenViking MCP and `ov` CLI calls Claude makes. It makes no network calls and writes nothing to OpenViking.

It keeps each answer's source URIs, scores, redacted search queries, and the transcript row id and a hash of the reply text it sits under (the desktop finds a card's reply by that hash) in Claude Code's plugin store, for the last 20 sessions, so cards survive `claude --continue`. Shell commands and prompt text are not stored. The card isn't sent to Claude, so it costs no tokens.

**Development.** `claude plugin test examples/agent-hook-plugin` runs `mods/usage/sources.test.ts` and `mods/seal/seal.test.ts`. Prompt sealing's decisions are also tested with `node --test` in `hosts/claude-code/prompt-seal.test.mjs` and `hosts/claude-code/seal-prompt.test.mjs`. After `claude --plugin-dir examples/agent-hook-plugin` has generated `.claude-plugin/types/`, run `tsc -p examples/agent-hook-plugin/mods/usage` (or `mods/seal`) to type-check the module.

## Seal secrets

Off by default; it runs on your machine and needs OpenViking server 0.5.7 or newer. Two settings in `ovcli.conf`, each with an environment override:

```json
{ "plugin": { "claude_code": { "sealSecrets": true, "enrollToolSecrets": false } } }
```

- `sealSecrets` (`OPENVIKING_SEAL_SECRETS`) turns sealing on.
- `enrollToolSecrets` (`OPENVIKING_ENROLL_TOOL_SECRETS`) also sends tool results with enrollment, so a secret first seen in a command's output or a file is saved too. It has no effect while `sealSecrets` is off.

With `sealSecrets` on, Claude reads `{{ov_privacy:…}}` references in place of secrets, and tools get the real values:

- **Your prompt.** Each prompt goes to OpenViking (`POST /api/v1/privacy-configs/seal`) before Claude Code sends it. Secrets already saved in Privacy are replaced with their references. Tokens the server's detector recognises (its built-in rules plus your `custom_rules`, minus `never_mask`) are saved as new entries in the `auto` category, such as `auto/github-k3j2m8`, and replaced too. While a prompt holds such a temporary entry, Claude gets a note telling it to save the entry under a proper name with a short description unless it is clearly for one-off use, use the reference that returns (for example with `ov privacy run`), and tell you what it stored. Slash commands and empty prompts are left alone.
- **Tool results.** Every string in a tool's result (Bash output, a file Read returns, an MCP tool's answer, a failed command's error) is sealed before Claude reads it. Saved secrets are replaced; new ones are enrolled only with `enrollToolSecrets`. The transcript stores the sealed result.
- **Tool arguments.** A reference Claude writes in a tool call is replaced by its value before the tool runs, so the command or the file gets the real secret, while the conversation keeps the reference Claude wrote. Only a whole reference to your own `credential` or `auto` entries is replaced, and only when the server resolves it and the value seals back to the same reference. A string with an `ov privacy` command, OpenViking's own tools, and tools that pass their arguments to a model (`Agent`, `Task`, `SendMessage`, `Workflow`, `Skill`) keep the references as written.
- **Attached and `@`-mentioned files, and reminders.** The text Claude Code injects for the model (a mentioned file's contents, `CLAUDE.md` files found while working, reminders, other hooks' context) is sealed without enrolling. The transcript keeps the engine's own record of these, with the original text.

How it runs and fails:

- **Read once per session.** The plugin runs `node hosts/claude-code/seal-prompt.mjs` once per session to read the settings, how to reach the server (the URL and headers every other hook uses), and whether the server can seal: the helper reads the version from the server's `/health` and compares it with the feature table the plugin ships (`lib/server-features.json`), where sealing needs 0.5.7 (a development build such as `0.5.7.dev44` counts as 0.5.7). The hooks module then sends every seal and resolve call from the hooks module itself, through Claude Code's HTTP API. A changed setting takes effect in a new session (`/clear` counts as one). After a stopped prompt, the next prompt reads all of this again, so sealing resumes once the server can seal. While it is off, nothing is run after that first read.
- **When sealing cannot be done, it stops.** That is: the server is older than 0.5.7, its `/health` fails (the version is unknown), a seal or resolve call fails (the server cannot be reached, answers with an error or a malformed answer, or too slowly: 10 s per call), or `OPENVIKING_SEAL_SECRETS` turns sealing on but the setting helper gives no answer. The prompt is not sent and Claude Code shows why (for a version, the required and the actual one) and what to do: upgrade or reach the server, or turn `sealSecrets` off. A tool result is replaced by a short note saying it was withheld (the tool did run); a tool call whose reference cannot be resolved is blocked with the reason; an attachment is replaced by a short note. To a server that is too old or whose version is unknown, no seal call is made at all. `OPENVIKING_SEAL_SECRETS=0` turns sealing off without running anything, even when the plugin's own helper is broken.
- **The session text** (what Claude is told about references) comes with the first prompt the hooks module seals in a session, and again with the first one after a compaction or after the module reloads.
- **Cost.** One server call per prompt, per tool result and per injected attachment (Claude Code asks once per attachment and keeps the answer), plus a resolve call and a check per reference in a tool call.
- **Not covered.** The system prompt, slash-command arguments, skill bodies, and history written before sealing was turned on are not sealed. OpenViking's own tools are recognised by a name containing `openviking`: with the OpenViking MCP server registered under another name, references in the arguments of its `privacy_upsert`, `write` or `remember` are replaced by their values before the call.
- **Undoing a false positive.** Delete the entry (`ov privacy delete auto <target>`), and add the value to `never_mask` so it is not enrolled again.
- **Requirements.** Sealing is part of the plugin's hooks module (`mods/seal/`), so it needs what [Source cards](#source-cards) need. On a Claude Code that does not load the module it does not run, and nothing is sealed. Reading the setting needs Claude Code's process API (the CLI has it). Where it is missing (or the helper gives no answer), sealing stays off without a message, whatever `ovcli.conf` says; set `OPENVIKING_SEAL_SECRETS=1` there and prompts are stopped with a message that the settings could not be read.
- No sealing setting is a workspace setting, so a repository's `.openviking/config.json` cannot switch them.

To try it with a local server: save a value (`ov privacy upsert credential demo --values-json '{"token":"<value>"}'`), turn `sealSecrets` on, start `claude`, and ask it to `echo` the value: the answer it reads holds `{{ov_privacy:…}}`. Ask it to run `printf '%s' '{{ov_privacy:…}}' > /tmp/t` with that reference: `/tmp/t` holds the value.

## Local recall compressor

`recallCompress` defaults to `off`. With `client`, the recall digest is produced locally through `claude -p` (Haiku with low effort by default, `recallCompressModel` overrides the model), so the token cost stays on your own subscription. With `auto`, the local compressor is used when a healthy `claude` CLI is found, otherwise the server's digest. The compressor subprocess runs with every OpenViking hook disabled so it cannot recurse; a compressor failure falls back to the uncompressed context block. See [Recall digest](../../../../docs/en/agent-integrations/16-capability-reference.md#recall-digest).

## Skill experience

An optional `PostToolUse` hook on `Read` (`hosts/claude-code/skill-experience.mjs`). With `skillExperience` on (`OPENVIKING_SKILL_EXPERIENCE`, default off), reading a `SKILL.md` under a skills directory injects a small block of relevant experience memories from OpenViking, at most `skillExperienceLimit` (default 3).

## Subagent capture

Claude Code is the only hook host that captures subagents: on `SubagentStop` the plugin reads the subagent's transcript and captures it into its own session `cc-<session_id>__subagent-<agent_id>`, then commits. `skipSubagentSessions` turns this off. See [Subagents](../../../../docs/en/agent-integrations/16-capability-reference.md#subagents).

## Troubleshooting

Start with the bundled doctor — it checks the install (marketplace, enablement, hooks, MCP wiring), the resolved config (which file won, API key shown masked), the connection (reachability, auth, `/mcp`) and recent hook activity, and prints a fix for every finding:

```bash
node "$(jq -r '.plugins["openviking-memory@openviking"][0].installPath' ~/.claude/plugins/installed_plugins.json)/hosts/claude-code/ov-memory-doctor.mjs"
```

Or just ask Claude to check the plugin: the `ov-memory-doctor` skill runs the same script and walks the report. When the server runs on the same machine (loopback url) the report adds a Server health section — whether anything listens on the port, plugin-only keys in ov.conf that stop the server from starting, and `GET /ready`; everything else server-side (config validation, live embedding probe, native engine, disk) stays with `openviking-server doctor`.

Set `OPENVIKING_DEBUG=1` to write hook logs to `~/.openviking/logs/cc-hooks.log`.

## Plugin structure

Claude Code uses the plugin root's default locations; the files below `hosts/claude-code/` are what only Claude Code has.

```
agent-hook-plugin/
├── .claude-plugin/plugin.json   # Claude Code plugin manifest
├── hooks/hooks.json             # hook registrations + hooks module
├── .mcp.json                    # MCP server config (local stdio proxy)
├── commands/ov.md               # /openviking-memory:ov
├── mods/
│   ├── register.ts              # hooks module entry
│   ├── seal/                    # seal secrets
│   └── usage/                   # source cards
├── scripts/
│   ├── hook.mjs                 # every hook event: `hook.mjs <Event> claude-code`
│   ├── uri-guard.mjs            # PreToolUse guard
│   └── statusline.mjs           # statusline
├── servers/mcp-proxy.mjs        # stdio -> OpenViking /mcp bridge
├── skills/                      # bundled skills
├── lib/                         # shared library and the hook flow (lib/flow/)
└── hosts/claude-code/
    ├── host.mjs                 # how the flow reads Claude Code's payloads and transcripts
    ├── state.mjs                # statusline snapshots
    ├── compressor.mjs           # local recall compressor
    ├── ov-status.mjs            # /openviking-memory:ov report
    ├── ov-memory-doctor.mjs     # diagnostics (ov-memory-doctor skill)
    ├── skill-experience.mjs     # PostToolUse(Read) skill experience
    ├── seal-prompt.mjs          # setting helper for mods/seal
    ├── setup.mjs                # interactive ovcli.conf wizard
    └── STATUSLINE.md            # statusline glossary
```

## License

Apache-2.0, same as [OpenViking](https://github.com/volcengine/OpenViking).
