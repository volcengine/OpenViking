# Scheduler

How accepted inputs reach the model. Set per instance with `agents[].scheduler`
in `config.json` (`VIKA_SCHEDULER` in the daemon env), read once at start.

| Value | Behaviour |
|---|---|
| `legacy` (default) | Every input goes into the running turn as soon as it arrives (Codex/TraeX `turn/steer`, Pi `steer`, ...); an idle runtime starts a turn with it. Group lines that arrive within 400 ms start one turn together. |
| `inbox` | While the model works, inputs wait and the model is told they are there; it reads them at a checkpoint. |

With `legacy`, the prompt, the tool list, the policyHash and the database schema
are exactly what they were before the setting existed.

## Inbox

Two problems with putting every input into the running turn at once:

- A long task is interrupted by every new line, from every chat.
- Under load the model tends to answer the newest line and lose the older ones.

The inbox (`packages/daemon/src/inbox.mjs`) changes when and how inputs reach the
model, not what they look like: an input line is the same line either way, with
the same `event=`, `msg=` and authenticated tags.

### Flow

```
Connector event → Harness (validate, dedupe, ledger) → inbox_pending
                                                         │
          runtime idle ──────────────────────────────────┤── wake: start a turn with the oldest
                                                         │   waiting inputs (up to 20; the rest
                                                         │   are noticed in that turn)
          runtime working ───────────────────────────────┤── notice: one [daemon inbox] line,
                                                         │   ~3 s after the first input of a burst
          message_check / held delivery ─────────────────┘── deliver: oldest first, through the
                                                             runtime's own submit path
```

- **Wake.** An idle runtime starts a turn with the oldest waiting inputs. A
  direct line starts it at once, a group line after the legacy 400 ms burst
  window, so a burst still starts one turn.
- **Notice.** While a turn runs, the first waiting input of a burst arms a
  3-second timer that later inputs do not extend. The notice is one line per
  chat with new inputs: where (`chat=`/`thread=`/`source=`/`type=`), how many
  wait there, the latest senders, and `owner=1` / `mention=me` when the owner
  or a mention of the agent is among them. Its header counts every waiting
  input. It never carries message text, and it is not an input: nothing is
  decided on it. A chat already noticed is named again only when new inputs
  arrive there.
- **`message_check`.** The model reads waiting inputs at a checkpoint in its
  work, oldest first, optionally from one chat or thread. They are delivered
  through `AgentRuntime.submit`, so the adapter books them like any steered
  input: the cognition journal, the turn's inbound seqs (and so the decision
  check at turn end), and the `agent.steered` activity.
- **Turn end.** Inputs still waiting when a turn ends start the next one.

### Interplay with the Core

- **Freshness fence.** Unchanged: a delivery is still refused when a newer
  accepted input exists in its scope. Under the inbox that input may still be
  waiting, so the model could not reconsider anything. The refusal then
  delivers that scope's waiting inputs at once and says so; a `stay_silent` on
  a superseded input does the same.
- **Decisions.** An input counts as consumed when it is delivered, by a wake,
  `message_check` or a held delivery, exactly as a steered input does. Inputs
  never delivered are never nudged.
- **Durability.** Waiting inputs are rows in `inbox_pending` (created on first
  use, so a legacy instance never has the table). A restart delivers them
  again; rows older than the event age limit (`VIKA_MAX_EVENT_AGE_MS`) are
  dropped like a stale event. Legacy keeps its known gap: an input inside the
  400 ms group window when the process dies is not delivered.
- **Runtimes.** The inbox needs the Core tools (`message_check`). Ark has none,
  so a manifest with `scheduler: "inbox"` on Ark is rejected; switching to Ark
  at run time delivers everything waiting at once and routes new inputs the
  legacy way. A runtime that cannot take input mid-turn (OpenCode,
  `activeInput: queue-next-turn`) gets no notices; its waiting inputs start its
  next turn.
- **Slash commands, approvals, meeting notes.** Slash commands and approval
  replies are handled by the Core before the inbox. Daemon notes about a chat
  (`[mail reply sent]`) and undecided-line reminders wait in the inbox like
  inputs. The meeting inbox keeps its own notice.

### Observing it

Activities `inbox.notified` (how many wait, how many are new) and
`inbox.delivered` (`reason`: `wake`, `check`, `held_delivery` or `drain`, the
inbound seqs, and `waitedMs`, the longest wait among them).
