# Personal Agent Connector protocol 0.2

The Core owns one agent runtime session and one OpenViking space. Connector
processes own provider credentials, receive loops, provider state, and external
side effects. A Core may start more than one Connector and routes all accepted
events into the same runtime session.

The protocol uses newline-delimited JSON over child process stdin/stdout. Stdout
is reserved for protocol messages; Connector logs go to stderr. Every message
contains:

```json
{
  "protocol_version": "0.2",
  "type": "REQUEST"
}
```

## Manifest and lifecycle

The Core starts a Connector and calls these operations in order:

1. `spec`: return the versioned manifest.
2. `check`: validate credentials, configuration, and provider availability. The
   result may name the owner, the person this agent works for, as seen on this
   Connector: `owner: { id, target }`, where `id` is how the owner appears as an
   event actor and `target` is the `message.send` input that reaches them in a
   direct chat (Lark: the profile's logged-in user, `{ user_id: "ou_..." }`;
   Weixin: the account that scanned the login code, `{ chat_id }`;
   Telegram: the account verified by the one-time Start link, `{ chat_id }`;
   QQ: the account verified by the one-time private-chat code, `{ chat_id }`;
   Slack: the person who sent the one-time code to the bot, `{ chat_id }` of
   that direct message). A mailbox reports none. It may also name the bot
   itself: `self: { id }`, where `id` is how the bot appears in an inbound
   event's `message.mentions` (Lark: the app's bot `open_id` from
   `/open-apis/bot/v3/info`). The Core then marks each chat input whose
   mentions include anyone with `mention=me|other|mixed` by id, never by display
   name; without `self` the field is left out. A failed lookup reports no
   `self` rather than failing `check`.
3. `start`: begin receiving events.
4. `action`: execute a typed external action.
5. `pause`: stop event intake while keeping actions available for Core shutdown draining.
6. `stop`: drain and exit.

Each Connector fails on its own. When a worker exits unexpectedly, or fails
`check` or `start`, the Core restarts it from `spec` with backoff (5 s, 15 s,
1 min, then every 5 min) while the runtime and the other Connectors keep
running. It counts as recovered after 30 s of running, and its backoff starts
over. A Connector that cannot go on (an expired login, a lost long connection)
should send `STATUS failed` with a short `detail.error`, then exit; the Core
shows that reason in Activity. Actions for a Connector that is down are refused
before any outbox reservation (`connector_unavailable`). Startup fails only
when no Connector passes `check` and `start`.

Example manifest:

```json
{
  "protocol_version": "0.2",
  "name": "weixin",
  "version": "openclaw-weixin-protocol/2.4.9",
  "capabilities": ["events", "actions"],
  "actions": {
    "message.send": { "content": ["markdown", "file"] },
    "message.reply": { "content": ["markdown", "file"] }
  },
  "queries": ["message.media"],
  "auth_schema": { "type": "object", "additionalProperties": false },
  "config_schema": { "type": "object", "additionalProperties": false }
}
```

Capabilities are optional modules:

- `events`: subscribed, polled, or webhook events that may wake the agent.
- `actions`: external side effects such as sending a message or creating an event.
- `search`: on-demand reads requested by the agent.
- `sync`: bulk or incremental records destined for OpenViking.

`actions` declares the outbound operations the Core may route to this
Connector. `content` lists the message bodies an action accepts (`markdown`,
`file`, `file_key`, `card`); `mentions` and `thread` opt into those Lark
features; `idempotent: true` means the provider honours the idempotency key, so
the Core may retry an `UNCERTAIN` attempt with the same key; `approval: true`
means the action speaks for the owner (an email reply), so the Core never runs it
for the agent: it shows the draft to the owner on the primary IM and runs it
only when the owner confirms it there. `conversations` narrows an action for
targets in one type of conversation: it maps the conversation type to the spec
fields that differ there, or to `null` when the action does not reach it (the
Lark Connector's `doc_comment` documents take a plain-text reply that is never
retried, and no `message.send`). `queries` lists the
read-only operations. The Core offers an agent tool only when some configured
Connector declares the operation behind it, and checks every call against the
target Connector's declaration.

