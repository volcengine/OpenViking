# OV session context

Native sessions retain provider-owned transcripts and compaction. A fresh native
session (including a hard runtime switch) receives OpenViking's recent raw
messages on the first real inbound turn. After a `/runtime` that interrupted
work, that first turn is the interrupted inputs themselves, handed on by the
switch: the history ends before the first of them, and what the stopped turn
sent after it is quoted in the switch notice instead (usage reference). Older history stays in OV archives,
which the model reads on demand with `read_openviking_session_context`. There
is no SQLite history fallback and no local summary generator. Whether the
handoff carries enough history is not checked here; a fuller cross-runtime
handoff is expected from OV itself.

OpenViking is required. Installation and setup verify it with public health plus
a protected `GET /api/v1/system/status`, which passes the same identity check as
the session API at a cost that does not grow with the user's sessions. At daemon startup a config or credential
OV rejects (4xx) stops the daemon; an OV that cannot be reached yet only records
`system.ov_unavailable`, so boot order or a blip during a restart cannot use up
pm2's restarts. Any failure while preparing history (unreachable OV, an invalid
or malformed response) starts the fresh native session without history, without
stopping message delivery or sending a failure notice to chat. Cached history is
discarded on that failure; transport cursors and uncertain writes remain durable
and the idle checkpoint retries. Recovery does not inject history into an
already-running native transcript.

Recall (`ov-recall.mjs`) is the same for every runtime. For a direct chat, a
line that @mentions the agent, a document comment, or an event outside chats
such as mail, the daemon reads OV on the ingress path before the line reaches
the runtime and appends what it found: the owner's preferences (the owner's
standing instructions, which govern the agent everywhere), and when someone
else wrote the line, that person's preferences. Each person's preferences are
a directory the Core names and finds by key (`ov-person.mjs`), never by a
display name: `memories/preferences/owner/` for the owner on every channel,
`memories/preferences/<name>~<id>/` for anyone else, where `<id>` is 12 hex
digits of a hash of the channel identity, so namesakes stay apart and a
renamed person keeps theirs. A person's directory is read directly under the
name they have now; only someone renamed since needs a scan of the whole
tree, paged through to the end. Topics show newest first and whole, up to
3000 characters for the owner and 1500 for anyone else; the ones that do not
fit are named, with where to read them. With
`agents[].ovAutoRecall` (default `false`, a config-file option) one context
search (`/api/v1/search/search`, mode `context`) on the line also runs; once
OV has this epoch's session the search names it, so OV's dedup ledger (a file
of its own, not a session message) keeps entries from coming back turn after
turn. Everything else the model looks up with the Core's `openviking_*`
tools. Reads are cached ten minutes (dropped as soon as `openviking_remember`
changes a preference) and bounded at 3 s together; on a timeout or a failure
the line goes on with what arrived. No request carries a peer.

Skills follow vikingbot's model rather than a catalog of tools, and they
belong to the work rather than to every line. A line the router answers
carries none: it either replies or dispatches, and ranking skills for each
message would be a lookup nothing acts on. A task is where they matter, so
the worker's policy (assembled for each turn from the line that asked) carries
the skills OpenViking ranked for that line (`/api/v1/skills/find`, at most five
above 0.35) as `<skills source="openviking">`: the name, a 40-token
description, the SKILL.md to read and the tool that reads it, within 600 tokens
(whole entries while they fit, then names and locations only, then a count).
The model reads one with `openviking_read`. That read takes the package once
(`/api/v1/skills/{name}` with content, files and integrity, under OV's own
package lock), shows the page from that snapshot's SKILL.md, and writes the
files into `<dataDir>/skills/<user|agent>/<name>/<revision>`, naming the
directory in the result, so a skill that ships scripts can run them from the
same revision as the instructions read. That page is the same visible view a
plain read of the file returns: a credential the Privacy layer sealed stays the
reference it put there, never the restored value, and the local SKILL.md is
written from that text. The download is bounded (200 files, 1 MiB each, 20 MiB
in total), every file is verified against the manifest's SHA-256, files whose
bytes do not match are left out, OpenViking's own summaries are not part of the
package, and nothing is written outside that cache. A revision is marked whole
only when every file that could arrive is there: one that failed for a reason
that may pass is filled in by the next read, which keeps the files the earlier
attempt verified, and stays reported as missing until then, while one that can
never arrive (an unsafe path, past the per-file bound) is reported on every read
without being retried. A revision is cleared a day after it was last handed out,
so a directory a task is still using stays while it runs; one skill's cleanup
never touches another's, and this instance's `chart` and the account's `chart`
live in different directories. Nothing here installs, updates or removes a
skill: installs are `ov skills add` with `-p viking://~/skills` pinning this
instance's own root, and updates and removals are left to the user, because the
CLI resolves a bare name in this instance's root and then in the account-shared
`viking://agent/skills`, and it cannot tell who asked. A discovery that fails
leaves the policy without skills.

