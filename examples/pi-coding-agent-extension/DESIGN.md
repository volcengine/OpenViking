# Pi OpenViking Extension — Design

The extension gives a pi session long-term memory and lets OpenViking own committed history when takeover is enabled. TypeScript loads directly through pi's jiti transpiler. Recall, session capture, commits, context takeover and health checks use REST; model-facing tools use the official MCP client SDK over Streamable HTTP.

README.md is the operator's document — installation, every configuration knob, the tool surface. This one is the maintainer's: what each module is responsible for, how the pieces meet at pi's event boundaries, and the reasons behind the choices that the code cannot state for itself.

## Design ancestry

Three earlier OpenViking integrations shaped this one. OpenClaw contributed synchronous recall against the current turn and threshold-triggered commits. The Claude Code plugin, the most production-hardened of the three, contributed the capture pipeline — sanitize injected blocks before capture, keep tool inputs, drop raw tool output — along with score-thresholded ranking, the pre-compact commit and session-resume rehydration. Hermes contributed the anti-pattern: it prefetched recall for the *previous* turn's query, so the first turn of a session got nothing and a topic switch got the wrong memories.

| Concern | Hermes | OpenClaw | Claude Code | This extension |
|---|---|---|---|---|
| Recall query | previous turn | current turn | current turn | current turn |
| Injection point | user message | user message | user message | `context` hook, newest user message |
| Commit trigger | session end | token threshold | threshold + pre-compact + session end | threshold + pre-compact + session end |
| Capture sanitization | none | its own recall block | every injected block | shared `capture-utils` |
| Committed history | owned by the agent | replaced by OV archives | owned by the agent | replaced by OV archives (default on) |

What the Claude Code plugin proved is no longer copied here — it is imported. `shared/` is a generated copy of `examples/memory-plugin-shared/lib`, produced by that directory's `sync.mjs`. Recall assembly, capture sanitization, profile building, the disk pending queue, batched sending, credential and settings resolution, and bypass matching all live there and behave identically in every harness. What stays local is the pi-shaped part: how a pi branch becomes capture payloads, how the `context` hook is rewritten, and how state survives `pi -c`.

## Layout

```
pi-coding-agent-extension/
├── config.ts     # settings, credentials, peer resolution
├── client.ts     # HTTP client for the OpenViking REST API
├── recall.ts     # per-prompt recall search and injection
├── sync.ts       # capture, delivery, pending queue, commit
├── takeover.ts   # binds the takeover state machine to pi
├── tools.ts      # publishes the bridge's tool descriptors as pi tools
├── index.ts      # entry point: event handlers and the /viking command
├── package.json  # name and version; pi loads index.ts regardless
├── lib/          # pi-specific logic kept out of the event handlers
│   ├── mcp-bridge.mjs        # official SDK connection lifecycle
│   ├── mcp-result.mjs        # pi content conversion and output limit
│   ├── takeover-core.mjs     # the context-takeover state machine
│   ├── recall-ledger.mjs     # injected recall blocks, keyed by pi entry id
│   ├── capture-adapter.mjs   # a pi branch -> capture payloads
│   └── uri-guard-adapter.mjs # pi tool events -> the shared URI guard
├── shared/       # generated copy of memory-plugin-shared/lib
├── scripts/      # live e2e harness
└── tests/        # node --test suites
```

Modules imported by TypeScript have adjacent declarations; the result converter is internal to the JavaScript bridge. The bridge and tool registration can be tested without installing pi.

## Modules

### config.ts

Defines `OVConfig` and resolves it once at load time. Knobs come from `resolveSettings("pi", { cwd })`: every one of them is declared in `shared/config-schema.mjs` and resolved through the same layers as every other harness — environment, the workspace's `.openviking/config.json` and machine registry entry, `ovcli.conf`'s `plugin.pi`, `ovcli.conf`'s `plugin`, then the schema default. The extension has no configuration file of its own. It used to ship one holding exactly the code defaults, which made an operator's choice indistinguishable from a factory setting.