Version 0.2 implements `events`, `actions` and `queries`. Future `search` results should
reuse the team's normalized Document shape. Future `sync` implementations
should reuse `spec/check/discover/read` and `RECORD/SCHEMA/STATE` semantics.

## Event envelope

```json
{
  "protocol_version": "0.2",
  "type": "EVENT",
  "id": "issue-opened-delivery-123",
  "connector": "github",
  "instance": "github-personal",
  "kind": "issue.opened",
  "occurred_at": 1791374400000,
  "subject": { "type": "issue", "id": "owner/repo#123" },
  "actor": { "id": "provider-user-id", "type": "user" },
  "content": {
    "title": "Review requested",
    "text": "Please investigate this issue.",
    "attachments": []
  }
}
```

`connector` identifies the provider; `instance` identifies a configured source
instance/account and is unique within a Core. Built-in chat connectors currently
use `instance=lark`, `instance=weixin`, `instance=telegram`, `instance=slack`, or `instance=qq`. The source process authenticates both
identities; input cannot select another Connector's identity.

`id` is required and stable across retries. The Core deduplicates on
`(instance, id)`, not a globally unique message id. An id identifies an event
occurrence, whereas `subject` identifies the object it concerns. For example a
Calendar Connector can use `reminder:meeting-42:2026-10-09T10:00Z:10m` for one
scheduled reminder. Redelivery keeps that id; another occurrence or reminder
rule uses a different id. Do not generate a new UUID or use receive time on
every delivery. Provider-native stable event ids are preferred when available.

`occurred_at` is UTC epoch milliseconds. `subject.type` and `subject.id` are
required; `actor`, `content.title`, `content.text`, and `content.attachments` are
optional. `actor.name` is an optional display name as the Connector saw it: an
untrusted label the Core shows as `from=` for connectors other than Lark (whose
names come from the chat roster). An actor of type `bot` or `app` is another
bot or app, shown to the model as `sender=bot`. Each attachment is a reference with `id` and `type`, not inline bytes.
An event such as `reminder.due` can have no actor or text and still wake the agent.

Chat events additionally carry optional structured fields:

```json
{
  "conversation": {
    "id": "provider-chat-id", "type": "p2p",
    "tenant_id": "provider-account-id", "thread_id": null
  },
  "message": {
    "id": "provider-message-id", "type": "text",
    "parent_id": null, "root_id": null, "thread_root_id": null, "mentions": []
  },
  "reply_route": {
    "action": "message.reply", "input": { "message_id": "provider-message-id" }
  }
}
```

`conversation.external: true` marks a conversation that includes people from
outside the owner's organization (a Slack Connect channel); the model sees it as
`external=true`.

A missing reply route means no automatic reply target exists; it does not mean
that processing failed. A subject or actor id is never inferred to be a send
target. Notifications to a different Connector need a configured or explicitly
authorized destination. Choosing silence is independent of having a reply route.
A reply route may also belong to a non-chat event (for example `issue.comment`);
the Core invokes that action on the event's instance with `input` plus a
markdown `content` body, through the same outbox as agent deliveries.
Failure-notice muting uses the event scope, and its outbound history keeps the
source, subject and action without inventing a chat.

Lark's Connector decodes message, card-action, and meeting envelopes. Weixin,
Telegram, QQ and Slack produce canonical chat events directly. The Core validates and
consumes only the fields above, plus optional typed extension `data`. Provider
raw envelopes are not required, forwarded to the runtime, or persisted. A
`provider_event` diagnostic field is ignored. Protocol 0.1 is rejected.

## Persistence and runtime input

`connector_events` is the single inbound ledger for every source. It stores the
canonical envelope and nullable chat indexes, with a unique `(connector_instance,
event_id)` constraint. There is no separate `inbound` message table or old-schema
migration. Use a fresh data directory for this pre-release schema change.

Generic runtime inputs retain the event semantics:

