# Develop and maintain an agent plugin

This guide defines how to add and maintain OpenViking agent plugins that automatically read and write memory through lifecycle hooks and expose tools through MCP. It covers the host contract, the shared flow, configuration and credentials, MCP, testing, generation, and releases. A host is the client or runtime that runs the agent; the code also calls it a harness.

Every hook host runs the same flow. A new host describes its events, payloads, transcript format, and output envelope; it does not decide what is sent, when a session commits, or what the model is told. MCP-only and native-tool integrations can adopt the relevant rules without adding hooks that do not apply.

## Developing plugins with VibeCoding

When using VibeCoding or other AI-assisted development to add, fix, or refactor an agent plugin, **require the coding agent to read this entire document and follow it before making changes**. Provide the document path with the task. Require the agent to verify the host contract first, implement the change, and report results against the acceptance checklist. Generated code and passing tests do not replace protocol, recovery, and installed-artifact verification.

Copy the following prompt into your coding agent and replace the final line with the specific task:

```text
Before changing any OpenViking agent plugin, read and follow
docs/en/agent-integrations/18-plugin-development.md
(Chinese: docs/zh/agent-integrations/18-plugin-development.md).

Inspect examples/agent-hook-plugin/lib/flow/host.d.mts (the host contract),
lib/flow/index.mjs, and two existing hosts under
examples/agent-hook-plugin/hosts/. Verify the target host's events, payloads,
output schema, time limits, and installation contract.

A host module describes the host and decides nothing: no network, no queue,
no state files. Do not edit generated shared/ copies. Cover configuration,
hook/MCP identity, fixtures, installation, versioning, and bilingual
documentation as applicable.

Before finishing, use the guide's acceptance checklist and report what was
changed, what was verified, and any remaining limitations.

Task: <describe the plugin addition, fix, or maintenance change>
```

## 1. Design principles

1. **One authoritative implementation per behavior.** What happens at each stage is decided in `lib/flow/` and nowhere else. Hosts supply facts, not rules.
2. **Reuse must happen in the execution path.** Configuration stays consistent only when the same code resolves and consumes it. Copied files and agreements to keep implementations aligned do not prevent divergence.
3. **Derive lists when possible.** Configuration keys come from the schema; runtime files come from the import closure; the host table in the capability reference comes from the host modules.
4. **Preserve real differences as data.** A missing session end, a strict output schema, or a short exit budget is expressed through `events`, `facts`, `budgets`, and `emit`, not through branches on the host name in shared code.
5. **Distribution determines when files are generated.** A host that loads a repository directory needs that directory to be complete; a package builds its copies at pack time.
6. **Every abstraction should reduce maintenance points.** Before adding to the host contract, identify the host fact that cannot be expressed today. Do not design for hypothetical future agents.

These principles match the repository's [ownership and design guidelines](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING.md#ownership-and-design).

## 2. Establish the host contract before implementation

Before development, record the following in the host's README or design notes. Cite host documentation, versioned source, or observed behavior. Do not fill gaps with assumptions about another agent.

| Area | What must be established |
| --- | --- |
| Versions and platforms | Minimum host and Node.js versions, verified operating systems, and behavior on older versions |
| Installation | Native plugin, marketplace, configuration-file hooks, or npm extension; directories the host actually loads |
| Events | Availability of startup, prompt submission, turn completion, pre-compaction, session end, subagent, and tool-call events |
| Input | Whether stdin is JSON; session, cwd, transcript, and turn fields, including when they can be absent |
| Output | Context injection, allow/deny fields, empty response, exit codes, and whether unknown keys invalidate the whole output |
| Time limits | Units, upper bounds, whether the host clamps values, and whether timeouts kill one process or a process group |
| Message source | Transcript/rollout format, flush timing, stable message IDs, and tool call/result association |
| MCP | Configuration format, stdio support, root variables, startup cwd, inherited environment, and tool namespaces |
| Recovery | Identity and state retained across resume, clear, abnormal exit, compaction, and transcript truncation |

