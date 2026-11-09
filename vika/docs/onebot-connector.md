# OneBot Connector

[Overview](../README.md) · [Features](usage-reference.md#qq-over-onebot) · [Connector protocol](connector-protocol.md#onebot-provider-contract)

The `onebot` Connector lets an instance speak through an ordinary QQ account. It is a protocol-neutral
[OneBot v11](https://github.com/botuniverse/onebot-11) client: a OneBot implementation logs in to QQ and
serves a WebSocket, and Vika connects to it. It is written against the source of
[NapCat](https://github.com/NapNeko/NapCatQQ) and tested against a simulated OneBot server, not a live QQ login;
LLOneBot and Lagrange.OneBot speak the same protocol with the differences listed under
[Implementations](#implementations). The code is in `packages/daemon/src/onebot/`.
What the model is told about it is in `packages/daemon/agent/connectors/onebot.md`.

It is a different Connector from `qq`. The `qq` Connector uses Tencent's official Bot API: a registered bot, the
owner's direct chat only, no groups. `onebot` drives a personal account through a third-party client. It can sit in
the groups that account is in and read history, and it carries that account's risks.

## Before you start: account risk

Tencent does not support third-party QQ clients. An account driven by NapCat, LLOneBot or Lagrange may be asked to
verify itself, logged out, or restricted by the platform, and no setting on the Vika side can prevent that. Vika keeps its
side conservative: it sends one message at a time, at most one every 1.2 s per chat; it never sends @全体成员; it
never answers private chats you have not connected. This lowers the risk but does not remove it. Use an account you
can afford to lose, not your main one.

## How it connects

Vika is the client of the implementation's WebSocket server (forward WebSocket; "WebSocket 服务器" in NapCat).
Events and action calls share that one connection, and each call is matched to its answer by `echo`. Vika needs no
listening port and no public address.

- The access token goes in an `Authorization: Bearer` header, never in the address, so the address can be logged.
  An address with `access_token=` in its query, a user name or a password is rejected.
- A dropped connection is reopened after 1, 2, 5, 10, then every 30 s. When the server announces a heartbeat
  interval, a connection that stays silent for twice that interval plus 10 s is closed and reopened.
- A rejected token (retcode 1403, or close code 1008) stops the worker with "OneBot rejected the access token; run
  vika login onebot <instance>". The Core shows that in Activity and retries with its usual backoff.
- The socket opens at the Connector's `check` and stays open until `stop`. `pause` stops delivering events, but
  sends still work while the Core drains on shutdown.

OneBot does not replay what a client missed. Messages that arrive while the worker is not connected never reach the
agent (see [Limits](#limits-and-known-gaps)).

## Set up the OneBot side

Run the implementation on the same machine as Vika, or on a network only you can reach. Plain `ws://` sends the
token and every message in clear text, so use it only over loopback or a private network.

### NapCat

In NapCat's WebUI, under network configuration, add a **WebSocket server** (`websocketServers` in
`onebot11_<QQ number>.json`):

| Field | Value |
| --- | --- |
| `host`, `port` | Where Vika can reach it; the defaults are `127.0.0.1` and `3001` |
| `token` | A long random string. Vika requires one: any program that reaches the server can act as the account |
| `messagePostFormat` | `array` (the default). Vika also reads `string` (CQ codes), and logs a hint to switch |
| `reportSelfMessage` | Optional. Turn it on to let the agent see what other programs, or you, send from this account (see [Sharing the account](#sharing-the-account-with-another-program)) |
| `heartInterval` | Leave the default (30000 ms). Vika's watchdog then fires after 70 s of silence |

`reportSelfMessage` applies to this connection only, so another program connected to the same NapCat keeps its own
setting.

For received files (not images), also turn on NapCat's top-level `enableLocalFile2Url`. NapCat then returns the
file's bytes when Vika asks for them. Without it, a file can be downloaded only when NapCat put a download address
in the message event, which it does not always do.

### LLOneBot

Add a connection of type `ws` with `host`, `port` and `token`. Set `messageFormat` to `array` (the default).
`reportSelfMessage` works as on NapCat.

### Lagrange.OneBot

Add an entry to `Implementations` with `"Type": "ForwardWebSocket"`, `Host`, `Port` and `AccessToken`. Keep
`Message.StringPost` at `false`. `Message.IgnoreSelf` applies to every connection: with the default (`true`), no
connection sees the account's own messages.

## Connect Vika

Select OneBot in `vika setup`, or run `vika login onebot [instance]` to change the address, the token or the owner
later. Setup asks for the WebSocket address (`ws://` or `wss://`) and the access token (hidden input; required, so
set one on the server). It connects, asks the implementation which account is logged in (`get_login_info`), and
shows a one-time code `vika-xxxxxxxx`.

Send that code to the account from your own QQ, as a private message. Your QQ must be a friend of the account:
temporary sessions and group messages do not count. The sender of the exact code becomes the owner. Messages from
before the login started, other text and other chats do not bind anyone. Setup then lists the account's groups with
their numbers, for the [policy file](#the-policy-file). The login gives up after five minutes. Nothing is saved
until the owner is bound; a failed or cancelled login keeps the previous account.

The address, token, account, owner and the time of the first binding live in `<dataDir>/onebot/state.sqlite3`
(mode 0600, directory 0700), never in `config.json`. So do reply routes, the names the agent has been shown, the ids
of messages it sent and the lines waiting for delivery. Messages sent before the first binding are never delivered.
Rebinding the same account keeps that time. Binding a different account clears the old account's routes, names,
sent ids and waiting lines.

A OneBot-only instance uses `"channel": "onebot"`, `"connectors": ["onebot"]` and no `profile`. It can also share
an instance with Feishu, WeChat, Telegram, Slack, QQ and email. Codex, TraeX, Claude, Pi, DSH and OpenCode are
supported; Ark is not. The owner's private chat can be the primary channel: `notify_owner`, numbered email
approvals (`发送 N`) and `/primary` work there. `/primary status` shows it as `QQ（onebot）`.

## The policy file

Which groups the agent is in, and how closely it listens there, is set by hand in `<dataDir>/onebot/config.json`.
Without the file, no group is connected and only the owner's private chat is.

```json
{
  "groups": { "123456789": "ambient", "987654321": "mention" },
  "private": "owner",
  "commandPrefixes": ["#", "/rss"],
  "names": ["Vika"],
  "settleMs": 3000
}
```

| Key | Meaning | Default |
| --- | --- | --- |
| `groups` | Group number → `"ambient"` or `"mention"`. A group that is not listed is not connected: its messages are dropped before anything is stored or looked up, and the agent cannot send there | `{}` |
| `private` | `"owner"`: only the owner's private chat is connected. `"friends"`: every friend's private chat is connected (never temporary sessions or strangers) | `"owner"` |
| `commandPrefixes` | Text that starts with one of these is addressed to another program on the same account. The check is an exact, case-sensitive "starts with" on what was typed, after a leading quote, @account and whitespace. At most 50 entries | `[]` |
| `names` | Other names people call the agent by. A group message whose text contains one anywhere, in any case, is addressed to it; the names in its @mentions of other people do not count. At most 10 entries | `[]` |
| `settleMs` | How long an `ambient` group must be quiet before unaddressed lines are delivered, 0–60000 ms | `3000` |

Entries in `commandPrefixes` and `names` are 1–32 characters of one-line text that does not start with a space. A
`names` entry is a substring match, so a short name also matches inside longer words. @account always addresses the
agent. The account's nickname does not, unless you list it in `names`.

Unknown keys, repeated keys and invalid values are errors, so a typo cannot quietly disconnect a group. The file is
at most 256 KiB. The Connector reads it again whenever it changes, with no restart. A changed file that does not
parse keeps the last good policy and logs `onebot_policy_invalid`. An invalid file at startup fails the Connector's
`check` with an error that names the file. Lines still waiting in a group you remove are never delivered.

## Which messages reach the agent

### Classes

The Connector admits a message only from a connected chat: a listed group (`sub_type: normal`) or a connected
private chat (`sub_type: friend`). It then gives the message one of four classes:

| Message | Class |
| --- | --- |
| The echo of a message this Connector sent | dropped |
| Sent from this account by another program or by hand, private, starts with `/` | dropped |
| Other messages sent from this account by another program or by hand | context, shown as `sender=bot` |
| Private, starts with a command prefix | dropped |
| Private, from someone other than the owner, starts with `/` | dropped |
| Private, from the owner (or from a friend under `"private": "friends"`) | direct |
| Group, starts with a command prefix | context |
| Group, @account, a quote-reply to the agent's message, or contains one of `names` | direct |
| Other group message, `ambient` group | ambient |
| Other group message, `mention` group | context |

The echo of the agent's own message is dropped because, as an event, it would wake the model with its own words and
make its next message stale. A message counts as the agent's own only if its id is one this Connector got back from a
send. The echo can arrive before the send's answer, so the Connector waits for the send on the socket to be answered
before it decides. Sends still waiting their turn are not waited for.

A private command line is an exchange with the other program; anything typed there, a password included, must not
reach the model. A private `/` line from anyone but the owner is dropped as well, and so is one sent from the account
itself by another program or by hand. The Core runs a leading `/` in every direct chat as a daemon command for
whoever sent it, with no owner check (`commandCandidate` in `commands.mjs`, `handleSlashCommand` in `daemon.mjs`). A
friend's `/new` or `/model`, or a plugin host's `/` command in a private chat, would then reset or reconfigure the
session every chat shares. Only `/primary` checks the owner. The owner's `/` lines, typed from the owner's own QQ,
still reach the Core, unless a command prefix matches first. In a group the Core runs no commands, so a `/` line sent
from the account stays context.

@全体成员 does not address the agent.

### When lines are delivered

The Core wakes the model for every event a Connector emits. It also refuses a delivery once it has accepted a newer
event in the same chat (the freshness fence). A busy group delivered line by line would wake the model again and
again, and each answer would be stale before it went out. So the Connector holds each chat's lines in its state
(`pending` in `state.sqlite3`, which survives a restart) and decides when the chat is released:

- **direct** releases its chat at once.
- **ambient** makes its chat due once the chat has been quiet for `settleMs`: due at
  `max(last activity + settleMs, grace end)`. Last activity is the newest line held in that chat, of any class, or
  the agent's latest send there. Whatever the chat does, it is due 45 s after its oldest waiting ambient line.
- **Grace.** After a release, more ambient lines wait until 20 s after that release, so that chatter does not make
  the answer stale while the model is still writing it. The grace ends early at the agent's first send to that
  chat written after that release; an answer to an earlier batch that is still on its way does not end it. This matters most for runtimes that hold new input until the next turn (OpenCode).
- **context** never makes a chat due. It goes out with the chat's next release. Each chat keeps at most its newest
  30 context lines, none older than 6 hours; older ones are deleted unseen.

A release emits every line held in that chat, in arrival order. The Core's group window (400 ms) then hands them to
the model as one input: one wake per batch, and the model decides once, on the newest line. In an `ambient` group the
agent follows the conversation and decides whether to speak. In a `mention` group it is woken only when addressed,
and then sees the chatter before that line (up to the 30-line, 6-hour bound) as context.

With the default `settleMs`, a single unaddressed line in a quiet `ambient` group reaches the model about 3 s after
it was sent. In a group where someone writes every two seconds, it reaches the model after 45 s.

Lines that arrive while the Connector is paused are held too. After a restart, chats that hold a direct or ambient
line are released at once; chats that hold only context wait for their next release. The Core still drops a line
older than `VIKA_MAX_EVENT_AGE_MS` (24 h by default).

## Sharing the account with another program

NapCat and LLOneBot send every event to every connection, so Vika can share the account with a plugin host or
another bot framework connected to the same implementation. Both then see every message, and both may answer it.
To keep them from talking over each other:

1. **On Vika's side**, put the other program's command prefixes in `commandPrefixes`. In a group, its commands become
   context: the agent sees that the command was given, but is not woken by it. In a private chat they are dropped.
2. **On the other program's side**, make it answer only its own commands. Turn off whatever answers every message,
   an @mention of the account, a keyword or a private chat: AI chat, auto-replies, keyword replies, poke
   responses. Otherwise one @account wakes both programs. Also make it ignore the account's own messages, which most
   frameworks do by default. Otherwise it may react to what the agent sends.
3. **Optionally**, turn on `reportSelfMessage` for Vika's connection. What the other program sends then reaches the
   agent as `sender=bot` lines from the account's own name, as context; a private one that starts with `/` is
   dropped (see [Classes](#classes)). People see those messages as coming from
   the agent. The model is told not to answer, repeat or contradict them as if someone else had said them, and to
   leave commands to the program that owns them. Messages you send by hand from the account's own QQ show up the
   same way.

Choose prefixes the other program really requires: an entry like `gs` also catches ordinary text that begins with
those letters, which the agent then sees only as context. Do not connect two Vika instances to one account: both
would answer.

## What the agent can do

| Tool | On OneBot |
| --- | --- |
| `send_message` | To a connected group, the owner, or under `"private": "friends"` a friend who has written to the account. Never to an arbitrary QQ number |
| `reply_message` | Quote-reply (a `reply` segment). QQ has no threads. A file other than an image goes out unquoted, as its own message |
| Text | Plain text, 1–4000 characters; the Core refuses longer text before anything is sent. Markdown markers are removed: emphasis, heading `#`s, backticks and code fences go (the code stays), and `[label](url)` becomes `label (url)`. Longer output should be split or sent as a file |
| Files | Workspace files up to 20 MiB. PNG, JPEG, GIF and WebP (detected from the file's bytes) are sent as images; anything else as a file. The bytes travel in the message (`base64://`), so the implementation never opens a path or an address the model chose |
| `mention_names` | Groups only. A name must match exactly one person this Connector has shown the model in that chat (as a sender, an @mention or a history entry), as stored or as the model sees it in `from=` (brackets and `=` become spaces, spaces become `_`, at most 40 characters). `@Name` in the text, in either spelling, becomes the @ at that point; otherwise the @ goes in front. In a private chat the name stays text. `mention_user_ids` is not available, and @全体成员 is never sent |
| `react_to_message` | Group messages only, on NapCat and LLOneBot (`set_msg_emoji_like`). Names: `OK`, `THUMBSUP`, `LGTM`, `THANKS`, `OnIt`, `SMILE`, `HEART`, `CLAP`, `MUSCLE`, `FIRE`, `PARTY`, `SOB`. The temporary `OnIt` is removed after the final answer |
| `retract_message` | Only messages this Connector sent (`delete_msg`). QQ allows a recall for about two minutes |
| `get_message_media` | Every attachment of a message, by its `msg=` id: images, files and videos, up to 20 MiB each. A forwarded chat comes back as a text transcript (`name: text` per message, at most 200 messages and 64 KiB) |
| `list_chats` | The connected groups the account is in, filtered by name. Private chats are not listed |
| `list_chat_messages` | The newest 1–50 messages (default 20) of a connected chat, newest first, including the account's own as `sender.type: bot`. There is no cursor and no older page. History is context only and never a decision |

Inbound messages are rendered as text. An @ shows the person's group card or nickname; an unknown name is looked
up with `get_group_member_info`, for at most 5 people and within 2 s per line, and the rest stay QQ numbers. A
failed lookup is not repeated for 10 minutes. At most 100 @mentions of a message reach the Core, the agent's own
among them; the text still shows every one. A quote becomes `parent_id`, so a reply to the agent carries `reply_to=me`. Images, files, videos and forwarded chats
become attachment references, at most 10 per message. Stickers, faces, voice messages and share cards become short
markers (`[sticker]`, `[face]`, `[voice message]`, `[card] <title> <link>`); a card's JSON is never shown. Text is
cut at 16,000 characters. Media addresses carry download keys and never appear in text, logs or errors.

Not available: threads, cards and `choices`, message search, member lists, editing, sending voice or video as such,
reactions in private chats, and reading chats that are not connected.

Ids: a chat is `group:<number>` or `private:<number>`. A message is `ob:<chat>:<message_id>`, keeping the sign of
NapCat's signed message ids. An actor is a QQ number.

## Implementations

| | NapCat | LLOneBot | Lagrange.OneBot |
| --- | --- | --- | --- |
| Wrong token | Rejected with retcode 1403: the worker stops | Retcode 1403 or close 1008: the worker stops | HTTP 403 at the handshake, which looks like an unreachable server: the worker keeps retrying |
| Own messages | `message_sent`, per connection (`reportSelfMessage`) | `message_sent`, per connection | `message` from the account itself, for all connections (`IgnoreSelf: false`) |
| Sending files other than images | Yes | Yes | No: its `file` segment only forwards a file it already knows |
| Reactions (group messages) | Yes | Yes | No (`Reactions are not available on this OneBot implementation`) |
| Received images | `get_image`, else the event's address | `get_image`, else the event's address | The event's address only |
| Received files and videos | `get_file` (the bytes only with `enableLocalFile2Url`), else the event's address | Videos through `get_file`. Other files cannot be fetched: `get_file` gives neither their bytes nor an address, and the event's file address is a local `file://` path or empty | The event's address only |
| Forwarded chats, history, member names | Yes | Yes | Yes |

Event addresses carry a key that expires, so an old attachment may no longer download. NapCat forgets which file
belongs to which message when it restarts. The address is the segment's `url`, never its file name, which the sender
chooses. Downloads never follow redirects, and never go to `localhost` or to an address literal in a loopback,
link-local, private, shared (`100.64.0.0/10`) or unspecified range; host names are not resolved for this check.

## Security

- **Connect only chats you trust.** Every member of a connected group, and under `"private": "friends"` every friend,
  can talk to an agent that runs commands and changes files in its workspace. The agent's prompt is not a security
  boundary: anyone who can write to it can try to talk it into something.
- **Run the instance isolated.** Use its own OS user, container or VM, with a workspace and credentials that are
  scoped to what this agent should touch. Do not give it your personal SSH keys, cloud credentials or other chat
  accounts.
- **Protect the OneBot server.** Whoever reaches its port with the token controls the QQ account. Keep it on
  loopback or a private network, set a token, and use `wss://` across machines.
- The token is never written to logs, errors or the manifest. Implementation errors reach the model as one line with
  addresses removed.

## Limits and known gaps

- **No catch-up.** Messages that arrive while the worker is down or disconnected are not delivered later; OneBot has
  no replay. The agent can read what it missed with `list_chat_messages`.
- **Private chats.** Under `"private": "owner"`, every private chat but the owner's is ignored. Temporary sessions,
  strangers and anonymous group messages are always ignored. Notices (joins, recalls, pokes) are not delivered: a
  recalled message stays in the agent's context.
- **One account per instance**, and one OneBot Connector per instance.
- **Failed sends are not resent.** A send whose request went out but got no answer within `VIKA_SEND_TIMEOUT_MS`
  (30 s by default) is reported as uncertain; one that never reached the socket, for example while it reconnects,
  says it was not sent. The Core cannot tell the two apart: it records every failed send as uncertain, and because
  OneBot has no idempotency key, its outbox blocks the identical retry either way. If an uncertain message did go out,
  its echo is not recognised as the agent's own and arrives as a `sender=bot` context line.
- **Pacing.** Sends go out one at a time, at most one every 1.2 s per chat. A burst to one chat queues behind that
  chat's pace; sends to other chats do not wait for it.
- **Reactions.** The face ids exist in the QQ face table NapCat and LLOneBot ship. Whether QQ accepts every one of them
  as a reaction has not been tested. `DONE` and `THINKING` have no face there and are refused.
- **History** is the newest page only, at most 50 messages.
- Routes, names, sent ids and downloaded media are pruned after `VIKA_RETENTION_MS` (90 days by default). Media lives in
  `<dataDir>/onebot/media`.