Credentials — server URL, API key, account, user — and the auth mode that gates whether identity headers go on the wire at all come from `resolveConnection("pi")`, the one connection resolver every harness's hooks and MCP proxy share. The peer identity is resolved twice on purpose: `resolvePluginPeerId` picks the id from the configured layers, then `resolveEffectivePeerId` maps it onto the workspace and also returns `legacyPeerId`, the pre-git workspace id that recall still has to reach.

Two older spellings are kept alive because setups depend on them: `bypassPatterns` holds the same list as the shared `bypassSessionPatterns`, and `OV_DEBUG_LOG` is read alongside the shared `OPENVIKING_DEBUG_LOG`. `EXTENSION_VERSION` reads `package.json`, the same manifest the release gate watches for a bump, and feeds the shared `User-Agent` builder.

### client.ts

The transport is built once in the constructor by `createOvHttp` from `shared/ov-http.mjs`, and `fetchJSON(path, init, timeoutMs)` is the thin call into it. Nothing throws: callers branch on `ok` over the same `{ ok, result, status, error, traceId }` envelope every harness reads, and a transport failure arrives as `status: 0`. The trace id is lifted out of whichever place the server put it so that failures can be correlated in the server's own logs.

Headers are built per request: `Authorization: Bearer` when a key is configured, `X-OpenViking-Actor-Peer` for peer scoping, the shared `User-Agent`, and `X-OpenViking-Account` / `X-OpenViking-User` only when `sendIdentityHeaders` says the deployment is in trusted mode. Timeouts follow the class of call — 5s for health and session metadata, 10s for reads and message writes, 30s for commit and resource ingestion.

Above that sit thin methods for the endpoints the extension itself needs: health, session metadata, session context, and commit through `commitSessionResponse`, which returns the whole envelope so a failure can be logged with its status and trace id. Search, content reads, filesystem operations and resource ingestion used to have wrappers here too; they are the model's business and reach the server over MCP now, so they are gone rather than kept as a second path to the same endpoints that would drift from the first.

### recall.ts

`RecallManager` runs one search per prompt and injects its block into the provider's view of the conversation.

The split between queueing and searching is what keeps recall off the UI path. `queueSearch(prompt)` runs in `before_agent_start` and only records the text. The first `context` hook of the turn calls `searchPending()`, which performs the search — by then pi has already rendered the user's message, so recall latency delays the model request but never the screen. Later LLM iterations within the same turn reuse the cached block, and `agent_end` invalidates it.

The search itself is `buildRecallBlock` from the shared `recall-core.mjs`; quotas, scopes, budgeting and formatting are shared with every other harness. The extension supplies three things of its own: the actor peer id, the legacy peer id (under `actor` scope the effective peer is the only one asked, so a workspace whose id changed would otherwise lose everything written before the change), and the OV session id, which is what turns on server-side query expansion and the cross-turn dedup ledger. A query shorter than `minQueryLength` skips the round trip entirely.

Injection is two-pass, and that is the interesting part. Historical user messages get back the exact block the recall ledger says was sent with them; only the newest user message receives this turn's fresh block, which is then recorded. The result is a request prefix that stays byte-identical across turns, so the provider's prompt cache keeps hitting instead of being invalidated by every new memory. The ledger keys on pi's entry id plus the message's original text — entry ids survive compaction and branch navigation, where an ordinal would not. When the host does not expose entry ids the pass fails closed: fresh recall still reaches the newest message, but nothing historical is replayed or recorded under an unstable key. A message that already contains `<openviking-context` is never injected into twice.

### sync.ts

`SyncManager` owns the OV session id, the capture watermark, and everything between a pi branch and a committed archive.

The OV session id is `pi-<pi session id>`, derived locally by the shared `deriveHarnessSessionId`. Nothing round-trips to open a session: the id is deterministic, so the first write can carry it.

**Capture.** `turn_end` hands the whole branch to `extractBranchCapturePayloads` (`lib/capture-adapter.mjs`), which takes the entries past `syncedEntryCount`, normalizes roles, renders tool parts with bounded input and output, and decides entry by entry whether to capture. There are two decision modes. Normally it is the shared `shouldCaptureText` heuristic. Under takeover it is a faithful mode that drops only empty text, slash commands and OpenViking's own status messages: once the boundary advances, a short acknowledgement may be represented to the model *only* through the archive overview, so discarding it as low-signal would lose it outright. A branch shorter than the watermark means pi navigated to a different branch, so the watermark resets to zero and the branch is re-extracted from the start.

