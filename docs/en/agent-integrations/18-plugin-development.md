# Hook + MCP Agent Plugin Development and Maintenance Standard

This guide defines how to add and maintain OpenViking agent plugins that automatically read and write memory through lifecycle hooks and expose tools through MCP. It covers module responsibilities, protocols, state handling, installation, testing, and releases. A host is the client or runtime that runs the agent; the code also calls it a harness.

A new host should primarily add its event, message-format, context-injection, and installation differences. Reuse shared implementations for configuration resolution, authentication, recall, capture filtering, HTTP requests, and offline retries. Claude Code, Codex, and other plugins are useful references, but verify the target host's actual contract. MCP-only and native-tool integrations can adopt relevant rules without adding hooks that do not apply.

## Developing plugins with VibeCoding

When using VibeCoding or other AI-assisted development to add, fix, or refactor an agent plugin, **require the coding agent to read this entire document and follow it before making changes**. Provide the document path with the task. Require the agent to verify the host contract first, implement the change, and report results against the acceptance checklist. Generated code and passing tests do not replace protocol, recovery, and installed-artifact verification.

Start with the [Claude Code](./02-claude-code.md) and [Codex](./04-codex.md) plugins to understand how shared capabilities connect to native hooks and MCP. For hosts configured through hook files, refer to `agent-hook-plugin`; for persistent extensions, refer to OpenCode, DSH, and other plugins. Follow their separation of responsibilities and verified behavior. Do not ask the agent to copy an entire plugin or force every host to use the same events.

Copy the following prompt into your coding agent and replace the final line with the specific task:

```text
Before changing any OpenViking agent plugin, read and follow
docs/en/agent-integrations/18-plugin-development.md
(Chinese: docs/zh/agent-integrations/18-plugin-development.md).

Inspect examples/memory-plugin-shared/lib/ and the Claude Code and Codex
plugins. Also inspect agent-hook-plugin, OpenCode, DSH, or another existing
integration when its host model matches the task. Verify the target host's
events, payloads, output schema, time limits, and installation contract.

Keep shared behavior in the shared modules and host differences in the
adapter. Do not copy a whole plugin or edit generated shared files. Cover
configuration, hook/MCP identity, capture acknowledgements, commit recovery,
installation, versioning, and bilingual documentation as applicable.

Before finishing, use the guide's acceptance checklist and report what was
changed, what was verified, and any remaining limitations.

Task: <describe the plugin addition, fix, or maintenance change>
```

## 1. Design principles

A shared library does not, by itself, make plugins share behavior. Configuration stays consistent only when the same execution path resolves and consumes it. Interpreting switches separately in each host leads to different names, defaults, and settings that are accepted but do nothing. Distribution files should likewise be derived from dependencies, so code that passes in the source tree does not fail after installation because an imported file is missing.

The requirements are:

1. **One authoritative implementation per behavior.** Shared modules own rules; adapters supply host facts. Do not copy a slightly different version of a rule into an adapter.
2. **Reuse must happen in the execution path.** Call `buildPluginConfig()`, `buildRecallBlockDetailed()`, or the shared sender. Copied files, identically named exports, and agreements to keep implementations aligned do not prevent divergence.
3. **Derive lists when possible.** Configuration keys, diagnostic keys, and workspace mappings come from the schema; runtime files come from the import closure; required archive contents come from entrypoints and manifests.
4. **Preserve real differences.** Claude Code's subagent events, Codex's exit recovery, and ZCode's strict output format each have a reason. Share common capabilities without forcing identical lifecycles.
5. **Distribution determines when files are generated.** A host that loads a repository directory needs that directory to be complete. A host that installs a built artifact needs dependencies generated before packaging. Keep installations complete and commit only the generated files that distribution requires.
6. **Every abstraction should reduce maintenance points.** A new interface should let a future fix touch fewer places. Reconsider it if it only adds forwarding layers, boolean options, or another configuration table.

These principles match the repository's [ownership and design guidelines](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING.md#ownership-and-design). A new maintainer should be able to follow calls, locate the rule, explain a failure, and deliver a fix. Line and file counts are outcomes, not the goal.

## 2. Establish the host contract before implementation

Before development, record the following in the plugin README or, where needed, its design document. Cite host documentation, versioned source, or observed behavior. Do not fill gaps with assumptions about another agent.

| Area | What must be established |
| --- | --- |
| Versions and platforms | Minimum host and Node.js versions, verified operating systems, and behavior on older versions |
| Installation | Native plugin, marketplace, configuration-file hooks, or npm extension; directories the host actually loads |
| Events | Availability of startup, prompt submission, turn completion, pre-compaction, session end, and subagent events |
| Input | Whether stdin is JSON; session, cwd, transcript, and turn fields, including when they can be absent |
| Output | Context injection, allow/deny fields, empty response, exit codes, and whether unknown keys invalidate the whole output |
| Time limits | Units, upper bounds, whether the host clamps values, and whether timeouts kill one process or a process group |
| Message source | Transcript/rollout format, flush timing, stable message IDs, and tool call/result association |
| Processes | Hook process reuse, detached worker survival, and concurrency across windows and sessions |
| MCP | Configuration format, stdio support, root variables, startup cwd, inherited environment, and tool namespaces |
| Recovery | Identity and state retained across resume, clear, abnormal exit, compaction, and transcript truncation |

Mark each capability as verified, supported with a fallback, or unsupported, and state the verified version. Do not describe `Stop` as session end or claim a capability by registering an event the host never emits. The minimum server version follows the APIs and URI capabilities actually used. For example, shared recall currently requires `viking://~` support; a fallback for one older API does not imply compatibility with every older server.