For substantive questions and project discussions, the local runtime's policy
asks the model to start with one focused `openviking_find`, reuse earlier
answers and decisions, and read useful hits before seeking more information
elsewhere. Evidence already retrieved for the same question is reused; greetings
and self-contained text transformations need no lookup. Relevant historical
context should help the answer, with no invented recollections or assumption
that old facts remain current. Privacy constraints already in context or found
with the topic are reused; missing constraints about other people require a
lookup because only owner/sender preferences are injected. This is model policy,
not an extra daemon search or a change to `ovAutoRecall`.

OpenViking SuperAgent owns OpenViking for the agents it runs. This session transport writes
the conversation, and its extraction keeps profile, entities and events, never
preferences: extraction decides whose a preference is from names, so it merges
namesakes and edits one person's file with another's request. Preferences are
written by `openviking_remember` alone, a file per topic in the directory of
the person who sent the message `decision_id` names (`/api/v1/content/write`,
removed with `DELETE /api/v1/fs`); the model gives the topic and the words,
never the person. Nothing else writes memory. A runtime's own OpenViking plugin is switched off where the runtime
is launched (`ov-isolation.mjs`; each runtime declares its mechanism as
`ovIsolation` in `runtime-catalog.mjs`): Codex and TraeX get config overrides
that disable the plugin, any MCP server that runs OpenViking and their own
memory feature; Claude Code gets one `--settings` layer that disables the
plugin and denies those servers; Pi resolves enabled user/project extensions
with its own package manager, then runs with discovery off and every non-OV
extension loaded by path, including configured paths and packages. Its built-in
coding tools, codemode, tool search and other MCP servers remain available;
OpenCode and Antigravity read a
OpenViking SuperAgent-owned config home with OpenViking's plugins, servers and the like left out. The OPENVIKING_*
process variables of the instance (`openVikingAgentEnv` in
`deployment-config.cjs`) are the second line: a plugin that loads anyway is
inert, and its state lives under `<dataDir>/openviking/`. The owner's own
clients are unaffected. How to add a runtime, and how to verify its switch
against the real CLI, is in [OpenViking belongs to OpenViking SuperAgent](openviking-ownership.md).

Each cognition epoch owns a stable, private-to-this-daemon OV session. Switching
providers keeps that session; `/new` starts a new epoch. Existing local ledger rows
are uploaded on first use, in sequence. Only observable inbound Connector events,
outbound actions (including uncertain outcomes), and controls are mirrored.
Every line someone sent is a `user` message, whoever sent it: its `from=`
names the speaker, and extraction files a person's preferences under that
name. No message carries a peer: the instance is an OV user of its own, and
everything it took part in is its own memory, so an event is kept once, not
once per participant. Outbound rows and the agent's own reminders (events
without a sender) are `assistant` messages: context, never anyone's words.
Control rows (slash commands and their notices) are not mirrored. Each session
is created with `memory_policy.memory_types` of `profile`, `preferences`,
`entities` and `events`, types every release with memory policies has:
extraction never writes the agent's own `soul` or `identity`, which a line
that talks the agent into a new name or a global rule would otherwise
rewrite. The agent's name comes from its configuration and its soul is a
file in OpenViking changed only on the owner's request; the owner's
standing rules are the owner's preferences. Sessions created before this
kept the server's default policy until `/new`.
Native reasoning and arbitrary tool results are not session archives.
An outbound row still `PENDING` holds the sync cursor until it settles; a
handoff prepared meanwhile carries the rows before it.

`ov_session_sync` binds each epoch's session to the namespace the connection
writes to (server, and the account and user OV authenticated; rotating a key
for the same user keeps it), and persists the acknowledged sequence, uncertain
append intent, pending asynchronous Task receipt and finalization watermark. Recall and the archive tool use only
sessions of the current namespace (`ovSessionFor`). Changing the connection's
identity starts the current epoch over in a fresh session, replaying its journal;
an old identity's pending write is never reconciled against the new connection,
and older epochs and archives stay under the identity they were created in. At
first binding, a row from before rows were bound is this namespace's only if its
session is found here under the same URI (or its exact session ID is verified
when the legacy row has no URI); any other is logged once
(`ov_epochs_left_behind`) and left alone. Older epochs that were never mirrored
are not imported: their identity is unknown.
SQLite's cognition journal remains the durable source for this
sync and the outbound delivery ledger; the `outbox` table guards the external
side effects themselves. Neither is read to assemble historical prompts. This
intentionally separates context storage from external side-effect reliability.