Mark each capability as verified, supported with a fallback, or unsupported, and state the verified version. Do not map `Stop` to session end or register an event the host never emits. A host with no usable session end leaves the last stretch of a session uncommitted unless the server's idle commit covers it; state that plainly (see [When each integration commits](./16-capability-reference.md#when-each-integration-commits)).

### 2.1 Choose the smallest suitable integration form

| Host constraints | Form | Reference |
| --- | --- | --- |
| Hooks run as commands configured in files | A directory under `examples/agent-hook-plugin/hosts/` | [agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) |
| Host SDK callbacks run inside the host's process | An extension package with its own `host.mjs` that calls `runStage` | [OpenCode](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin), [DSH](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin), [pi](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension) |
| The host registers native tools but has no MCP support | Use the official MCP client and register the server's `tools/list` as native host tools under an `openviking_` prefix | [pi](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension) |
| MCP is available, but automatic injection or complete session records are not | An MCP-only integration with documented limits | [Agent Plugins](https://github.com/volcengine/OpenViking/tree/main/agent-plugins) |

## 3. Plugin layout and the flow

### 3.1 Where the code lives

`examples/agent-hook-plugin/` is the one plugin directory for every hook-script host, including Claude Code and Codex.

```text
examples/agent-hook-plugin/
  .claude-plugin/plugin.json   Claude Code manifest; hooks/hooks.json, .mcp.json, mods/, commands/,
                               scripts/statusline.mjs are its default locations
  .codex-plugin/plugin.json    Codex manifest, pointing at hosts/codex/hooks.json and hosts/codex/.mcp.json
  lib/                         The shared library (config, credentials, HTTP, recall, queue, MCP core, ...)
  lib/flow/                    The flow: host contract, runStage, one module per stage
  scripts/hook.mjs             The one hook entry: hook.mjs <event> <host>
  scripts/uri-guard.mjs        The URI guard entry: uri-guard.mjs <host>
  servers/mcp-proxy.mjs        The one MCP proxy
  skills/                      The one copy of the skills
  hosts/index.mjs              loadHost(id): imports hosts/<id>/host.mjs on demand
  hosts/<host>/                host.mjs, decode.mjs, registration files, fixtures/, host-only extras

examples/memory-plugin-shared/ Installer (install.sh), sync.mjs, release tooling, and their tests
```

Host directories: `claude-code`, `codex`, `cursor`, `trae` (also serves `trae-cn`), `antigravity`, `zcode`, `kimicode`, `codebuddy`, `qoder`. Claude Code's directory also holds its statusline state, compressor, doctor, seal, and skill-experience code; Codex's holds its compressor, usage, and doctor code.

`hook.mjs` loads the host, resolves the config, decides whether a write event may detach (`mayDetach`), reads stdin, calls `runStage(host, event, payload)`, and prints `host.emit(...)`. There is no per-host dispatcher.

### 3.2 The host contract

[`lib/flow/host.d.mts`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/flow/host.d.mts) is the contract. A host module exports an object with:

| Field | Meaning |
| --- | --- |
| `id`, `sessionPrefix` | Client id; prefix of the OpenViking session id a host session is captured into |
| `events` | Host event name, as the hook entry passes it, to stage: `start`, `prompt`, `turnEnd`, `compactSoon`, `end`, `compacted`, `toolCall`, `toolResult` |
| `budgets` | Milliseconds every request of one event shares |
| `canDetach`, `syncEvents`, `detachEvents` | Whether write events may answer first and work in a detached process; which must stay in the hook; which always detach because the host gives too little time |
| `facts` | `profileAt` (`"start"` or `"firstPrompt"`), `compactionDropsProfile`, `compactionDropsTranscript`, `historySession`, `historyTool`, `transcript` (`false` or `"optional"`), `replayLimit` |
| `config` | `logFile` where it is not `<id>-hooks.log`; `rootKeyFallback` |
| `toolHints` | Replacement tools and example calls the URI guard names |
| `read(event, payload, io)` | What one event's payload says: session, cwd, prompt, transcript reference, `resumed`, `defer`, `child`, `skip`, `note`. Pure; `io.text` is the only way to look at a file |
| `locate(ref)` | The transcript's path; synchronous |
| `decode(input)` | Turns not yet acknowledged and `cursorAfter(count)`. Pure |
| `emit(event, output, payload)` | The value to print for `{ context }`, `{ deny }`, `{ notice }`, `{ input }`, or nothing |
| `tool`, `mcpSession`, `legacyCursor`, `present`, `compress` | Tool-call payload shape; session id in an MCP call's `_meta`; an earlier version's cursor; host-specific presentation; a local recall compressor |
| `observe(ctx, outcome)` | Told after every event what it did, for files the host keeps for its own UI. Cannot change the answer; a failure is only logged |

[`lib/flow/index.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/flow/index.mjs) exports `runStage(host, event, payload, { detached, cfg, outcome })`. It runs one event and returns the output; the caller hands it to `host.emit`. It skips an event with a logged reason for `read.skip`, `disabled`, `unconfigured`, `bypass`, and `subagent` (a child session with `skipSubagentSessions`). The stages are `start.mjs`, `prompt.mjs`, `capture.mjs` (turn end, compaction, session end), `commit.mjs`, `pointer.mjs`, and `tool.mjs`; their behaviour is documented in the [capability reference](./16-capability-reference.md#capture-and-commits).

### 3.3 What a host may and may not do

A host module does no network requests, touches no offline queue, and writes no state files: the flow owns them. `read` and `decode` are pure functions of their input, so they can be tested from fixtures. Cursors, locks, the offline queue under `~/.openviking/pending`, commits, and the session's auto-commit policy belong to the flow.

A host may keep files for its own interface (a statusline snapshot, a session binding) through `observe`, and may run a local compressor through `compress`. Neither may change what the flow sends or answers.

Shared code must not import a host directory or branch on a host id. When a host needs something the contract cannot say, extend `host.d.mts` with a fact, and implement its effect once in the flow.

### 3.4 In-process hosts

OpenCode, DSH, and pi run inside their harness's process. Each has its own `host.mjs` and calls `runStage(host, event, payload, { cfg, outcome })` from the library's `flow/index.mjs`, which it gets as a generated `shared/` copy (`lib/shared/` for OpenCode). It passes the config it resolved as `cfg`, and an `outcome` object when it needs to know what the event did. The host module still decides nothing; the extension code only turns SDK callbacks into events and payloads, and places the returned context where the harness expects it.

## 4. Configuration, credentials, and identity

### 4.1 Declare once and resolve consistently

Declare new behavior settings in `config-schema.mjs`: canonical name, type, default, range, owning capability, and applicable environment variable, legacy aliases, and workspace key. Resolve through `buildPluginConfig(harness, options)`; hook processes get it through `loadAgentHookConfig(host.id, cwd, host.config)`. Do not add a private plugin `config.json` that repeats shared defaults or maintain separate doctor/workspace key tables.

Behavior settings resolve in this order, highest precedence first:

1. `OPENVIKING_*` environment variables.
2. The machine's workspace registry.
3. Workspace `.openviking/config.local.json`.
4. Workspace `.openviking/config.json`.
5. `ovcli.conf` `plugin.<harness>`.
6. `ovcli.conf` `plugin`.
7. The legacy host section in `ov.conf`.
8. Schema defaults.

This `ovcli.conf` fragment supplies shared recall defaults and disables automatic capture for Codex; higher-priority environment or workspace settings can still override it:

```json
{
  "plugin": {
    "autoRecall": true,
    "codex": {
      "autoCapture": false
    }
  }
}
```

Register the canonical host ID in `HARNESS_KEYS` and verify hyphen/underscore aliases follow existing conventions. Within a layer, the canonical name wins over an alias.

Distinguish an absent value, explicit `false`, and a resolved value equal to the default. Fields marked `sendOnlyWhenConfigured` are sent only when actually configured, so client defaults do not override server defaults. Do not use `value || default` for fields that allow `false`, `0`, or an empty string.

Switches must govern actual behavior. Shared `isRecallEnabled()` and `isCaptureEnabled()` checks are the final guard. Advertise settings such as `mcpEnabled` for a host only when that host actually consumes them.

### 4.2 Credentials are not ordinary workspace settings

Resolve connections and identity through `resolveConnection()` in `credentials.mjs`, which `buildPluginConfig()` calls. The `auto`, `cli`, and `env` modes of `OPENVIKING_CREDENTIAL_SOURCE` select credential sources; they do not simply follow the behavior-setting precedence above. An MCP proxy exports `readProxyConfig(env)`, resolves through the same loader as its hooks, and maps the result with `toMcpProxyConfig()`; it never picks fields by hand or calls `credentials.mjs` itself. If the host hands MCP servers an allowlisted environment, the allowlist must cover `MCP_PROXY_ENV_VARS`; if it hands them a closed one, forward the resolved connection with `forwardConnectionEnv()`. Add every new proxy to `mcp-hook-parity.test.mjs`, which fails until it has a row.

Workspace files must not contain forbidden connection or credential fields such as URLs, API keys, and user credentials, and must not interpolate environment variables. [`workspace-config.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/workspace-config.mjs) owns this rule; do not add a separate allowlist per host. Installers must not write resolved API keys into `.mcp.json` or replace the user's selected cloud connection.

Build request headers with `buildOvHeaders()`. Send API keys as `Authorization: Bearer`, without a duplicate `X-API-Key`; send account/user headers only when the resolved `sendIdentityHeaders` is true. Preserve `User-Agent` and error `traceId` values to identify the running version and trace requests. Status lines and diagnostic probes must not reimplement authentication.

In diagnostics, `credentialSource` identifies the resolution mode, while `apiKeySource` identifies the key's actual origin. `rootKeyFallback` (set in a host's `config`) exists for older installations; a new host must not copy it just because a reference host enables it.