### 2.1 Choose the smallest suitable integration form

| Host constraints | Preferred form | Reference |
| --- | --- | --- |
| Hooks and MCP are installed through configuration files; the common dispatcher can express the lifecycle | Add an adapter and host configuration under `agent-hook-plugin/hosts/` | [agent-hook-plugin](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README.md) |
| A native plugin needs its own manifest, directory, and lifecycle entrypoints | A separate plugin directory whose entrypoints call the shared runtime | [Claude Code](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md), [Codex](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) |
| Host SDK callbacks require persistent state or dispose/idle callbacks | A host extension package using shared capabilities and explicit session scheduling | [OpenCode](https://github.com/volcengine/OpenViking/blob/main/examples/opencode-plugin/README.md), [DSH](https://github.com/volcengine/OpenViking/blob/main/examples/dsh-memory-plugin/README.md) |
| MCP is available, but automatic injection or complete session records are not | An MCP-only integration with documented limits | [Agent Plugins](https://github.com/volcengine/OpenViking/blob/main/agent-plugins/README.md) |

A new host name does not justify copying the Claude Code or Codex directory. Conversely, a host with a distinct session state machine should not be forced into the common dispatcher through accumulating `isFoo` or `specialStop` flags.

## 3. Module responsibilities and dependency direction

```text
Host events / transcript                  Host MCP client
          ↓                                     ↓ stdio
Event, message, and output adapter          Thin MCP entrypoint
          ↓                                     ↓
runHookStage + host lifecycle               buildMcpProxyConfig
          ↓                                createOpenVikingMcpProxy
Shared recall / capture / session                 ↓
          ↓ createOvHttp                    Shared MCP transport
          └────────── buildOvHeaders ─────────────┘
                               ↓
                         OpenViking Server

buildPluginConfig / credentials configure both paths and their identity
sync / install / pack deliver the complete dependency graph
```

Authoritative capability sources live in [`examples/memory-plugin-shared/lib/`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared/lib/). The following table helps locate rules; it is not a template to recreate inside every plugin.

| Responsibility | Authoritative module | What a host may supply |
| --- | --- | --- |
| Configuration declarations, defaults, aliases, ranges | [config-schema.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/config-schema.mjs) | Justified host-specific defaults, still declared in the schema |
| Layered resolution and the complete config object | [plugin-config.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/plugin-config.mjs) | Harness ID, manifest, log filename, native host parameters |
| Credentials and authentication mode | [credentials.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/credentials.mjs) | Existing compatibility requirements; no separate fallback chain |
| Workspace and peer identity | [workspace-peer.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/workspace-peer.mjs), [workspace-identity.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/workspace-identity.mjs) | Actual session cwd and an explicitly provided peer |
| Hook initialization, bypass, single output | [agent-hook-runtime.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/agent-hook-runtime.mjs) | Input reader, session ID resolver, enablement predicate, output envelope |
| HTTP headers, timeouts, error results | [ov-http.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/ov-http.mjs) | Request path and body, time budget, current actor peer |
| Recall and context construction | [recall-core.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/recall-core.mjs), [profile-inject.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/profile-inject.mjs) | Query, session identity, local compressor callback, host presentation |
| Message sanitation, roles, structured content | [capture-utils.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/capture-utils.mjs), [input-filters.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/input-filters.mjs) | Transcript decoding and native tool event normalization |
| Batch sending, offline replay, retry classification | [batch-send.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/batch-send.mjs), [pending-queue.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/pending-queue.mjs), [retryable.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/retryable.mjs) | When to call them and how acknowledgements advance the host cursor |
| Background writes | [async-writer.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/async-writer.mjs) | Host-supported detach timing and recovery measures |
| MCP configuration and protocol | [mcp-proxy-config.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/mcp-proxy-config.mjs), [mcp-proxy-core.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/mcp-proxy-core.mjs) | Configuration projection, logger factory, necessary local tools |
| Virtual URI checks and diagnostics | [uri-guard.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/uri-guard.mjs), [doctor-core.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/doctor-core.mjs) | Tool names, denial format, host installation/state checks |

Dependencies must point from host adapters to shared capabilities. Shared code must not import a host directory. Pass a small, explicit callback when a capability needs a host action. Do not introduce a plugin container, service locator, or inheritance hierarchy for a single file read. Shared runtime modules must not depend on installers, tests, or user interfaces.

### 3.1 How thin should an adapter be?

Thin means owning only host differences; it is not a line-count limit. For example, [cc-transcript.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/scripts/cc-transcript.mjs) converts Claude message blocks and nested `tool_result` content. [Codex capture-utils.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/scripts/capture-utils.mjs) also expands nested tool activity and removes duplicate representations of the same call, so it can be longer. Both should delegate common content processing to shared code.

For a new thin host, prefer the existing [`HOSTS`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/index.mjs) registry and dispatcher interfaces: `stages`, `envelope`, `prompt`, `normalizeInput`, `capture`, and `guard`. Before adding an interface, identify the host fact that cannot be expressed. Do not design an adapter DSL for every hypothetical future agent.

The existing `hosts/cursor.mjs`, `hosts/trae.mjs`, and `hosts/zcode.mjs` demonstrate the separation: event mappings are data, message conversion fits pure functions, and asynchronous capture needs explicit callbacks. Do not combine them into a configuration-object builder that also performs network requests.

## 4. Configuration, credentials, and identity

### 4.1 Declare once and resolve consistently

Declare new behavior settings in `config-schema.mjs`: canonical name, type, default, range, owning capability, and applicable environment variable, legacy aliases, and workspace key. Resolve through `buildPluginConfig(harness, options)`; adapters only project fields needed by their host. Do not add a private plugin `config.json` that repeats shared defaults or maintain separate doctor/workspace key tables.

Behavior settings resolve in this order, highest precedence first:

1. `OPENVIKING_*` environment variables.
2. The machine's workspace registry.
3. Workspace `.openviking/config.local.json`.
4. Workspace `.openviking/config.json`.
5. `ovcli.conf` `plugin.<harness>`.
6. `ovcli.conf` `plugin`.
7. The legacy host section in `ov.conf`.
8. Schema defaults.

Pass host SDK inputs through explicit shared builder parameters and document their precedence. DSH host settings currently participate in the compatibility layer, and `hostInput` can pin connection or peer values. These specific interfaces do not authorize arbitrary overrides of the resolved configuration.

Keep examples minimal. This `ovcli.conf` fragment supplies shared recall defaults and disables automatic capture for Codex; higher-priority environment or workspace settings can still override it:

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

Register the canonical host ID in `HARNESS_KEYS` and verify hyphen/underscore aliases follow existing conventions. The shared resolver decides conflicts between canonical names and aliases; within a layer, the canonical name wins. Do not add a second, conflicting alias conversion in the adapter.

Distinguish an absent value, explicit `false`, and a resolved value equal to the default. Fields marked `sendOnlyWhenConfigured` are sent only when actually configured, so client defaults do not override server defaults. Do not use `value || default` for fields that allow `false`, `0`, or an empty string; validity follows the field's semantics.

Switches must govern actual behavior. Disabling recall prevents automatic recall requests; disabling capture prevents new automatic capture and its associated commits for the session. Decide and verify separately whether previously queued writes are still recovered. Shared `isRecallEnabled()` and `isCaptureEnabled()` checks provide the final guard; adapters may exit early but must not be the only place enforcing switches. Advertise settings such as `mcpEnabled` for a host only when that host actually consumes them.

### 4.2 Credentials are not ordinary workspace settings

Resolve connections and identity through `credentials.mjs` and `buildPluginConfig()`. The `auto`, `cli`, and `env` modes of `OPENVIKING_CREDENTIAL_SOURCE` select credential sources; they do not simply follow the behavior-setting precedence above. Verify custom configuration paths, `plugin.<harness>` overrides, and hook/MCP behavior after `ov config switch`. Comparing a few default loader fields is insufficient.

Workspace files must not contain forbidden connection or credential fields such as URLs, API keys, and user credentials, and must not interpolate environment variables. [`workspace-config.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/workspace-config.mjs) owns this rule; do not add a separate allowlist per host. Installers must not write resolved API keys into `.mcp.json` or replace the user's selected cloud connection.

Build request headers with `buildOvHeaders()`. Send API keys as `Authorization: Bearer`, without a duplicate `X-API-Key`; send account/user headers only when the resolved `sendIdentityHeaders` is true. Preserve `User-Agent` and error `traceId` values to identify the running version and trace requests. Health checks, status lines, and diagnostic probes must not reimplement authentication.

In diagnostics, `credentialSource` identifies the resolution mode, while `apiKeySource` identifies the key's actual origin. Keep these meanings distinct. `rootKeyFallback` exists for older installations; a new host must not copy that option unconditionally just because a reference plugin enables it.

### 4.3 Separate session identity from peer identity

A native session ID identifies a conversation; a peer identifies project memory ownership. They are not interchangeable. Writes use a stable native session ID and an explicit host prefix. Separate windows, sessions in the same cwd, and main/subagents must not accidentally share a capture cursor. Prefixes and session ID algorithms are data compatibility contracts; explain how old state remains readable when changing them.

Use shared peer resolution. The current default derives identity from Git; ordinary non-Git directories receive no automatic peer, while marker files can specify one. See the [shared library's workspace notes](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#workspace-peers) for worktrees, subdirectories, and forks. Hooks must reload workspace settings using the payload's actual cwd, not the plugin installation directory.

A persistent MCP proxy cannot infer the active project from its startup cwd. Use `resolveMcpActorPeerId()`: broad reads omit the actor peer header; actor-scoped reads require an explicit peer. The shared implementation currently warns and falls back to broad reads when that peer is absent. Document this behavior accurately. Peer scopes organize memory and retrieval within an authenticated user; do not present them as authorization isolation between different users.

## 5. Hook lifecycle and reliable writes

### 5.1 Map common stages to host events

| Stage | Shared responsibility | Claude Code today | Codex today |
| --- | --- | --- | --- |
| Session start | Profile injection and necessary recovery | `SessionStart` | `SessionStart`, distinguishing startup, clear, and resume |
| Prompt submission | Filter the query, recall, inject context | `UserPromptSubmit` | `UserPromptSubmit` |
| Turn completion | Catch up new messages from a reliable source and persist progress | `Stop`, plus threshold-based commits | `Stop`, normally appending rather than ending the session |
| Before compaction | Ensure preceding messages were written, then commit as appropriate | `PreCompact` commits existing messages; this entrypoint does not catch up the transcript | `PreCompact` catches up the transcript and commits |
| Session end | Finish outstanding writes and commit | `SessionEnd` commits existing messages; this entrypoint does not catch up the transcript | `SessionEnd` catches up and commits in a worker, with startup recovery retained |
| Subagents | Preserve identity and parent relationships without duplicates | `SubagentStart`, `SubagentStop` | Neither event is registered in the current hook manifest |
| Local tool checks | Prevent treating virtual URIs as local paths | `PreToolUse` URI guard | No URI guard is registered in the current hook manifest |

Use [Claude Code hooks.json](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/hooks/hooks.json) and [Codex hooks.json](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/hooks/hooks.json) as the event registration references. Identical event names do not guarantee identical payloads or outputs. A host without an end event must choose and document an alternative commit point, such as ZCode's Stop commits, rather than register an unreachable SessionEnd handler.

### 5.2 Hook entrypoint responsibilities

Use `runHookStage()` for stdin parsing, configuration reload against the session cwd, enablement and bypass gates, and a single response. Supply a resolver for host-specific session fields instead of disabling bypass to accommodate a field name. Output must match the host's current event protocol: `decision: "approve"` is not universal, and Cursor's `additional_context` is not interchangeable with `hookSpecificOutput.additionalContext`.

Keep stdout reserved for the host's protocol. Write logs to stderr or the shared log file. No results, disabled features, unavailable configuration, and network errors should produce a valid empty result for that event, so a memory service failure does not block normal interaction. An explicit URI guard denial must retain its denial semantics; generic error handling must not turn it into approval.

Ordinary module imports must not read stdin, spawn processes, access the network, or exit the process. Put those effects in entrypoints or explicitly called functions. Shared functions must not call `process.exit()` for convenience. Reusable entrypoints need a testable main function and an explicit startup condition.

### 5.3 Recall and profile injection

Use `buildProfileBlock()` for profiles and `buildRecallBlock()` / `buildRecallBlockDetailed()` for per-turn recall. Hosts may supply compressors, presentation, and statistics, but must not reimplement retrieval targets, ranking, token budgets, or server compatibility fallbacks. A status line should consume shared results and the final injected content, rather than issue another recall to calculate counts.

Automatic recall must carry the correct session and peer, and honor input filters, bypass, and switches. Keep empty results empty instead of injecting a server's no-relevant-memory sentinel. Compression failure may fall back to the existing uncompressed result, but must not invent a digest. Compressed `viking://` URIs must remain readable. Capture must distinguish the user's input, recalled context, and host wrappers so injected old memories are not captured again.

When using a host CLI for compression, isolate that auxiliary invocation from automatic memory hooks, bound its execution time, and reuse the existing compressor interface. Do not launch an agent that recursively invokes the same recall/capture hooks. Model selection and invocation belong to the host adapter; common compression result handling belongs to shared code.

### 5.4 Capture data and acknowledgement rules

Prefer a complete host transcript or rollout. Use stdin as a fallback only when the complete source is verified to be unavailable. A Stop payload may lack the user input; do not manufacture a complete turn from the last assistant response. An unreadable file and no new messages must be distinguishable results.

Decode host records into the shared capture model before applying shared filtering and sending. Preserve text separately from structured tool content. Tool names, call IDs, inputs, outputs, status, and available turn IDs must come from actual records. Do not record a native event, MCP result, and nested representation of one tool call as several calls. Do not retain only final assistant prose and lose tool activity. Text filtering must not discard valid records that contain only tools.

Use `capture-utils.mjs` and `input-filters.mjs` for filtering and truncation. A text summary of tool output and structured `tool_output` are different fields. The current implementation delegates large-output externalization to the server while retaining a client limit for pathological payloads. Shortening text summaries must not also remove structured evidence.

Write in this order:

```text
Read new records → decode and filter → send in order
                                      ├─ server acknowledgement: advance sent progress
                                      ├─ durable pending entry: handoff progress may advance; not yet delivered
                                      └─ send and enqueue both fail: keep progress for recovery
```

Preserve these invariants:

- Advance cursors only through a contiguous acknowledged prefix. The shared sender returns `sent` and `queued`. An adapter that advances by both must establish that queued entries are durable and retain responsibility for replay. Attempts are not successes.
- Prefer stable message IDs, turn IDs, or transcript positions for deduplication. Identical text can belong to two legitimate turns; do not permanently deduplicate solely by text hash.
- Use session locking when multiple processes update the same state, and write state through a temporary file and atomic rename. Lock wait limits, stale-lock recovery, and task duration must agree. Long tasks need demonstrable ownership and liveness, not a stale TTL shorter than their execution.
- Define how replay handles messages sent successfully before a failed state save. Do not claim exactly-once delivery without server-side idempotency guarantees.
- Transcript shrinkage, resume, and compaction must not leave the cursor permanently beyond new records. Explain how these changes are detected and recovered.
- Give subagent capture a clear owner. If the main transcript already contains subagent records, a separate subagent hook must not import them again.

### 5.5 Commits, retries, and exit

A commit requests server archiving and processing; it does not mean long-term memory extraction has finished. Report HTTP success, task acceptance, archive completion, and extraction completion separately. A `task_id` is not proof that all processing is complete.

Catch up a session's messages before committing. On partial failure, retain its active session and unfinished state. Implementations that hand messages to the pending queue must also prove a commit cannot overtake unreplayed messages. **Using the pending queue does not automatically provide transactional ordering between messages and commits.** Verify complete failure sequences and choose deferred commits or replay with enforced ordering as appropriate for the host.

Shared retry classification currently permits retries for network failures, 408, 429, and 5xx. A 409 is retryable only when `error.details.retryable` explicitly says so. Do not repeatedly enqueue 401/403 responses or ordinary parameter errors. `sendSessionMessages()` handles batches of at most 100 messages and falls back to serial sends when the batch endpoint returns 404/405. Hosts must not maintain separate status-code lists or batching loops.

Preserve the original payload when enqueueing, including commit parameters such as `keep_recent_count`; replay must execute the original operation. A failed commit must not clear the active ID, end marker, or missing messages prematurely. Pending queues have retry, replay-batch, and TTL limits. They provide bounded recovery, not indefinite offline retention.

Set exit budgets from verified host behavior. Codex currently caps SessionEnd at 3 seconds. Its parent writes an `.ended` marker before launching a worker; a later SessionStart scans unfinished or expired active sessions. Resume does not imply an ended session, and an old worker must not commit a newly resumed session, so markers and cleanup must refer to the same exit event. See the [Codex commit design](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md).

Use `maybeDetach()` and `readHookStdin()` for background writes and correctly transfer already-consumed stdin. Avoid losing the payload when parent and worker each try to read it. A successful detach means only that a worker started. Verify whether it survives host exit and how transcript data, pending entries, or end markers recover failures. A valid empty hook response is not a write acknowledgement.

Synchronous shutdown needs one total budget covering lock waits, catch-up, and commit. Each step uses the remaining time instead of receiving the full hook timeout again. Background workers must also be bounded; a stuck request must not hold a lock indefinitely.

A server field, configuration echo, or API's existence does not prove automatic commits execute in the actual message path. Before transferring commit responsibility from a plugin to the server, verify runtime behavior, older-server fallback, and duplicate-commit prevention. Do not remove reliable plugin commit points solely because an `auto_commit` setting exists.

## 6. MCP and model-visible tools

Hooks automate lifecycle behavior; MCP exposes tools the model chooses to call. Both connect through the same configuration and identity, but have separate responsibilities. Document what is injected automatically, which operations require model calls, and which MCP tools remain usable when automatic behavior is disabled.

Hosts with stdio MCP support should use the shared proxy employed by Claude Code and Codex: resolve configuration, shape it with `buildMcpProxyConfig()`, and pass it to `createOpenVikingMcpProxy()`. MCP transport owns its own sessions, SSE, and protocol negotiation, so a REST JSON helper is not a substitute. Common authentication headers still come from `buildOvHeaders()`.

Proxy entrypoints must not own separate tool schemas, API clients, SSE parsers, or retry state machines. Treat server `tools/list` and `tools/call` as authoritative instead of maintaining a second memory-tool catalog to customize descriptions. Necessary local tools use the shared `localToolProvider` interface with explicit scope, and must not accidentally shadow server tools.

For each new host, verify:

- Startup from the host's actual working directory, including relative paths, root variables, and environment allowlists. Codex's `.mcp.json` `env_vars` is a host-specific requirement, not evidence that other hosts inherit the same variables.
- `initialize` negotiation, notifications without ordinary responses, JSON/SSE handling, concurrent response IDs, and protocol-clean stdout.
- Shared recovery for expired MCP sessions and changed credential files. Do not blindly retry arbitrary tool calls after network failures, especially calls with write effects.
- Expected hook and MCP URLs and identities across environment configuration, default/custom files, and profile switching. Observe request targets and headers rather than only configuration contents.
- Tool names in diagnostics and documentation match what the host exposes. Do not rename `remember` to `store` in documentation or copy another host's namespace.

Use direct remote MCP connections only when the host has suitable credential and configuration support, and document how it stays consistent with hooks. Do not add wrappers, environment files, or installation-time rewrites to work around a problem the shared proxy already solves.

## 7. URI guards, skills, and diagnostics

`viking://` is a virtual URI and must not be passed to local file or shell tools. Where the host supports checks before tool execution, use `evaluateUriGuard()` and keep only tool-parameter mapping and denial envelopes in the adapter. Do not expand the guard into a general command interceptor; ordinary file paths retain their behavior. Without the required event, document the limitation and guide models through a skill, without claiming equivalent interception.

Shared skill sources live in [`examples/skills/`](https://github.com/volcengine/OpenViking/tree/main/examples/skills/) and are shipped through `SKILL_TARGETS`. Do not edit the same guidance separately in several plugin copies. Skills must describe tools that can actually be called and capabilities that exist. Do not instruct the model to repeat capture or commits each turn when hooks already own them. Distinct tool surfaces may need distinct skills; explain why. Generated skill files must not receive a banner before their YAML frontmatter.

Use `runDoctor(hostSpec)` for diagnostics. Hosts supply installation paths, manifests, hook registrations, and state checks; `doctor-core.mjs` owns common configuration, credential, network, and output handling. Diagnostics must make it possible to inspect the installed version, configuration sources and effective values, peer, MCP entrypoint, hook budgets, and pending/session state. Prefer offline and JSON modes; offline checks must not silently access the network.

Diagnostics should distinguish disabled, bypassed, no results, timeout, authentication failure, queued, enqueue failure, and commit failure. Logs retain host, stage, session association, latency, and trace IDs. They must not log API keys, full Authorization headers, or whole user transcripts by default. Doctor recommendations must use commands valid for the current installation form, not removed debug scripts.

## 8. Layout, generation, and installation

### 8.1 Recommended layout

These are placement conventions, not a requirement to create every file. Angle brackets represent names supplied during implementation.

```text
examples/memory-plugin-shared/
  lib/<capability>.mjs          Authoritative shared behavior
  lib/<capability>.d.mts        Types when needed; maintained with implementation
  lib/install/                 Installer-only code, outside hook dependency closures
  testing/support.mjs          Test helpers, not shipped as runtime
  sync.mjs                     Distribution targets and closure generation

examples/agent-hook-plugin/
  hosts/<host>.mjs             Host adapter
  hosts/<host>/                Host declarations, hook/MCP config, required assets
  scripts/hook.mjs             Common dispatcher
  servers/mcp-proxy.mjs        Common MCP entrypoint

examples/<host>-memory-plugin/ Create only when a separate native plugin is needed
  <host manifest directory>/
  hooks/
  scripts/                    Host entrypoints, state, transcript adaptation
  scripts/shared/             Generated; do not edit
  servers/
  skills/
```

Use explicit relative imports. Where shared modules have TypeScript consumers, keep `.d.mts` beside the authoritative `.mjs` and generate both together. Do not hand-maintain declarations in each artifact directory. List re-exports explicitly when needed to avoid `export *` collisions with adapter implementations.

### 8.2 Choose generation strategy by distribution

| Distribution | Current examples | Requirement |
| --- | --- | --- |
| Host loads a plugin directory directly from Git | Claude Code, Codex, `agent-plugins`, OpenClaw (`ov-install` GitHub source) | Commit shared copies so a checkout is loadable |
| npm package or installation archive | OpenCode, DSH, Pi | Generate during prepack or staging; do not commit runtime copies |
| Installer assembles an adjacent runtime directory | Cursor, TRAE, TRAE CN, ZCode | Derive `lib/MANIFEST` from `ASSEMBLED_ROOTS` and copy runtime modules from it |

Register these targets in [`sync.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs). For separate plugins, add the source root, output directory, and `committed` policy to `TARGETS`. Extend `ASSEMBLED_ROOTS` only for a new assembled root; ordinary thin hosts are usually covered already. Register skill delivery separately in `SKILL_TARGETS`. Do not maintain a manual list of runtime modules to copy.

After changing shared source, run:

```bash
node examples/memory-plugin-shared/sync.mjs
```

The generator analyzes static imports and literal dynamic imports. Keep required dependencies discoverable; do not hide them behind constructed strings. Preserve relative layout after installation so the same import works in the source tree and on the user's machine. Do not rely on absolute development paths, temporary symlinks, or `NODE_PATH` to make local tests pass.

Pull requests must include current generated files where the delivery policy requires them to be committed, including newly generated untracked files. The main-branch [`plugin-shared-sync.yml`](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/plugin-shared-sync.yml) is an additional safeguard, not a replacement for a complete change. Documentation-only edits that do not affect generation sources need not regenerate unrelated copies.

### 8.3 Installation and removal contracts

Reuse [`install.sh`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/install.sh). Keep JSON/JSONC merge logic in [`lib/install/`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared/lib/install/) instead of adding large JavaScript programs inside shell heredocs. Installers should:

1. Parse, validate, and prepare required files before changing user configuration. Invalid JSON/JSONC must produce an error and preserve the original file, not be treated as empty configuration.
2. Be idempotent, without duplicate hook/MCP entries. Preserve other plugins, user settings, and meaningful comments in formats that support them.
3. Handle spaces in paths, hosts without variable expansion, and custom configuration paths. Write only fields the host supports.
4. Remove only entries owned by this plugin, including URI guards. Preserve other integrations, credentials, memory, and session data. Uninstalling should not require downloading source again.
5. Keep shared runtime files available to other clients after removing one client. Failures must not leave configuration split between old and new installation directories.

Validate archives with [`stage-memory-plugin-marketplace.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/stage-memory-plugin-marketplace.sh) and [`check-marketplace-archive.mjs`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-marketplace-archive.mjs). Entrypoints, transitive dependencies, and scripts referenced by skills must exist. Exclude `node_modules`, `.git`, and local secrets. Validation must also detect an omitted distribution target, rather than merely trusting the staging directory list.

Before release, extract the final archive or tarball and smoke-test entrypoints and installation. Successful source-tree imports prove only that the source tree is complete, not the package users receive.

## 9. Reviewing code smells and maintainability

Review the path from host input through the normalized model, shared capability, acknowledgement state, and installed artifact. Each rule should have one implementation and each side effect an explicit caller. Address the following issues in relevant changes or document a concrete compatibility reason. A promise to unify code later does not justify new duplication.

| Signal | Problem | Preferred treatment |
| --- | --- | --- |
| Copied and renamed clients, recall implementations, or doctors | Fixes require multiple edits and behavior diverges | Move rules into the existing capability module; pass host parameters |
| A field appears in several default tables | Configuration, diagnostics, and execution disagree | Declare it in the schema and derive other views |
| A setting is read but never consumed | User changes have no effect | Trace actual execution; do not advertise unsupported behavior |
| Shared functions repeatedly branch on harness names | Shared code is taking ownership of host protocols | Move event, format, and policy differences into adapters |
| Several boolean options eliminate only a few repeated lines | Callers must understand incompatible modes and invalid combinations | Use small callbacks or retain short, direct host code |
| One `utils.mjs` handles parsing, networking, and commits | Rules lack ownership and tests are hard to isolate | Split by capability and keep parsing mostly pure |
| A loader writes files or starts services while reading config | Calling it again changes the system | Move actions into explicit installation or lifecycle entrypoints |
| An ambiguous `true` means handled | Delivery, queueing, and skipping cannot be distinguished | Return explicit results with errors and acknowledgement counts |
| A catch returns success or an empty array | Unreadable files and authentication failures look like no data | Preserve failure categories and let the host entrypoint choose a valid fallback |
| Global state represents the current session | Concurrent windows and subagents overwrite each other | Key state by session and pass the current identity |
| `export *` and multiple layers of same-name forwarding | The implementation being called is unclear | Use explicit imports/exports and remove wrappers without compatibility value |
| Comments promise reliable commits while code only calls `spawn()` | Documentation hides unacknowledged effects | Document recovery conditions and verify completion through state |
| Shared copies or generated type declarations are edited manually | The next sync overwrites the fix | Edit the authoritative source and regenerate |
| Release scripts list every shared file manually | New dependencies are missed until installation | Validate dependency closures and manifests |
| Stale designs, unused scripts, and copied tests remain | Search results mislead maintainers and duplicated tests inflate coverage | Remove obsolete references and files; keep history in Git |

Name functions and variables after actions and states: `parseTranscript`, `sent`, `queued`, and `commitAccepted` are useful; `handleEverything`, `done`, and `successLike` hide meaning. Comments should explain host constraints, data invariants, and tradeoffs rather than repeat the next line. Document why a wrapper exists if it only supports logging or compatibility.

Similar code is not necessarily equivalent. Calling the same builder does not prove hooks and MCP use identical credential projections. Verify observable behavior before fixing or preserving a difference. Do not turn every line of a reference plugin into a requirement.

## 10. Implementation sequence for a new plugin

1. **Record the host contract.** Establish the facts in section 2, the support matrix, minimum versions, substitutes for absent events, session IDs, and commit timing.
2. **Choose the integration form.** Prefer adding a thin-host adapter. Create a separate package only for actual distribution or lifecycle requirements, and document the choice.
3. **Connect configuration and MCP first.** Register the host ID, use the shared builder and proxy, verify paths, environment, and authentication under real startup conditions, and drive hooks from the same configuration.
4. **Connect automatic reads.** Adapt startup profile and per-turn recall events and outputs. Verify disabled, bypass, empty-result, and network-failure behavior.
5. **Connect reliable writes.** Implement transcript decoding, stable identity, incremental cursors, acknowledgement updates, commits, and recovery. Prioritize disconnections, duplicate events, and missing tail messages.
6. **Add supported host features.** Add URI guards, skills, and diagnostics where supported. Do not add empty handlers for events that do not exist.
7. **Complete installation and distribution.** Register generation targets, installers, archives/marketplaces, version checks, and release workflows. Package entrypoints must run directly.
8. **Verify and document.** Reuse existing contract tests, verify host differences, smoke-test final artifacts, and record tested versions, platforms, and limits.

Each step needs an inspectable result. A created directory or working tool list is not a complete integration. Work may be split into independent commits, but each commit should have explainable behavior and preserve existing integrations.

## 11. Testing and acceptance evidence

Test observable contracts and major failure cases. Follow the contribution guidelines: prefer extending existing high-value tests instead of mechanically adding unit tests for forwarding code, new files, or configuration lines. Test shared capabilities once; host tests cover wiring and differences. Do not copy entire recall, pending, or MCP suites.

Put common helpers in [`testing/support.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/testing/support.mjs). Do not import helpers from other test files or place them in the runtime `lib/`. Use private temporary directories, isolated configuration and pending paths, and dynamic ports. Installation tests must not modify developers' live agent configuration or share a directory that another test's generator rewrites.

| Area | Behavior to verify | Existing entrypoints |
| --- | --- | --- |
| Configuration and switches | Layer precedence, alias conflicts, invalid values, configured flags, no new network effects when disabled | `plugin-config.test.mjs`, `plugin-known-keys.test.mjs`, host config tests |
| Credentials and peers | Matching hook/MCP request identity; custom paths, profile switching, multiple workspaces | `credentials.test.mjs`, `wire-headers.test.mjs`, `mcp-proxy-config.test.mjs` |
| Hook output | Real payloads, one valid response, empty results, missing/unknown fields, failures | `agent-hook-runtime.test.mjs`, host event tests |
| Recall | Switches, bypass, empty results, server compatibility, compression failure, readable URIs | `recall-core.test.mjs`, host recall tests |
| Capture | Complete text/tools, repeated events and text, nested tools, partial success, truncation recovery | `capture-utils.test.mjs`, `batch-send.test.mjs`, host transcript tests |
| State and commits | Concurrent Stop/End, failed workers, resume versus old end markers, unreadable tails, enqueue failure, retained commit parameters | `pending-queue.test.mjs`, Codex session tests, host recovery tests |
| MCP | Initialize, protocol versions, notifications, SSE, expired-session recovery, error semantics, stdout | `mcp-proxy-core.test.mjs`, plus host startup smoke tests |
| Installed artifacts | Fresh/repeated installs, preserved third-party config, invalid config not overwritten, complete uninstall, dependency closures | `install-agent-hooks.test.mjs`, `release-marketplace.test.mjs`, `sync.test.mjs` |
| Maintenance constraints | One header implementation, matching schema/consumers, correct generated-file tracking | `one-header-builder.test.mjs`, `plugin-known-keys.test.mjs`, `sync.test.mjs` |

Filenames are relative to the shared library unless a host is specified. Use [PR CI](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml) and each package's scripts for complete commands; do not create another manually maintained repository-wide test inventory. Source-structure checks protect important single-implementation constraints, but do not replace behavioral tests or justify pinning unrelated helper names or line counts.

For example, after changing shared configuration and MCP contracts, run focused checks from the repository root, followed by affected host tests:

```bash
node examples/memory-plugin-shared/sync.mjs
node --test \
  examples/memory-plugin-shared/plugin-config.test.mjs \
  examples/memory-plugin-shared/credentials.test.mjs \
  examples/memory-plugin-shared/mcp-proxy-config.test.mjs \
  examples/memory-plugin-shared/mcp-proxy-core.test.mjs
```

Installer and marketplace tests generate runtime files and assemble installations. Run them separately and serially, as CI does:

```bash
node --test --test-concurrency=1 \
  examples/memory-plugin-shared/install-agent-hooks.test.mjs \
  examples/memory-plugin-shared/release-marketplace.test.mjs
```

CI currently uses Node.js 24, and some TypeScript tests rely on native type stripping. Document the test environment separately from the minimum plugin runtime version. Passing Node.js tests does not establish that Windows detach behavior, path quoting, or process cleanup works. State untested platforms rather than substituting an overall green test count for verification scope.

Acceptance evidence should identify the artifact, host version, scenario, and observation. For writes, confirm complete tail messages and the corresponding commit reached the server. For installation, confirm extracted entrypoints can resolve their dependencies. Documentation-only changes need syntax, relative-link, symbol-reference, and example checks; they do not require the entire plugin suite.

## 12. Maintenance, releases, and documentation

### 12.1 Follow through by change type

| Change | Required follow-up |
| --- | --- |
| Shared behavior fix | Edit the authoritative module, verify affected hosts, generate copies, inspect relevant artifacts and versions |
| Host payload or event update | Update adapter, real-format fixtures, minimum-version/fallback notes; preserve other hosts' defaults |
| New setting | Schema, actual consumer, configured semantics, diagnostic source, user documentation; parsing alone is insufficient |
| New shared dependency | Colocated types where needed, sync closure, `lib/MANIFEST`, archive and offline installation validation |
| Directory or package rename | Imports, manifests, installer, marketplace, CI path filters, skills, links, uninstall identification |
| Compatibility removal | Supported versions and migration notes; preserve configuration aliases still promised to users |
| Documentation only | Accuracy and working links; do not change unrelated behavior to update prose |

Start a fix from the failing behavior and its owning module. A shared defect exposed by one host still belongs in shared source; a payload change unique to one host belongs in that adapter. Split large refactors into independently verifiable, reversible steps rather than combining behavior changes, historical cleanup, and distribution changes into one replacement with unclear causes.

### 12.2 Versions and releases

Versions determine whether users receive updates. A new plugin must join the applicable version checks and release workflow, not merely add a directory. [`check-plugin-version-bumps.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-plugin-version-bumps.sh) counts shared `lib/` changes for every plugin it registers. That is not proof that all distribution targets are covered automatically. Check registration whenever adding or changing a target.

Keep host manifests, `package.json`, root package versions in existing lockfiles, installation manifests, and installer version checks consistent. Use the package manager's versioning workflow without rewriting unrelated dependencies. `User-Agent` and doctor output should report the actual artifact version, so an apparently upgraded installation does not run old code.

npm/archive builds must generate shared files from clean source before packaging. The current [`plugin-npm-release.yml`](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/plugin-npm-release.yml) matrix covers DSH and OpenCode. Check other packages' release paths individually; sharing a prepack command does not mean they are in that matrix. Release triggers must include shared source changes rather than depend on diffs in generated copies that are no longer committed.

Before merge, check versions against the actual base. For example, when local `origin/main` is current and is the target branch:

```bash
bash .github/scripts/check-plugin-version-bumps.sh origin/main
git diff --check
git status --short --untracked-files=normal -- examples agent-plugins
```

The version check identifies changes through committed `base...HEAD`; it does not treat uncommitted work as a complete pull request. Run the final check against the final commit. Inspect generated untracked files as well as `git diff`. After release, verify the package and version users actually download; a started CI run is not completion.

### 12.3 Documentation is part of the interface

A plugin README should include supported capabilities and versions, the quickest usable installation method, credential sources, automatic behavior versus MCP, core switches, limitations, upgrade/removal, diagnostics, and test entrypoints. Start user installation instructions with a directly usable one-command option. Keep GUI steps separate rather than mixing CLI commands or TOML configuration into a GUI procedure.

User integration guides and this standard live in `docs/{en,zh}/agent-integrations/`; maintain corresponding English and Chinese pages together. If a host also has mirrored instructions in `docs/images/agents/{en,zh}/`, check those too, without creating mirrors that do not otherwise exist. Link to the shared README for complete common configuration details and keep host pages focused on differences and common examples.

Design documents should retain constraints, decisions, and recovery invariants that reading the code alone does not explain. Update support matrices, commands, and entrypoints when they change, and remove contradictory old guidance. Temporary investigations, one-off validation reports, and stale test counts are not long-term design documentation.

## 13. Pre-merge checklist

- [ ] Host versions, input/output formats, time limits, message sources, and end semantics have evidence; unsupported capabilities are explicit.
- [ ] The integration form fits the host; new code mainly describes host differences, with no shared-to-host dependencies.
- [ ] Configuration is declared once, switches affect execution, and actual hook/MCP connections and identity are verified.
- [ ] Session and peer identities are distinct; multiple windows, resume, subagents, and cwd changes do not misuse state.
- [ ] Capture preserves text and tool evidence; partial failure, offline queueing, duplicate events, and tail recovery do not advance cursors incorrectly.
- [ ] Commit ordering and recovery ownership are clear, including a verified recovery path when exit workers fail.
- [ ] Hook/MCP output is valid; URI guards, skills, and diagnostics promise only supported behavior.
- [ ] Generation targets, installation, removal, archives, types, and release triggers are complete; the final artifact was tested.
- [ ] Versions and related manifests agree; checks match the change and untested platforms/scenarios are documented.
- [ ] Documentation and migration notes describe the final behavior, without new duplicate settings, old paths, or unused helper scripts.

## 14. Maintainer reading guide

Choose an entrypoint by the problem, instead of starting from generated plugin copies:

- Supported behavior across integrations: [Capability Reference](./16-capability-reference.md).
- Configuration, peers, and generation strategy: [Memory Plugin Shared README](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md).
- Claude Code event wiring: [hooks.json](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/hooks/hooks.json); recall adaptation: [auto-recall.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/scripts/auto-recall.mjs).
- Codex commits, abnormal exits, and recovery: [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md); implementation: [session-end.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/scripts/session-end.mjs).
- New configuration-file hosts: [agent-hook-plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README.md); strict protocol example: [ZCode DESIGN](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/DESIGN.md).
- CI and delivery checks: [pr.yml](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml), [sync.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs), [marketplace archive checker](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-marketplace-archive.mjs).