The daemon never uses OV's Working Memory. It is off by default since it became
opt-in after v0.4.23: a completed archive has no overview, and `/context`
returns only the raw messages of archives still being processed plus the live
messages, trimmed to the requested budget by complete turns, newest first.
Releases up to v0.4.23, among them the Volcengine hosted service (v0.4.20.x),
still write one per archive: `latest_archive_overview` in `/context`,
`.overview.md` / `.abstract.md` in the archive directory, and a `checkpoint`
message that `/context` inserts after a turn whose earlier part was archived
(its text kept in the archive's `.meta.json`). The VLM-written document only
grows. The daemon does not change OV's settings to stop it (a session-level
memory policy would replace the user's whole one); it drops the overview and
checkpoint messages from the handoff and from every tool result, so history is
raw messages on every release. A checkpoint (at most 1,024 tokens, one turn)
still counts against the context budget before it is dropped. The daemon disables OV auto-commit. At idle boundaries, once
OV reports 24,000 pending tokens, it commits with `retention_mode=turn_budget`,
keeping the newest complete turns, up to 24,000 tokens, live as raw messages.
Assembly requests 48,000 tokens, so the live session fits whole. OV's pending
tokens decide commits of the active epoch. A synchronous commit with a lost
response can be retried because one that landed leaves nothing pending; an
accepted asynchronous commit instead resumes its original Task. A commit
runs only after a complete sync, so an uncertain append is reconciled before it
can be archived.

After `/new`, the idle checkpoint also syncs retired epochs and commits them in
full (`keep_recent_count=0`), even below the token threshold or if they were
never mirrored, so a short conversation still reaches memory extraction. The
watermark advances only after OV acknowledges the archive (a completed Task or
the Core's `archived: true` result; background memory extraction need not finish).
An accepted Task is resumed, while retrying a lost synchronous response commits
an empty session, a no-op; failures are retried on later
checks and after restart, and logged with their cause
(`ov_epoch_finalize_failed`). Retired epochs have a queue of their own, started
after the current epoch's queued work: OV answers a full commit only once the
session is archived, and the history a fresh session needs after `/new` never
waits for that. One session that times out or fails does not hold up the next;
only when OV cannot be reached at all do the retired epochs wait for the next
check. This protocol assumes exclusive
daemon ownership of these sessions: do not append or commit them from another
client.

A write timeout without an acceptance receipt preserves its source ID. Before
retrying, the next sync scans OV live messages and avoids re-appending an observed ID. OV currently has no
idempotent append API: an original write arriving after reconciliation can still
duplicate a source. This is not an exactly-once remote transport guarantee.
External deliveries (Lark, Weixin) use the Core `outbox` idempotency ledger
independently.

## Synchronous and asynchronous session writes

`OVSessionClient` interprets the actual response, not the endpoint's domain or
version. A complete synchronous result returns immediately. `accepted: true`
(or HTTP 202) is only admission: a Task ID is required and queried through the
same authenticated `/api/v1/tasks/{id}` endpoint. Creation returns only after
the session's exact ID and URI have been verified by GET; an existing fixed ID
or `ALREADY_EXISTS` is recovered by that same check, never by constructing a URI.

Before polling, `ov_session_sync.pending_task` stores the operation, target
sequence and receipt, alongside the epoch and authenticated namespace. It is
cleared together with the acknowledged cursor or finalization watermark. Polls
are bounded (200 ms interval, by default the connection's 15 s timeout). Timeout,
query failure, malformed acceptance and missing Tasks do not advance cursors or
resubmit writes. Restart resumes the original receipt before a later write or
context read. Known terminal failures retain the journal intent for a later
retry and report a business error code without private server error bodies.

An expired create Task can be recovered by verifying the exact session. For an
append or commit whose Task disappeared, the result remains unresolved: absence
from live messages does not prove a queued append failed, and the client cannot
prove a specific commit from an empty session alone. Restore Task visibility or
reconcile the remote operation with explicit evidence before clearing its
receipt. This conservative behavior prevents blind duplicate submission; it
does not claim an exactly-once guarantee for a lost HTTP response before any
receipt was obtained. No server dispatch settings or SDK replacement is needed.

Pending appends are resumed before choosing the next journal row, even if their
source was cancelled by normal temporary-reaction cleanup or the journal head
has moved backwards. Resume uses only the saved receipt; it never re-posts a
cancelled message. Completion clears the receipt and acknowledges its original
sequence atomically. Retired epochs with outstanding receipts remain eligible
for recovery even when all their journal rows have been cancelled. A failed Task
is reported without advancing the cursor; subsequent sync can then skip the
cancelled source and continue with later messages.

Regression tests: `node --test packages/daemon/test/ov-session-*.test.mjs`
from `superagent/`.

`read_openviking_session_context` exposes read-only context, archive listing,
archive reading (the original messages) and grep. Archives are read from their
stored `history/<archive>/messages.jsonl`, not OV's archive API, which adds the
Working Memory where a release writes one, answers 404 up to v0.4.23 for an
archive without it, and skips archives still being processed. Listing drops the
Working Memory abstract; grep keeps only matches in `messages.jsonl`. Character pagination includes
an expected digest to reject mixed pages when a result changes. Archive content
grants no action permission. The API uses the instance-pinned OV config and
redacts remote errors.

The implementation targets the local OpenViking session API inspected at
`c01bb34d` (Working Memory off by default, `turn_budget` retention) and was
checked against the hosted service at v0.4.20.6 (Working Memory on). Before
rollout, verify the deployment supports `/sessions`, `/context`, `/commit` with
`retention_mode`, source_message_ids and the content read endpoint.
Unit tests use an isolated OV client; production OV data is not mutated by tests.