**Delivery.** Payloads leave in one batched request through the shared `sendSessionMessages`. Retryable failures are written to the shared disk pending queue and replayed later. The result distinguishes delivered, queued and permanently lost payloads. A non-retryable rejection or failed enqueue advances the capture watermark to avoid duplicate replay, but also records a persistent capture gap; takeover never trims across that gap. The watermark only moves when the whole extraction has either been delivered, queued or recorded as permanently lost.

**Backlog drain.** `flushForTakeover` is the barrier takeover waits on, and it needs the queue empty for this session. Startup restores takeover state before replay, then routes the current session through the tracked Pi drainer rather than the generic replay path. Sync drains queued `addMessage` entries through the batch endpoint, `BATCH_LIMIT` per request, and also treats active `.processing` files as undelivered. The drain is bounded by wall time (`OPENVIKING_PENDING_DRAIN_BUDGET_MS`, 10s by default, narrowed further to whatever takeover's handler budget leaves) and optionally by batch count, so a huge backlog cannot block `turn_end` indefinitely; the remainder drains on later turns. Once this session's queue is empty, startup hands other sessions' entries to the generic replay; while this session still has a backlog they wait for a later start, because the generic path drops rejected entries without recording whose gap they are. Other sessions never affect this session's barrier. The same generic replay runs in every OpenViking harness plugin on the machine over the same queue, so a gap is guaranteed to be recorded only for losses this session's own drainer sees.

**Commit.** Outside takeover, sync asks the server for `pending_tokens` after each accepted turn and commits when it crosses `commitTokenThreshold` — server-side accounting, not a local estimate. A failed commit is queued for replay unless the caller passes `queueOnFailure: false`, which takeover always does, because a commit that lands later cannot justify a boundary that moved now.

### takeover.ts

A binding, not a mechanism. The state machine lives in `lib/takeover-core.mjs`, which is pure and unit-tested; `takeover.ts` supplies its I/O: branch sync, flush and commit go to `SyncManager`, each archive overview is read directly from `<archive_uri>/.overview.md`, state is persisted through `pi.appendEntry`, and the watermark is read back from sync.

Context takeover makes OpenViking the authoritative long-term store for a pi session. Pi still keeps recent turns locally; committed history is represented to the model by OpenViking's archive overview through pi's `context` hook.

#### Model

| Field | Meaning |
|---|---|
| `coveredThroughEntryId` | The boundary: id of the pi entry the covered prefix ends at, inclusive |
| `coveredUserTurns` | User turns the boundary covers in the active context (display only) |
| `overview` | Overview of the archive the boundary advanced behind, read from `<archive_uri>/.overview.md` |
| `pendingTokens` | Estimated synced token pressure since the last successful advance |
| `lastSeenUserTurns` | User turns counted in the most recent `context` hook (display only) |
| `syncedEntryCount` | Pi branch watermark, restored across `pi -p` / `pi -c` processes |
| `archiveUri` / `historyUri` | Exact archive and parent history directory used by the recovery hint |
| `pendingArchive` | Accepted archive whose own overview is not ready yet, including its frozen boundary |
| `captureGap` | Persistent proof that at least one captured message can no longer reach the server |

State is persisted as a pi custom entry:

```ts
pi.appendEntry("ov-takeover", state)
```

It is written on state transitions — an advance, a pending archive, a capture gap — and at shutdown, not on every turn, since each entry carries the overview. At startup the extension scans the branch from the end for the newest such entry and restores both the boundary and `SyncManager`'s watermark, so a `pi -c` continuation does not resend branch entries OpenViking already has. An entry written by 0.4.1 or earlier holds its boundary as a user-turn count plus an optional fingerprint instead; the first `context` hook locates it the way those versions did and converts it to the entry id in front of the first kept turn, or drops it when it no longer matches.

#### Boundary identity

The boundary is an entry id because pi shows takeover two views of the same session that do not line up. `getBranch()` holds every entry on the path: the `ov-takeover` state entries takeover itself appends between turns, model and thinking-level changes, and after a pi compaction the whole compacted-away prefix. The `context` hook's messages hold none of those. A user-turn count or a "last covered message" fingerprint taken on one view lands somewhere else on the other — the state entry in front of every user turn alone makes a branch-side fingerprint disagree with the context on the next request. An entry id names the same entry in both.

Takeover therefore works on pi's context projection of the branch (`projectContextEntries`: after the latest compaction only its kept entries and what follows, minus entries a context edit removed — pi's own `buildContextEntries`, which pi < 0.86 does not expose). The boundary is the entry in front of the oldest kept user turn. In the `context` hook, the first user entry after it is matched onto the hook's messages by its timestamp, which content rewrites leave alone, and the cut goes there. Whatever sits between the boundary and that turn — what a run appended after a `takeoverKeepRecentTurns` 0 commit, or a branch summary `/tree` left at the boundary — is covered by no archive and stays: the cut steps back over those messages role by role, and when the roles disagree with the hook's messages nothing is trimmed. When the boundary entry is not in the active context — `/tree` moved to another branch, or pi compacted past it — the full context is sent and the boundary is kept, so returning to the branch applies it again.