```text
[source=github instance=github-personal kind=issue.opened event=delivery-123 subject=issue:owner/repo#123 time=2026-10-09T10:00Z] title and body
```

Chat input keeps `chat=`, `msg=`, thread, mentions and reply context, and also
carries `event=`. A non-chat event that can itself be answered (a mail) carries
its reply target as `msg=` as well:

```text
[source=email instance=email-gmail kind=email.received msg=mail:3f2a9c1b:1:18475 event=imap:3f2a9c1b:1:18475 subject=thread:gmail:1878… time=2026-10-13T08:17Z actor=email:alice@example.com] subject ⏎ From: … ⏎ [mail stored: viking://user/…/resources/emails/3f2a9c1b-1-18475/body.md; the first 500 of its 4210 characters are below] ⏎ [attachment 01 report.pdf (application/pdf, 1.2 MB): viking://…/attachments/01-report.pdf] ⏎ start of the body…
```
The email Connector stores each mail in the owner's private OpenViking area
before it reports it, so a mail line carries links and the start of the text;
the extension field `data.archive` records where, and the Core's `read_email`
tool reads it back. This is the email Connector's own use of OpenViking, not
the future `sync` capability.

The Lark Connector of the one instance that stores meeting notes
(`agents[].meetingNotes`) likewise writes each AI note of the owner's meetings
to OpenViking (`viking://user/<user>/resources/meetings/<meeting_id>/`), found
by scanning the owner's meetings every 15 minutes. Nothing of it becomes a
protocol EVENT or reaches the Core; the Core's `read_meeting_note` tool reads
the stored notes.

An optional `instance=` is included when it differs from
`source=`. Tag values percent-encode whitespace, brackets and `%`, so an id
copied from a tag decodes back to the exact id. Untrusted content is
collapsed onto one physical line. Journal-to-OpenViking synchronization uses the
same formatter, so native session recovery preserves event identity and subject.

The Harness applies shared validation, filtering, persistence, and admission to
all events. Only replyable direct text/post chat messages can execute slash
commands. Only message events in a group are burst-batched; subject events are
submitted immediately or queued when the runtime cannot accept them. Lark roster
lookups require a Lark conversation. Failure retry retains the canonical event;
an event without a reply route records `NO_TARGET` instead of attempting an IM
reply. Specialized card and meeting handlers consume normalized extension data.

The generic event contract does not provide an inbound ACK/replay guarantee.
Connector cursor durability and Core acknowledgement are separate work; the
current Weixin and Telegram receive loops checkpoint after emitting events to stdout.

## Actions and the outbox

Every outbound action, whichever runtime asked, goes through one Core service
and one SQLite `outbox` table:

```
agent runtime ── MCP adapter (Codex, TraeX, Claude, Pi) ──> <dataDir>/actions.sock
                                                   │ Core ActionService
                                                   │  runtime authority
                                                   │  route to a Connector
                                                   │  freshness fence + attribution
                                                   │  content + manifest checks
                                                   │  outbox + journal reserve
                                                   ▼
                                                 Connector `action` request
```

The adapters only relay `tools/list` / `tools/call` (`GET /v1/tools`,
`POST /v1/call`) with the runtime's authority token; neither validates nor
executes. The socket is mode `0600`. Agent runtimes never open a Connector
state database or call a provider API to send messages.

The MCP stdio transport splits only on LF, preserving Unicode line/paragraph
separators inside JSON strings. Independent requests run concurrently and responses
are correlated by id, so a slow media read cannot queue unrelated deliveries.
Cancellation prevents undispatched work and aborts the adapter's HTTP wait; it
cannot retract a delivery already submitted by Core. Outbox remains authoritative
for those outcomes, and a timeout must not be interpreted as proof of non-delivery.

Every delivery names the input it acts on as `decision_id`: that input's
`event=`, copied as the tag shows it; a `msg=` is never a reference, since one
message can carry several events and a message id can equal another event's id.
The Core resolves it to one accepted event `(instance, event_id, seq,
scope_key)`. Event ids are unique only per instance, so an id accepted on
several instances is refused with `decision_ambiguous` until `decision_instance`
names one; it is never settled by taking the newest.

