# OV session context

Native sessions retain provider-owned transcripts and compaction. A fresh native
session (including a hard runtime switch) receives OpenViking's recent raw
messages on the first real inbound turn. Older history stays in OV archives,
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

`agents[].ovAutoRecall` defaults to `false`. When enabled the daemon recalls
(`ov-recall.mjs`), the same for every runtime: for a direct chat, a line that
@mentions the agent, a document comment, or an event outside chats such as
mail, one context search (`/api/v1/search/search`, mode `context`) runs on the
ingress path before the line reaches the runtime, with the sender's peer as
the actor peer (none for the owner). `agents[].ovRecallScope` `all` (default)
searches every peer and ranks the sender's first; `actor` searches only the
sender's and the owner's own. The sender's peer profile, which search never
returns, goes first. Each `<memory>` says whose it is (`who=` owner, sender,
or another person). Once OV has this epoch's session the search names it, so
OV's dedup ledger (a file of its own, not a session message) keeps entries
from coming back turn after turn. The search is bounded at 3 s; on a timeout
or a failure the line goes on without recall. It does not control this
session transport.

This session transport is the only writer of the conversation. In a runtime
the daemon starts, the OpenViking plugin writes nothing and recalls nothing on
its own: auto-recall, auto-capture, commit-on-compact, Pi's compaction
takeover (Pi compacts natively) and the workspace-derived peer are off, and its
session state, queued writes and recall ledger live under
`<dataDir>/openviking/`, so its session-start sweep never commits or replays
sessions of the owner's own Codex / Claude Code / Pi through this instance's
identity. All of it is process environment of the instance
(`openVikingAgentEnv` in `deployment-config.cjs`), which the plugins rank
above their config files; the owner's own clients are unaffected. Their
session-start injection and MCP tools stay.

Each cognition epoch owns a stable, private-to-this-daemon OV session. Switching
providers keeps that session; `/new` starts a new epoch. Existing local ledger rows
are uploaded on first use, in sequence. Only observable inbound Connector events,
outbound actions (including uncertain outcomes), and controls are mirrored.
Each message says who said it, because memory extraction files what a `user`
message says under its speaker (`ovAttribution`). The instance is an OV user of
its own and its owner speaks as that user: the owner's messages are `user`
messages without a peer. Anyone else, people and other bots alike, is a peer:
their messages are `user` messages with a message-level `peer_id` named after
the channel identity they came with (`ov-peer.mjs`, decided at ingest, when the
Connector's owner is known), such as `lark-ou_…`, `slack-<team>-<user>` or
`email-<address>`. A mail's From address is not verified, so a forged one only
writes under that address's own peer. The same person on two channels is two
peers. Outbound rows, control rows and the agent's own reminders (events
without a sender) are `assistant` messages: context, never the owner's words.
Rows recorded before peers were are mirrored as plain `user` messages. The
server must keep peer memory on (`memory_policy.peer`, the default); with it
off, OV skips a peer's messages rather than filing them under the owner.
Native reasoning and arbitrary tool results are not session archives.
An outbound row still `PENDING` holds the sync cursor until it settles; a
handoff prepared meanwhile carries the rows before it.

`ov_session_sync` binds each epoch's session to the namespace the connection
writes to (server, and the account and user OV authenticated; rotating a key
for the same user keeps it), and persists the acknowledged sequence, uncertain
append intent and finalization watermark. Recall and the archive tool use only
sessions of the current namespace (`ovSessionFor`). Changing the connection's
identity starts the current epoch over in a fresh session, replaying its journal;
an old identity's pending write is never reconciled against the new connection,
and older epochs and archives stay under the identity they were created in. At
first binding, a row from before rows were bound is this namespace's only if its
session is found here under the same URI; any other is logged once
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
tokens decide commits of the active epoch, and a commit with an unknown outcome
is safe to repeat, because one that landed leaves nothing pending. A commit
runs only after a complete sync, so an uncertain append is reconciled before it
can be archived.

After `/new`, the idle checkpoint also syncs retired epochs and commits them in
full (`keep_recent_count=0`), even below the token threshold or if they were
never mirrored, so a short conversation still reaches memory extraction. The
watermark advances only after OV acknowledges the archive (retrying a lost
response commits an empty session, a no-op); failures are retried on later
checks and after restart, and logged with their cause
(`ov_epoch_finalize_failed`). Retired epochs have a queue of their own, started
after the current epoch's queued work: OV answers a full commit only once the
session is archived, and the history a fresh session needs after `/new` never
waits for that. One session that times out or fails does not hold up the next;
only when OV cannot be reached at all do the retired epochs wait for the next
check. This protocol assumes exclusive
daemon ownership of these sessions: do not append or commit them from another
client.

A write timeout preserves its source ID. Before retrying, the next sync scans
OV live messages and avoids re-appending an observed ID. OV currently has no
idempotent append API: an original write arriving after reconciliation can still
duplicate a source. This is not an exactly-once remote transport guarantee.
External deliveries (Lark, Weixin) use the Core `outbox` idempotency ledger
independently.

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