#### Handler budget

Every takeover step runs inside a pi event handler, and pi hosts cap those at 30s: omp logs `handler timed out after 30000ms`, discards the result and lets the handler run on (#5275). Each handler therefore gives takeover a deadline 25s after it started (`HANDLER_BUDGET_MS`), sync included. Nothing sleeps waiting for a summary in `turn_end` or `before_agent_start`; the drain gets the time the commit does not need, the commit gets the time one overview read does not need, and with less than 10s left the commit waits for a later turn. Only the compaction handler polls, because pi needs its summary now, and it stops at the deadline.

#### Runtime flow

1. `turn_end` captures new branch entries into the OpenViking session, falling back to the disk pending queue when the server is unreachable.
2. When `pendingTokens` reaches `takeoverTokenThreshold`, and there are more user turns than `takeoverKeepRecentTurns`, takeover tries to advance.
3. On one branch snapshot, takeover freezes the candidate boundary entry, token pressure and the exact number of capture payloads the branch holds after the boundary. A candidate no later than the current boundary is not worth an archive.
4. It syncs the latest branch and drains this session's queue. A transient queued failure may proceed after the drain succeeds; a permanent rejection, enqueue failure or retry exhaustion persists `captureGap` and blocks takeover for this session.
5. The commit runs with `queueOnFailure: false` and the retained payload count as `keep_recent_count`. A skipped commit, `archived: false` or missing `archive_uri` cannot advance the boundary.
6. The extension reads `<archive_uri>/.overview.md` directly, once. It never substitutes an older session-context overview. A summary that is not there yet — phase 2 usually needs longer than a handler may wait — persists the pending archive and frozen boundary; every later `turn_end` and the next `before_agent_start` read that archive once, without committing again. When the archive carries the server's terminal marker — `.done`, written last once phase 2 completed (with Working Memory disabled it completes without a summary), or `.failed.json` — and a last read still finds no summary, the pending archive is dropped and its frozen token pressure spent, so the next archive waits for fresh pressure. The markers are the server's own archive state and, unlike task records, do not expire. A pending archive whose boundary left the active context is dropped without a read.
7. Once the overview is non-empty, takeover confirms the frozen boundary entry is still in the active context. Only then does it advance; token pressure accumulated while waiting remains for the next archive.
8. The `context` hook then replaces the covered *conversation* with one synthetic user message beginning `[OpenViking Session Context]`, keeps every covered `system` message in front of it in original order, keeps the recent tail verbatim, and recall is injected into the newest kept user turn as usual. With `takeoverKeepRecentTurns` 0 the boundary is the tip at commit time and applies from the next user turn; conversation the run added after that commit is never trimmed, because no archive covers it.

The overview message's timestamp is derived from the first kept message, so the provider payload stays byte-stable between commits and can benefit from prompt caching.

On pi ≥ 0.86 the transcript carries the base prompt and its tool declarations as the leading `system` message, and mid-conversation tool additions/removals, section updates and appended instructions as later `system` messages (`@earendil-works/pi-ai`'s `getCurrentTools` / `getCurrentSystemMessage`). Slicing those off with the covered turns would strip the model's tools and instructions, so they are preserved: the overview stands in for the conversation, never for the system state. A `system` message inside the retained tail is left where it is rather than hoisted. On 0.80.3 there are no `system` messages in the branch, so this preserves nothing and the behaviour is unchanged; on 0.87 the host reconciles the declared tools against the executable set on every request, so keeping the existing declarations introduces no duplicate.

#### Compaction

When pi emits `session_before_compact`, takeover first syncs the latest branch, fully archives the captured history with `keep_recent_count: 0`, and polls that archive's overview — `takeoverOverviewPollMax` reads, `takeoverOverviewPollMs` apart — until the handler deadline. On success it hands Pi the overview while preserving Pi's `firstKeptEntryId`; summary coverage may therefore overlap the retained tail. If sync, commit, overview generation, the deadline or cancellation prevents a proven result, the handler returns `undefined` and Pi performs its native compaction. That path changes no takeover state beyond remembering the archive: pi may still cancel or fail its own compaction, and the boundary has to keep applying to the uncompacted context. If pi does compact, the entry-id boundary either falls outside the new context or still covers only what its overview covers.

```ts
{
  compaction: {
    summary: "[OpenViking Session Context]\n...",
    firstKeptEntryId,
    tokensBefore,
    details: { source: "openviking" },
  }
}
```

If any step fails the handler returns nothing and pi's default compaction runs. Fail-open is deliberate: a failed takeover must never leave the session without a compaction.

#### Failure modes

| Failure | Behavior |
|---|---|
| Health check fails | Extension stays disconnected; pi runs normally |
| Pending `addMessage` replay incomplete | Barrier stays closed, boundary is not advanced, full local history remains visible |
| Permanent delivery failure | Persist `captureGap`; takeover stays disabled for this session and Pi owns compaction |
| Commit fails or is skipped | Boundary is not advanced; pending token pressure is retained |
| Overview not ready | Persist this archive and frozen boundary; later turns and the next prompt read it once each, without another commit |
| Archive terminal (`.done` / `.failed.json`) without an overview | Drop the pending archive and spend its frozen token pressure; the boundary stays where it was |
| Handler budget nearly spent | Postpone the commit to a later turn |
| Frozen boundary left the active context while waiting | Discard the pending boundary and leave local history with Pi |
| Boundary not in the active context (other branch, pi compaction) | Send the full context; keep the boundary for a return to that branch |
| Compaction takeover fails | Returns nothing; pi's default compaction proceeds |

Successful ordinary and compaction summaries share a recovery footer when the
required MCP tools are active. It names the exact `archive_uri`, explains that
the source contains captured historical messages rather than an unfiltered Pi
transcript, and directs the model to `openviking_list` plus paginated
`openviking_read`. The footer is appended after overview truncation so the
summary budget cannot remove the recovery entry point.

#### Live gate

`scripts/e2e-live.sh` drives a real pi binary, a real OpenViking server and a real LLM endpoint (its required and optional environment variables are documented at the top of `scripts/e2e-live.mjs`). It runs three `pi -p` / `pi -c` turns with a tiny takeover threshold and asserts that the third provider payload carries `[OpenViking Session Context]` while the padding from the first turn is gone from the raw conversation history. The third turn also calls a built-in tool, lists the archive with `openviking_list`, and reads `messages.jsonl` with paginated `openviking_read` arguments before recovering the archived detail. Nothing in the unit suites covers that end to end, so it stays a manual gate.

Between the two, `tests/takeover-session-manager.test.mjs` drives the core against pi's real `SessionManager` — takeover's own state entries between turns, a pi compaction, `/tree` away from and back to the covered branch — and checks which prompts actually go out trimmed. It resolves pi from a local or global install (`PI_SESSION_MANAGER` overrides) and skips when none is found.

### MCP tools

The official `@modelcontextprotocol/client` SDK handles Streamable HTTP, initialization, JSON-RPC request pairing and cancellation. The small bridge owns a single connection and its startup promise. It does not run the shared stdio proxy or maintain a protocol implementation.

`tools/list` supplies the catalogue, descriptions and input schemas. `tools.ts` registers names with the `openviking_` prefix once per session. SDK Ajv validation runs in `prepareArguments` before pi can coerce values or discard optional nulls; valid arguments are passed through unchanged. The original schema, including `additionalProperties`, is preserved. No repair rules or hand-written catalogue are maintained.

The bridge reuses shared configuration resolution and `buildOvHeaders`, including the same actor peer as REST recall. It reads configuration before connecting or calling a tool. Changes to the endpoint or request headers replace the connection. A failed transport is discarded for the next call; the failed call is never replayed. Protocol and tool errors do not cause reconnects. Tool registrations remain fixed until the next pi session.

Initialization, the initialized notification and tool discovery share a 5-second deadline. Calls share their configured `timeoutMs` budget with any necessary connection attempt. Cancellation stops local waiting; it does not guarantee that a server-side write was cancelled. Closing the client releases the transport. OpenViking's MCP endpoint is stateless, so no remote session cleanup protocol is needed.

`mcp-result.mjs` maps MCP content to pi's text/image blocks, avoids duplicate structured text and limits all result text together to 50 KiB / 2000 lines. MCP errors are reported as failed tools. No `promptSnippet` or `promptGuidelines` is added: tools discovered during `before_agent_start` would otherwise change pi's cached prompt prefix on the following turn. The existing prompt line names the registered tools immediately.

The extension's coexistence marker is owned by its instance and removed on shutdown, including reload. A pending startup cannot restore the marker or register tools after shutdown. MCP startup failure leaves REST recall, sync and takeover available.

The npm client version is pinned in `package.json` and `package-lock.json`. Installation runs `npm ci` and imports the client in the staging directory before replacing an existing extension. Marketplace archives contain the lockfile, not `node_modules`.

### index.ts

The entry point. It loads the config, returns immediately when disabled, constructs the modules, and registers handlers.

| Event | Action |
|---|---|
| `session_start` | Kick off startup without awaiting it |
| `before_agent_start` | Await startup, queue the prompt for recall, compose system-prompt additions |
| `context` | Run the pending recall, apply the takeover transform, inject recall |
| `tool_call` | Redirect host file tools that were handed a `viking://` URI |
| `tool_result` | Append a notice to a `bash` result whose command carried a `viking://` URI |
| `turn_end` | Sync the branch, feed the token estimate to takeover, update the status line |
| `session_before_compact` | Takeover compaction, or a commit plus a fresh overview |
| `session_shutdown` | Close the MCP bridge, then persist takeover state or commit one last time |
| `agent_end` | Invalidate the recall cache |

**Two guards.** The bypass check runs the shared `isBypassed` against the cwd, so a scratch directory never pollutes long-term memory; the pattern syntax is the shared one, identical across harnesses. The health check runs once — if the server is unreachable the extension stays disconnected for the whole session, every handler returns early and no tools are registered. No retries, no repeated warnings. A bypassed directory never opens the bridge at all: bypass means this directory does not touch OpenViking.

**Startup is memoized, not awaited.** The startup chain — health check, session derivation, pending replay, profile build, takeover restore, tool registration — costs a couple of seconds against a remote server, and `session_start` does not await it, because that delay would land on every pi launch. `before_agent_start` awaits the same in-flight promise, so the first turn still gets its profile and recall. That is also the only startup path a `pi -c` continuation has: pi does not fire `session_start` for one. The MCP handshake is started right after the health check so it runs alongside the pending replay, the profile build and takeover recovery, and is joined at the end of the chain; when it failed, a separate branch in `before_agent_start` retries it once per turn until the session has tools. The list is never refreshed after that: pi has no `unregisterTool`, and adding or removing a tool mid-session rewrites the prompt's tool section and invalidates the provider's cached prefix. With the shared `mcpEnabled` key set to `false` there is no bridge at all, and the status line does not report that as a failure — it is what was asked for.

**System prompt.** `before_agent_start` appends the profile block built by the shared `profile-inject.mjs` and capped at `profileTokenBudget`; outside takeover, the archive overview cached at resume or after a pre-compact commit; and one line naming the OpenViking tools. That line is generated from the names that actually registered, so it can never promise a tool the server does not have, and it is omitted entirely when the handshake produced none. Under takeover the overview reaches the model through the `context` hook instead, so it is not appended twice.

**Tool guard.** `guardVikingUriToolCall` (`lib/uri-guard-adapter.mjs`) watches for a `viking://` URI handed as a path to a host file tool that cannot read one — `read`, `grep`, `find`, `ls`, `write`, `edit` — and blocks the call with the equivalent `openviking_*` invocation spelled out, written to be valid against the server's own schemas. Without it the model burns turns on a file path that does not exist on disk. A grep `pattern` is search text, not a path, so grepping a local tree for `viking://` is not blocked. `edit` is handed only its `path`: pi carries the replacement text in `edits[].oldText/newText`, which are not in the shared guard's content-key allowlist, so the generic sweep would read them as locations and block any edit whose new text merely mentions a `viking://` URI — which fires the moment somebody edits this repository's own docs. `bash` is not blocked either: a URI in a command is as often data (an `ov` argument, an HTTP payload, a search pattern) as a path the model hoped to open. The command runs, and on `tool_result` `noticeVikingUriToolResult` appends a text block to its output that names `openviking_read` / `openviking_search` and tells the model to ignore the notice when the URI was intentional. A tool absent from the hint table is never guarded, which is why the `openviking_*` tools themselves need no allowlist.

**Surface.** The status line reports connection, entries added on the last turn, and either takeover coverage against its threshold or the plain commit threshold. `/viking` prints that same state; `/viking commit` forces a flush and commit, which under takeover also advances the boundary.

## Event flow

**Session start.** Bypass check, then health check; on failure everything after this is a no-op. Open the recall ledger, derive the OV session id, replay anything the pending queue still holds. Build the profile block. With takeover on, restore the boundary from the branch and hand `SyncManager` its watermark; with takeover off, fetch the archive overview for resume rehydration instead. Register the tools.

**Per prompt.** `before_agent_start` awaits startup, records the prompt for recall without any I/O, and returns the composed system prompt. Pi renders the user message. Then, for each LLM iteration, the `context` hook fires: the first one runs the search, the takeover transform replaces covered history with the overview message, and recall is injected — fresh into the newest user message, replayed from the ledger into the older ones. `agent_end` clears the cache.

**Per turn.** `turn_end` extracts everything past the watermark from the branch, sanitizes and filters it, sends it as one batch (or queues it), advances the watermark, and estimates the tokens it just synced. Outside takeover, that is followed by a threshold check against the server's `pending_tokens`. Under takeover, the estimate is added to the pending pressure, which may trigger a flush, commit and boundary advance.

**Pre-compact.** Under takeover, pi is handed the OV overview as its compaction summary, or nothing at all if any step failed. Outside takeover, the extension commits so that content pi is about to rewrite is preserved as an archive, then caches the new overview for the next system prompt.

**Shutdown.** Flush, then persist takeover state, or commit one last time when takeover is off.

## Two decisions worth recording

**The memory index was superseded, the reasoning behind it was not.** An earlier draft of this design specified an `index_builder.ts` that would keep a browsable table of contents of `viking://` in the system prompt; it was superseded by `shared/profile-inject.mjs`, whose profile block is folded into `systemPrompt` instead. The block stays a listing rather than memory content for the reasons the index was a listing: a map costs a fixed, small token budget, stays relevant to every turn instead of one, and is never stale in the way a copied memory is. Recall is the flashlight that fetches content for the turn at hand; the block is the map that tells the model what there is to fetch.

**Capture works on whole turns, not on individual tool calls.** `turn_end` hands `sync.ts` the finished branch, and that is what gets mirrored; `tool_call` is only ever consulted by the URI guard. A turn carries the user's intent and the assistant's conclusion together, which is what a memory needs, while a single tool call carries neither, so intercepting them one by one would have produced many fragments and no memories.

**Token estimates are CJK-aware.** `estimateTokens` counts codepoints at or above U+3000 as 1.5 tokens and everything else at a quarter of a token. Flat chars/4 undercounts CJK by four to six times, which silently turns a 3000-token overview budget into a few hundred real tokens of Chinese. It overcounts CJK slightly, which is the safe direction for a budget.