### 4.3 Separate session identity from peer identity

A native session ID identifies a conversation; a peer identifies project memory ownership. The flow derives the OpenViking session from `sessionPrefix` and the host's session id (or `read.session` for a host that derives it its own way), and gives a child session (`read.child`) state, a lock, and an OpenViking session of its own. Prefixes are data compatibility contracts; explain how old state remains readable when changing them, and use `legacyCursor` so an upgrade does not resend what an earlier version captured.

Use shared peer resolution. The default derives identity from Git; ordinary non-Git directories receive no automatic peer, while marker files can specify one. The flow reloads workspace settings using the payload's actual cwd, not the plugin installation directory.

A persistent MCP proxy cannot infer the active project from its startup cwd. Use `resolveMcpActorPeerId()`: broad reads omit the actor peer header; actor-scoped reads require an explicit peer. Peer scopes organize memory and retrieval within an authenticated user; do not present them as authorization isolation between different users.

## 5. Adding a host

1. **Create `examples/agent-hook-plugin/hosts/<id>/`.** `hosts/index.mjs` loads `./<id>/host.mjs` on demand; a client id served by another directory (as `trae-cn` by `trae`) goes in its `DIRECTORY` map, and the module exports `hosts: { <id>: ... }`.
2. **Write `host.mjs`.** Map the host's event names to stages, set `budgets` from the host's verified timeouts, set `canDetach` / `syncEvents` / `detachEvents` from verified process behavior, and declare `facts`. `read` extracts fields from the payload; `emit` builds exactly the envelope the host accepts (a strict schema such as ZCode's must never see an unknown key).
3. **Write `decode.mjs`.** Turn the transcript (or, for `transcript: false`, the payload and `notes`) into turns not yet acknowledged and `cursorAfter`. Preserve tool calls and results with their ids; use `parseJsonl` and `resumeTurns` from `lib/flow/turns.mjs` where they fit.
4. **Add the registration files.** The host's hook configuration calls `node <plugin root>/scripts/hook.mjs <event> <client id>` for each event in `events`, plus `scripts/uri-guard.mjs <client id>` where the host has a pre-tool event; add the host's manifest or integration file and MCP configuration pointing at `servers/mcp-proxy.mjs`.
5. **Add `fixtures/`.** Record real payloads and transcripts as cases (section 9).
6. **Register it.** Add the host to the installer, `sync.mjs` `SKILL_TARGETS` if the host bundles skills from its own directory, `HOOK_HOSTS` in `host-table.mjs`, and the docs.

Do not register an empty handler for an event the host does not have.

## 6. MCP and model-visible tools

Hooks automate lifecycle behavior; MCP exposes tools the model chooses to call. Both connect through the same configuration and identity. Document what is injected automatically, which operations require model calls, and which MCP tools remain usable when automatic behavior is disabled.

Hosts with stdio MCP support use the one proxy, `servers/mcp-proxy.mjs`: it resolves configuration, shapes it with `buildMcpProxyConfig()`, and passes it to `createOpenVikingMcpProxy()`. A host whose MCP calls name the host session in `_meta` supplies `mcpSession`, so `session_history` defaults to the current session. Treat server `tools/list` and `tools/call` as authoritative instead of maintaining a second tool catalog. Necessary local tools use the shared `localToolProvider` interface and must not shadow server tools.

For each new host, verify:

- Startup from the host's actual working directory, including relative paths, root variables, and environment allowlists.
- `initialize` negotiation, notifications without ordinary responses, JSON/SSE handling, concurrent response IDs, and protocol-clean stdout.
- Expected hook and MCP URLs and identities across environment configuration, default/custom files, and profile switching. Observe request targets and headers rather than only configuration contents.
- Tool names in diagnostics and documentation match what the host exposes.

## 7. URI guards, skills, and diagnostics

`viking://` is a virtual URI. A local file tool whose path argument is a `viking://` URI cannot succeed, so where the host has a pre-tool event, `scripts/uri-guard.mjs <host>` denies it. A shell command that carries a `viking://` URI may be using it as data, so it runs with a notice through the host's model-visible channel. The rules live in `lib/uri-guard.mjs` and `lib/flow/tool.mjs`; a host supplies only `tool`, `toolHints`, and the envelopes in `emit`. Without the required event, document the limitation and guide models through a skill.

Shared skill sources live in [`examples/skills/`](https://github.com/volcengine/OpenViking/tree/main/examples/skills/) and are shipped through `SKILL_TARGETS` in `sync.mjs`. Do not edit the same guidance separately in several copies. Skills must describe tools that can actually be called. Generated skill files must not receive a banner before their YAML frontmatter.

Skills stored in OpenViking are created, installed, shared, and replaced through the server's `add_skill` MCP tool. Do not reimplement installation in a host. The URI guard (`isSkillUri()`) points a denied local write or edit on a skill URI to `add_skill`.

Use `runDoctor(hostSpec)` for diagnostics. Hosts supply installation paths, manifests, hook registrations, and state checks; `doctor-core.mjs` owns common configuration, credential, network, and output handling. Logs retain host, stage, session association, latency, and trace IDs. They must not log API keys, full Authorization headers, or whole user transcripts by default.

## 8. Generation and installation

### 8.1 How `sync.mjs` copies the library

The library lives in `examples/agent-hook-plugin/lib/`. Hook hosts import it in place. Plugins elsewhere get a generated copy from [`sync.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs):

- Each entry in `TARGETS` names only where a plugin's own code lives, where its copies go, and whether the copies are `committed`. The file set is the transitive closure of what that code imports (static imports, literal dynamic imports, and JSON files read through `new URL("./x.json", import.meta.url)`), plus `.d.mts` declarations when the plugin's TypeScript imports the copies.
- Copies carry the header `// GENERATED FROM examples/agent-hook-plugin/lib. DO NOT EDIT.`; JSON data files are copied byte-identical.
- `committed: true` is for plugins a host installs by pointing at a directory in this repository (Agent Plugins, OpenClaw); a bot regenerates them on main. `committed: false` is for plugins built into a package or tarball (OpenCode, DSH, pi), which generate their copies at pack time.
- `DATA_TARGETS` copies lib data files outside the plugins (the gateway's `server-features.json`); `SKILL_TARGETS` copies skills verbatim.
- The script reports a copy nothing imports any more (`stale`) and a lib module no target imports (`unused`).

After changing the library, run:

```bash
node examples/memory-plugin-shared/sync.mjs
```

Keep imports discoverable; do not hide them behind constructed strings. Do not rely on absolute development paths, symlinks, or `NODE_PATH`. Pull requests must include current committed copies.

### 8.2 Installation and removal contracts

Reuse [`install.sh`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/install.sh). Keep JSON/JSONC merge logic in [`lib/install/`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared/lib/install/). Installers should:

1. Parse, validate, and prepare required files before changing user configuration. Invalid JSON/JSONC must produce an error and preserve the original file.
2. Be idempotent, without duplicate hook/MCP entries. Preserve other plugins, user settings, and meaningful comments.
3. Handle spaces in paths, hosts without variable expansion, and custom configuration paths.
4. Remove only entries owned by this plugin, including URI guards. Preserve credentials, memory, and session data.
5. Keep shared runtime files available to other clients after removing one client.

Validate archives with [`stage-memory-plugin-marketplace.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/stage-memory-plugin-marketplace.sh) and [`check-marketplace-archive.mjs`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-marketplace-archive.mjs). Before release, extract the final archive or tarball and smoke-test entrypoints and installation.

## 9. Testing

The flow is tested once, in [`examples/memory-plugin-shared/flow.test.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/flow.test.mjs), against a minimal host and a mock server. Behaviour that every host shares (start, recall, capture, cursors, commits, queue, pointer, tool calls) belongs there, not in per-host tests.

[`hosts.test.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/hosts.test.mjs) checks every directory under `agent-hook-plugin/hosts/` that has a `host.mjs`: the contract its module meets, and the cases in its `fixtures/`. A case is one JSON file with a `what` and one of:

| Case | Input | Expected |
| --- | --- | --- |
| `decode` | `{ transcript?, cursor?, notes?, payload?, cfg? }` | `messages`: the session messages the flow sends; optionally `cursor` once all are settled |
| `read` | `{ event, payload, transcript? }` | `expect`: the fields named |
| `tool` | `{ payload }` | `expect`: what the host prints |
| `emit` | `{ event, output, payload? }` | `expect`: what the host prints |
| `mcp` | `{ meta }` | `expect`: the OpenViking session an MCP call's `_meta` names |

`transcript` names a file beside the case. `UPDATE_FIXTURES=1` rewrites what each case expects from what the host does now; read the diff before committing it. `OV_TEST_HOST=<directory>` runs one host's cases only.

```bash
OV_TEST_HOST=zcode node --test examples/memory-plugin-shared/hosts.test.mjs
```

The host table in [16-capability-reference.md](./16-capability-reference.md) (between `<!-- host-table:start -->` and `<!-- host-table:end -->`) is generated by [`host-table.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/host-table.mjs) from the host modules, and `host-table.test.mjs` fails when either language's page differs. After changing a host's events or facts, paste the output of `node examples/memory-plugin-shared/host-table.mjs --lang <lang>` between the markers. The in-process hosts import their generated `shared/` copy, so run `sync.mjs` first on a fresh checkout.

Shared library tests (`plugin-config`, `credentials`, `mcp-proxy-*`, `pending-queue`, `recall-core`, `uri-guard`, ...) sit beside them in `examples/memory-plugin-shared/`. Put common helpers in [`testing/support.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/testing/support.mjs). Use private temporary directories, isolated configuration and pending paths, and dynamic ports. Installer and marketplace tests generate files; run them serially, as CI does:

```bash
node --test --test-concurrency=1 \
  examples/memory-plugin-shared/install-agent-hooks.test.mjs \
  examples/memory-plugin-shared/release-marketplace.test.mjs
```

Use [PR CI](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml) for complete commands. Passing Node.js tests does not establish that Windows detach behavior, path quoting, or process cleanup works; state untested platforms. Acceptance evidence should identify the artifact, host version, scenario, and observation.

## 10. Maintenance, releases, and documentation

### 10.1 Follow through by change type

| Change | Required follow-up |
| --- | --- |
| Flow or library fix | Edit `lib/`, extend `flow.test.mjs` or the library test, run `sync.mjs`, bump versions |
| Host payload or event update | Update `host.mjs` / `decode.mjs`, add a fixture from a real payload, regenerate the host table if events or facts changed |
| New setting | Schema, actual consumer, configured semantics, diagnostic source, user documentation |
| Directory or package rename | Imports, manifests, installer, marketplace, CI path filters, skills, links, uninstall identification |
| Documentation only | Accuracy and working links |

A shared defect exposed by one host still belongs in the flow; a payload change unique to one host belongs in that host's directory.

### 10.2 Versions and releases

A new plugin must join the version checks and release workflow, not merely add a directory. [`check-plugin-version-bumps.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-plugin-version-bumps.sh) counts shared `lib/` changes for every plugin it registers; check registration whenever adding or changing a target. Keep host manifests, `package.json`, lockfiles, installation manifests, and installer version checks consistent. `User-Agent` and doctor output should report the actual artifact version.

npm/archive builds generate shared files from clean source before packaging. The [`plugin-npm-release.yml`](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/plugin-npm-release.yml) matrix covers DSH and OpenCode; check other packages' release paths individually.

```bash
bash .github/scripts/check-plugin-version-bumps.sh origin/main
git diff --check
git status --short --untracked-files=normal -- examples agent-plugins
```

The version check compares committed `base...HEAD`; run it against the final commit.

### 10.3 Documentation is part of the interface

A plugin README should include supported capabilities and versions, the quickest installation method, credential sources, automatic behavior versus MCP, core switches, limitations, upgrade/removal, and diagnostics. Shared behaviour is documented once in the [capability reference](./16-capability-reference.md); host pages cover differences. User guides and this standard live in `docs/{en,zh}/agent-integrations/`; maintain English and Chinese pages together.

## 11. Pre-merge checklist

- [ ] Host versions, input/output formats, time limits, message sources, and end semantics have evidence; unsupported capabilities are explicit.
- [ ] The host module only describes the host: no network, queue, or state files; `read` and `decode` are pure.
- [ ] Every event in `events` is registered with `scripts/hook.mjs <event> <host>`, and nothing is registered that the host never emits.
- [ ] Fixtures cover `read`, `decode`, `emit`, and tool calls from real payloads; `hosts.test.mjs` passes for the host.
- [ ] Configuration is declared once, switches affect execution, and actual hook/MCP connections and identity are verified.
- [ ] The host table in the capability reference matches `host-table.mjs` in both languages.
- [ ] `sync.mjs` output, installation, removal, archives, and release triggers are complete; the final artifact was tested.
- [ ] Versions and related manifests agree; untested platforms and scenarios are documented.
- [ ] English and Chinese documentation describe the final behavior.

## 12. Maintainer reading guide

- Shared behavior across integrations: [Capability Reference](./16-capability-reference.md).
- The host contract and the flow: [host.d.mts](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/flow/host.d.mts), [flow/](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/lib/flow).
- Hook hosts: [hosts/](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts), [agent-hook-plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README.md).
- Generation and tests: [sync.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs), [hosts.test.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/hosts.test.mjs), [flow.test.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/flow.test.mjs), [pr.yml](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml).
