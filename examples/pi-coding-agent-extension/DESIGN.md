# Pi OpenViking Extension — Design

The extension gives a [pi](https://github.com/earendil-works/pi) session long-term memory and, when takeover is on, lets OpenViking own the session's committed history. It is a directory of TypeScript modules loaded directly by pi's `jiti` transpiler: no build step, no dependencies beyond what pi already provides, no MCP server. Everything reaches OpenViking over its REST API.

README.md is the operator's document — installation, every configuration knob, the tool list. This one is the maintainer's: what each module is responsible for, how the pieces meet at pi's event boundaries, and the reasons behind the choices that the code cannot state for itself.

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
├── tools.ts      # the seven model-facing viking_* tools
├── index.ts      # entry point: event handlers and the /viking command
├── package.json  # name and version; pi loads index.ts regardless
├── lib/          # pi-specific logic kept out of the event handlers
├── shared/       # generated copy of memory-plugin-shared/lib
├── scripts/      # live e2e harness
└── tests/        # node --test suites
```

## Modules

### config.ts

Defines `OVConfig` and resolves it once at load time. Knobs come from `resolveSettings("pi", { cwd })`: every one of them is declared in `shared/config-schema.mjs` and resolved through the same layers as every other harness — environment, the workspace's `.openviking/config.json` and machine registry entry, `ovcli.conf`'s `plugin.pi`, `ovcli.conf`'s `plugin`, then the schema default. The extension has no configuration file of its own. It used to ship one holding exactly the code defaults, which made an operator's choice indistinguishable from a factory setting.

Credentials — server URL, API key, account, user — come from `resolveOpenVikingCredentials(process.env, "pi")`, and `resolveAuthMode` decides between `trusted` and `api_key`, which is what gates whether identity headers go on the wire at all. The peer identity is resolved twice on purpose: `resolvePluginPeerId` picks the id from the configured layers, then `resolveEffectivePeerId` maps it onto the workspace and also returns `legacyPeerId`, the pre-git workspace id that recall still has to reach.

Two older spellings are kept alive because setups depend on them: `bypassPatterns` holds the same list as the shared `bypassSessionPatterns`, and `OV_DEBUG_LOG` is read alongside the shared `OPENVIKING_DEBUG_LOG`. `EXTENSION_VERSION` reads `package.json`, the same manifest the release gate watches for a bump, and feeds the shared `User-Agent` builder.

### client.ts

The transport is built once in the constructor by `createOvHttp` from `shared/ov-http.mjs`, and `fetchJSON(path, init, timeoutMs)` is the thin call into it. Nothing throws: callers branch on `ok` over the same `{ ok, result, status, error, traceId }` envelope every harness reads, and a transport failure arrives as `status: 0`. The trace id is lifted out of whichever place the server put it so that failures can be correlated in the server's own logs.

Headers are built per request: `Authorization: Bearer` when a key is configured, `X-OpenViking-Actor-Peer` for peer scoping, the shared `User-Agent`, and `X-OpenViking-Account` / `X-OpenViking-User` only when `sendIdentityHeaders` says the deployment is in trusted mode. Timeouts follow the class of call — 5s for health and session metadata, 10s for reads and message writes, 30s for commit and resource ingestion.

Above that sit thin methods for the endpoints this extension and its tools need: health, session metadata, session context, message append, commit, `search/find`, the three content tiers (abstract, overview, read), `fs/ls`, `fs/stat`, delete, and resource ingestion. Commit is exposed twice because two callers need different things from it: `commitSessionResponse` returns the whole envelope so a failure can be logged with its status and trace id, `commitSession` returns just the result.

### recall.ts

`RecallManager` runs one search per prompt and injects its block into the provider's view of the conversation.

The split between queueing and searching is what keeps recall off the UI path. `queueSearch(prompt)` runs in `before_agent_start` and only records the text. The first `context` hook of the turn calls `searchPending()`, which performs the search — by then pi has already rendered the user's message, so recall latency delays the model request but never the screen. Later LLM iterations within the same turn reuse the cached block, and `agent_end` invalidates it.

The search itself is `buildRecallBlock` from the shared `recall-core.mjs`; quotas, scopes, budgeting and formatting are shared with every other harness. The extension supplies three things of its own: the actor peer id, the legacy peer id (under `actor` scope the effective peer is the only one asked, so a workspace whose id changed would otherwise lose everything written before the change), and the OV session id, which is what turns on server-side query expansion and the cross-turn dedup ledger. A query shorter than `minQueryLength` skips the round trip entirely.

Injection is two-pass, and that is the interesting part. Historical user messages get back the exact block the recall ledger says was sent with them; only the newest user message receives this turn's fresh block, which is then recorded. The result is a request prefix that stays byte-identical across turns, so the provider's prompt cache keeps hitting instead of being invalidated by every new memory. The ledger keys on pi's entry id plus the message's original text — entry ids survive compaction and branch navigation, where an ordinal would not. When the host does not expose entry ids the pass fails closed: fresh recall still reaches the newest message, but nothing historical is replayed or recorded under an unstable key. A message that already contains `<openviking-context` is never injected into twice.

### sync.ts

`SyncManager` owns the OV session id, the capture watermark, and everything between a pi branch and a committed archive.

The OV session id is `pi-<pi session id>`, derived locally by the shared `deriveHarnessSessionId`. Nothing round-trips to open a session: the id is deterministic, so the first write can carry it.

**Capture.** `turn_end` hands the whole branch to `extractBranchCapturePayloads` (`lib/capture-adapter.mjs`), which takes the entries past `syncedEntryCount`, normalizes roles, renders tool parts with bounded input and output, and decides entry by entry whether to capture. There are two decision modes. Normally it is the shared `shouldCaptureText` heuristic. Under takeover it is a faithful mode that drops only empty text, slash commands and OpenViking's own status messages: once the boundary advances, a short acknowledgement may be represented to the model *only* through the archive overview, so discarding it as low-signal would lose it outright. A branch shorter than the watermark means pi navigated to a different branch, so the watermark resets to zero and the branch is re-extracted from the start.

**Delivery.** Payloads leave in one batched request through the shared `sendSessionMessages`. Retryable failures are written to the shared disk pending queue and replayed later. A non-retryable rejection counts as accepted, so the watermark advances past payloads the server will never take rather than re-sending them every turn. The watermark only moves when the whole extraction was accepted.

**Backlog drain.** `flushForTakeover` is the barrier takeover waits on, and it needs the queue empty for this session. The shared `replayPending` sends one request per entry and stops after a single replay window, which is what let a large offline backlog hold the barrier closed for many turns, so sync drains this session's own queued `addMessage` entries through the batch endpoint, `BATCH_LIMIT` per request, claiming one batch at a time so a failed batch costs only the retries of the entries it held. The drain is bounded by wall time (`OPENVIKING_PENDING_DRAIN_BUDGET_MS`, 60s by default) and optionally by batch count, so a huge backlog cannot block `turn_end` indefinitely; the remainder drains on later turns.

**Commit.** Outside takeover, sync asks the server for `pending_tokens` after each accepted turn and commits when it crosses `commitTokenThreshold` — server-side accounting, not a local estimate. A failed commit is queued for replay unless the caller passes `queueOnFailure: false`, which takeover always does, because a commit that lands later cannot justify a boundary that moved now.

### takeover.ts

A binding, not a mechanism. The state machine lives in `lib/takeover-core.mjs`, which is pure and unit-tested; `takeover.ts` supplies its I/O: flush and commit go to `SyncManager`, the archive overview comes from the session context endpoint, state is persisted through `pi.appendEntry`, and the watermark is read back from sync.

Context takeover makes OpenViking the authoritative long-term store for a pi session. Pi still keeps recent turns locally; committed history is represented to the model by OpenViking's archive overview through pi's `context` hook.

#### Model

| Field | Meaning |
|---|---|
| `coveredUserTurns` | Real user turns already covered by the archive overview |
| `overview` | Latest archive overview returned by the session context endpoint |
| `fingerprint` | Fingerprint of the last covered message, for branch-mismatch detection |
| `pendingTokens` | Estimated synced token pressure since the last successful advance |
| `lastSeenUserTurns` | User turns counted in the most recent `context` hook |
| `syncedEntryCount` | Pi branch watermark, restored across `pi -p` / `pi -c` processes |

State is persisted as a pi custom entry:

```ts
pi.appendEntry("ov-takeover", state)
```

At startup the extension scans the branch from the end for the newest such entry and restores both the boundary and `SyncManager`'s watermark, so a `pi -c` continuation does not resend branch entries OpenViking already has.

#### Runtime flow

1. `turn_end` captures new branch entries into the OpenViking session, falling back to the disk pending queue when the server is unreachable.
2. When `pendingTokens` reaches `takeoverTokenThreshold`, and there are more user turns than `takeoverKeepRecentTurns`, takeover tries to advance.
3. Advancing requires the flush barrier: every `addMessage` entry queued for *this* session must be delivered. Queued `commitSession` entries and entries belonging to other sessions do not hold it closed.
4. The commit runs with `queueOnFailure: false`.
5. The session context endpoint is polled until `latest_archive_overview` is available — `takeoverOverviewPollMax` attempts, `takeoverOverviewPollMs` apart. An empty overview is never injected; the boundary stays where it is and the token pressure resets so the next threshold crossing retries instead of re-committing every turn.
6. On success the boundary advances to `lastSeenUserTurns - takeoverKeepRecentTurns`.
7. The `context` hook then replaces every covered message with one synthetic user message beginning `[OpenViking Session Context]`, keeps the recent tail verbatim, and recall is injected into the newest kept user turn as usual.

The overview message's timestamp is derived from the first kept message, so the provider payload stays byte-stable between commits and can benefit from prompt caching.

#### Compaction

When pi emits `session_before_compact`, takeover runs the same flush → commit → overview sequence. On success it hands pi the overview as the compaction summary and resets the boundary, because pi's own compaction has absorbed it:

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
| Commit fails | Boundary is not advanced; pending token pressure is retained |
| Overview not ready | Boundary is not advanced; retried at the next threshold or by `/viking commit` |
| Branch fingerprint mismatch | Boundary resets to 0 and full history is shown until the next successful advance |
| Compaction takeover fails | Returns nothing; pi's default compaction proceeds |

#### Live gate

`scripts/e2e-live.sh` drives a real pi binary, a real OpenViking server and a real LLM endpoint (its required and optional environment variables are documented at the top of `scripts/e2e-live.mjs`). It runs three `pi -p` / `pi -c` turns with a tiny takeover threshold and asserts that the third provider payload carries `[OpenViking Session Context]` while the padding from the first turn is gone from the raw conversation history. Nothing in the unit suites covers that end to end, so it stays a manual gate.

### tools.ts

Seven tools registered on pi's model, all sharing the one `OVClient`. Each handler answers "OpenViking server is not reachable." when the startup health check never succeeded, so a down server degrades to a message rather than an error.

| Tool | Purpose |
|---|---|
| `viking_search` | Semantic search, optionally scoped to a `viking://` prefix |
| `viking_read` | Read one URI at `abstract`, `overview` or `full` detail |
| `viking_browse` | List a directory or stat an entry |
| `viking_remember` | Store a fact for cross-session persistence |
| `viking_forget` | Delete by URI, or by query when the match is strong |
| `viking_add_resource` | Ingest a URL into the knowledge base |
| `viking_archive_expand` | Expand an archived session back into detail |

Two of them are less direct than they look. `viking_remember` does not write a memory: it appends a `[Remember — <category>]` message to the live OV session, so extraction treats it like any other turn and the fact goes through the same pipeline as everything else. `viking_forget` by query deletes only when the top match scores above 0.8; below that it reports no strong match rather than guessing which memory to destroy. There is no `add_skill` tool — pi has a skill system of its own.

### index.ts

The entry point. It loads the config, returns immediately when disabled, constructs the modules, and registers handlers.

| Event | Action |
|---|---|
| `session_start` | Kick off startup without awaiting it |
| `before_agent_start` | Await startup, queue the prompt for recall, compose system-prompt additions |
| `context` | Run the pending recall, apply the takeover transform, inject recall |
| `tool_call` | Redirect host file tools that were handed a `viking://` URI |
| `turn_end` | Sync the branch, feed the token estimate to takeover, update the status line |
| `session_before_compact` | Takeover compaction, or a commit plus a fresh overview |
| `session_shutdown` | Persist takeover state, or a final commit |
| `agent_end` | Invalidate the recall cache |

**Two guards.** The bypass check runs the shared `isBypassed` against the cwd, so a scratch directory never pollutes long-term memory; the pattern syntax is the shared one, identical across harnesses. The health check runs once — if the server is unreachable the extension stays disconnected for the whole session, every handler returns early, and the tools say so. No retries, no repeated warnings.

**Startup is memoized, not awaited.** The startup chain — health check, session derivation, pending replay, profile build, takeover restore, tool registration — costs a couple of seconds against a remote server, and `session_start` does not await it, because that delay would land on every pi launch. `before_agent_start` awaits the same in-flight promise, so the first turn still gets its profile and recall. That is also the only startup path a `pi -c` continuation has: pi does not fire `session_start` for one.

**System prompt.** `before_agent_start` appends the profile block built by the shared `profile-inject.mjs` and capped at `profileTokenBudget`; outside takeover, the archive overview cached at resume or after a pre-compact commit; and one line naming the seven tools. Under takeover the overview reaches the model through the `context` hook instead, so it is not appended twice.

**Tool guard.** `guardVikingUriToolCall` (`lib/uri-guard-adapter.mjs`) watches for a `viking://` URI handed to a host tool that cannot read one — `read`, `grep`, `find`, `ls`, `bash` — and blocks the call with the equivalent `viking_*` invocation spelled out. Without it the model burns turns on a file path that does not exist on disk.

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