The freshness fence is a conservative default: a decision is stale once the
Core has accepted a later event in its scope, the conversation for chat events
and `(instance, subject.type, subject.id)` otherwise, ordered by the Core's
receive `seq`, never by provider time. A newer event does not cancel earlier
work: `response_to_id` may name an earlier event in that scope, for attribution
only. Right before the outbox reservation the Core re-checks runtime authority
and the scope of the event it first resolved. The Connector defines what one
subject is, for example whether the occurrences of a recurring calendar event
share a subject (so a new occurrence makes the previous reminder stale) or each
have their own.

Another bot's message (actor type `bot` or `app`) is a decision like any other:
whether and when to answer it is the model's judgment, with or without an `@`.
Its line carries `sender=bot` and `bots_since_human=<n>`, the bot messages in that
chat/thread since a person last wrote there. A safety fuse, not a working rule,
stops runaway loops: with no person writing in a chat/thread, once the agent's
text deliveries there have answered `VIKA_BOT_REPLY_FUSE` bot messages (default
20; `0` turns it off), the next is refused with `bot_loop` and the owner is told
once, privately, on the primary IM. A person's message there starts the count
over. The messages of one decision's burst count once; reactions and notices to
the owner do not count.

Who a decision may reach is decided in one Core function, so routing rules
(a source that may also post to a group, say) can be added there later. Today
a non-chat event may only notify the owner, through `notify_owner`: the agent
passes no chat id, and the Core sends to the owner's `target` on the primary
IM (`VIKA_CHANNEL`). A chat message may name another IM (`source`) when
the owner asked to be told there. A chat decision keeps every other delivery
tool, across Connectors too.

An action declared `approval` (an email reply) is authorized by the owner, not
by a decision: who asked does not matter, since a decision only proves which
input the agent acted on, and an old message from the owner stays fresh in its
own chat however many mails arrive. Any decision, the mail itself included, may
propose one. The Core shows the owner the draft on the primary IM with the
recipients and subject (from the mail event's `data.reply`), in the form that IM
can show. On an IM with cards (Lark) it is a card with the draft in an editable
input (read-only beyond 1000 characters), registered as a question only the
owner can answer, once, within three days. On any other IM it is a numbered
message; the owner answers with the whole message `发送 N` (send), `不发 N`
(drop) or `修改 N <text>` (replace the text, shown again for confirmation), and
the Core takes such a message as an answer only from that draft's owner on that
IM, once, within three days; anything else is an ordinary message. A newer
draft for the same mail replaces a pending numbered one. The draft lists every
recipient: a mail that would be answered to more than 20 addresses reports only
their number in `data.reply` and cannot be answered. On send the Core delivers
the text the owner approved to the recipients the draft showed, passed as `to`;
the Connector sends only if they are exactly the mail's reply recipients. The
key is per card or numbered draft, so the reply goes out at most once, and the
owner sees the outcome: sent, cancelled, not sent or uncertain. The model has no
part in that; afterwards the Core tells it, as a line in the owner's chat, what
actually went out (with the owner's edits) or that nothing did. A reply is recorded where its target is: one to
a mail has no chat, and its history line is rendered from the mail's event.

The Core derives the idempotency key from the decision and response events'
`(instance, event_id)`, the target and the canonical payload, so two events with
the same text are two deliveries (a caller-supplied key is bound to that payload), reserves it in
`outbox` together with a `PENDING` outbound journal row, and only then sends the
Connector `action` request. It records `SENT` only after the Connector replies
successfully; any failure, or a process exit between remote acceptance and the
reply, leaves the row `UNCERTAIN`. A repeated identical request returns the
earlier result instead of sending again. An `UNCERTAIN` row is retried with the
same key only when the action is declared `idempotent`; otherwise it stays
blocked. Daemon-authored replies (slash command results, failure notices) use
the same path with a key per event and purpose.

```json
{
  "protocol_version": "0.2",
  "type": "REQUEST",
  "id": "request-id",
  "operation": "action",
  "payload": {
    "name": "message.reply",
    "input": {
      "message_id": "provider-message-id",
      "content": { "type": "markdown", "text": "hello" },
      "idempotency_key": "ld_..."
    }
  }
}
```

A `file` body arrives as `{ "type": "file", "path": "<absolute snapshot path>",
"name": "report.pdf" }`: the Core has already validated the workspace file and
frozen it in a private directory, which it removes after the attempt. Markdown
mentions arrive as `mentions: [{ id, name? }]` already resolved by the Core.
Results report the delivered `message_id` and, where known, `chat_id`,
`thread_id` and `root_id`.

## Current actions

| Operation | Lark | Weixin | Telegram | Slack | QQ | Email |
| --- | --- | --- | --- | --- | --- | --- |
| `message.send` | markdown, file, file key, card; mentions; idempotent; to a chat or the owner | markdown (plain text), file | markdown (plain text), file; verified owner only | markdown (as mrkdwn), file; mentions; to a channel the bot is in, or a DM | plain text, file; verified owner, proactive quota | — |
| `message.reply` | as `message.send`, plus threads; to a document comment: plain text, not retried | markdown (plain text), file | as `message.send`, with quoted reply | as `message.send`, into the target's thread | plain text, file; passive quota, quote when indexed | markdown (plain text); owner approval on the primary IM |
| `message.react`, `message.retract` | yes | — | — | yes | — | — |
| `message.media` (query) | download by image/file key | downloaded inbound media | lazy download by message ID | lazy download by message ID and file ID | lazy download by message ID | — |
| `chat.list`, `chat.messages`, `thread.messages` (queries) | yes | — | — | bot identity, cursor pagination | — | — |
| `chat.members.list` (query) | — | — | — | member IDs and optional profiles | — | — |
| `message.search` (query) | yes | — | — | — | — | — |

The Email Connector watches one mailbox's `INBOX` over IMAP, opened read-only
(`EXAMINE`, `BODY.PEEK[]`): it never marks, moves or deletes mail. It starts
from the mail that arrives after its first start, keeps a UID cursor, and after
a stop reports at most the newest 50 mails it missed. Auto-replies, bounces and
its own replies are skipped; mailing-list and bulk mail arrive as `email.list`.
Replies go over SMTP to Reply-To (else From) with `Re:`, `In-Reply-To` and
`References`, as plain text. SMTP has no idempotency key, so an uncertain reply
is never resent.

The Lark Connector also serves Core-internal operations that are not agent
tools: `message.reaction.delete` (temporary working reactions), `card.update`,
`chat.members.list`, `chat.get` and the meeting operations.

## QQ provider contract

QQ uses [Tencent's Bot API v2](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)
at `https://api.bot.qq.com` and the pinned MIT `@tencent-connect/qqbot-nodejs`
gateway transport (Identify/heartbeat/Resume/reconnect, intent `1 << 25`). HTTP
requests use bounded, cancellable fetches with no raw provider diagnostics.
Only `C2C_MESSAGE_CREATE` from the verified owner's OpenID is admitted; group,
guild and stranger messages are ignored. The independent QR credential SDK is
not bundled. Setup takes AppID/AppSecret, then a fresh random `vika-bind` C2C
proof within three minutes. Reauthorization keeps old credentials until proof,
stages the old owner's events during the handshake, and holds a local PID lease.

The private `<dataDir>/qq/state.sqlite3` stores credentials, owner, reply routes,
reference indices, passive reply counters and pairing's staged events. An
account/owner change clears old routes/counters/staged events. Both event and
message IDs are `qq:<appId>:<sha256(nativeId + NUL + msgIdx)>`; signed attachment
URLs and scene `auth_token` stay out of canonical events, logs and tool output.
Quote parents resolve only against locally known reference indices. `msg_id`
is the inbound native ID; visual `message_reference.message_id` is `msg_idx`
or the send response's `ext_info.ref_idx`, not the native ID.

Each native inbound ID allows four attempts. `msg_seq` is reserved durably
before POST, including uncertain attempts; duplicate events do not reset it.
The local expiry is occurrence time plus 60 minutes. Tencent's current docs
also retain a contradictory five-minute field description: earlier provider
expiry errors are respected without switching to proactive sends. Explicit
`message.send` and owner notifications omit `msg_id` and require proactive
permission/quota. Sends are paced, are never automatically retried, and do not
declare `idempotent`. HTTP-200 business errors and HTTP-201/202 pending review
responses are not reported as successful delivery.

Text is literal plain text (4000 characters); files/downloads are capped at
20 MiB. Uploads below 5 MB use base64, larger files use prepare/PUT/part-finish/
complete, always `srv_send_msg=false`; only the final Core action sends a
message. Part acknowledgement retries only explicit transient code 40093001,
within the action's deadline. No Bot authorization is sent to CDN/COS hosts;
redirects are forbidden and hosts/stream sizes are checked. Files are private
and pruned by retention. Voice, video, groups, buttons and history are outside
this version. Resume is in-process only; this connector adds no shared durable
ACK, cross-process replay guarantee or outbox reliability changes.

## Telegram provider contract

Telegram uses the official hosted [Bot API](https://core.telegram.org/bots/api),
with a dedicated BotFather token per instance and `getUpdates` long polling.
`getMe` verifies the bot, not the owner. Pairing requires a fresh private
`/start <nonce>` from a random, three-minute deep link. Both the sender ID and
private chat ID are pinned; groups, other senders and all Start commands are
excluded from runtime input. `check` also refuses existing webhooks without
deleting them. A local SQLite PID lease prevents setup and the worker from
polling the same instance simultaneously; Telegram 409 conflicts fail the worker.

Tokens, identity, update cursor and reply/media routes live in the connector's
private SQLite database, never the manifest. IDs are validated safe integers
and represented as strings. Event IDs are `<botId>:<updateId>`; message IDs are
`tg:<botId>:<chatId>:<messageId>`, so bot changes and separate chats cannot collide.
Media captions retain their media message type and cannot become slash commands.

The next `getUpdates` call confirms updates below its offset. The worker writes
its cursor after emitting a batch, including skipped update kinds. During
reauthorization, the old owner's canonical events are staged in a private
pending table before the login poll can confirm them; cancellation preserves
the old credentials and staged events. On restart those events are emitted
with stable IDs for Core deduplication. This staging is specific to pairing;
it does not add a Core durable ACK or exactly-once guarantee. Telegram retains
unreceived updates for at most 24 hours.

Text is literal plain text, at most 4096 characters. JPEG/PNG files up to 10 MB
use `sendPhoto`; larger images use `sendDocument`. The provider document limit
is 50 MB, but the existing Core workspace-file limit remains 20 MiB. Downloads
use `getFile`, preserve names/MIME from the inbound message, and enforce the
hosted API's 20 MB limit on both metadata and streamed bytes. File paths cannot
change the API host or traverse directories, redirects are disabled, and errors
discard URLs/provider descriptions because Bot API URLs contain the token.

Sends are serialized and paced; only explicit 429 responses with `retry_after`
are retried, at most twice within the action deadline. Network failures, 5xx
and invalid responses have uncertain outcomes and are not retried. The manifest
does not declare idempotency, so the existing Core outbox also blocks uncertain
resends. `pause` aborts only receiving so shutdown deliveries can finish; `stop`
also cancels actions and releases the lease. The primary-channel notification
and numbered mail approval paths use the existing Core authorization rules.

## Slack provider contract

Slack uses one workspace-internal app per instance, created from the manifest
`vika setup` prefills (Socket Mode on, token rotation off, bot scopes
`chat:write reactions:write files:read files:write users:read
channels:read groups:read im:read mpim:read
channels:history groups:history im:history mpim:history`, bot events
`message.channels message.groups message.im message.mpim`). An internal app keeps
the Web API's ordinary rate limits; Slack throttles history reads for apps
distributed outside the Marketplace. `app_mention` is not subscribed: the same
message would arrive twice under different event ids. The bot token (`xoxb-`)
calls the Web API; the app-level token (`xapp-`, `connections:write`) only opens
Socket Mode connections. Both live in `<dataDir>/slack/state.sqlite3` (0600,
directory 0700) with the owner, reply routes, display names and the receive
queue, never in the manifest.

Pairing checks that both tokens belong to one app (`auth.test` + `bots.info`
against the Socket Mode hello) and binds the owner: whoever sends the one-time
code to the bot in a direct message within five minutes. Everyone in the
workspace may still talk to the bot, as on Lark.

Socket Mode hands each envelope to one of the app's open connections, chosen at
random, and reports no conflict. The first connection of a worker therefore
checks `hello.num_connections`: another receiver (counted again after 15 s, for
a connection of a crashed worker that Slack has not dropped yet) fails the
worker. A local PID lease keeps setup and the worker apart. Connections are
refreshed before Slack closes them (`disconnect` warning or refresh), and every
30 minutes, new one first; `link_disabled` and authorization errors fail the
worker.

A relevant envelope is staged in the private queue and only then
acknowledged; name lookups (`users.info`, cached for a day) and the Core
delivery follow from the queue, so a slow lookup never delays the
acknowledgement and a worker restart loses nothing it acknowledged. Slack's
retries cover only a few minutes of downtime; there is no catch-up read yet.

Only new messages are events: the bot's own posts (they come back as events),
edits, deletions, joins and other subtypes are dropped before staging, so they
can neither wake the model nor make a pending decision stale. Messages from
other apps are kept as actor type `bot`. The event id is the message identity
`<team>:<channel>:<ts>`, so redeliveries and overlapping connections deduplicate
in the Core; the message id is `sl:<team>:<channel>:<ts>`. A thread reply carries
`conversation.thread_id = thread_ts`, a scope of its own; a top-level message
keeps the channel's. `im` is a `p2p` conversation, everything else `group`.
Slack markup is rendered to text: mentions by display name (the bot itself as
the agent's name), channel links and URLs; text a person typed stays literal.

Basic queries run in the connector through the bundled official Slack CLI
4.10.0 (`slack api --data`): `users.conversations`, `conversations.members`,
`users.info`, `conversations.history`, and `conversations.replies`. CLI calls
use an isolated temporary configuration and working directory, a filtered
environment with only this instance's bot token, bounded output and a deadline;
the worker registry tracks and terminates the child. CLI diagnostics never
escape as raw error text. Binaries for Linux/macOS x64/arm64 are pinned by
SHA-256 and bundled with their license. There is no personal user-token fallback.

Each query returns one page with `has_more` and `next_cursor`. An empty filtered
conversation page can still have a continuation. History is newest first;
threads are oldest first and include the root on the first page. History
keeps old/self posts and attachment references, but never stages new events
or supplies decision authority. It caches reply/file routes in the connector.
Message results include canonical `conversation` scope and a full `thread_id`
(`sl:<team>:<channel>:<root-ts>`); the Core remembers bounded query routing
context separately from its inbound ledger. Query errors leave reception and
delivery running. Search and user-identity reads remain unsupported on Slack.

Outbound Markdown is escaped and converted to mrkdwn, so model text cannot
produce `<!channel>` or other control tokens; mentions the Core passed (ids, or
names the connector resolves against the people it showed the model in that
chat) become `<@U…>`. Text is at most 12,000 characters, link unfurling is off,
and sends are serialized at about one per second per channel. Files go through
`files.getUploadURLExternal` and `files.completeUploadExternal`; sharing is
asynchronous, so the delivered message id may be unknown. Downloads and uploads
accept only `slack.com` hosts and never follow redirects. Only explicit
`ratelimited` responses with `Retry-After` are retried, at most twice; Slack has
no idempotency key, so uncertain sends stay blocked in the outbox. A reaction
maps the model's Lark emoji names to Slack's (`OnIt` → `eyes`).
