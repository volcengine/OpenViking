# Scheduler

How accepted inputs reach the model. Set per instance with `agents[].scheduler`
in `config.json` (`VIKA_SCHEDULER` in the daemon env), read once at start.

| Value | Behaviour |
|---|---|
| `legacy` (default) | Every input goes into the running turn as soon as it arrives (Codex/TraeX `turn/steer`, Pi `steer`, ...); an idle runtime starts a turn with it. Group lines that arrive within 400 ms start one turn together. |
| `inbox` | While the model works, inputs wait and the model is told they are there; it reads them at a checkpoint. |
| `workers` | The inbox, and the main session becomes a router: it hands longer work to worker sessions on the same runtime and relays their results. |

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

## Workers

With `workers`, the main session is a **router**. It still reads every input,
through the inbox above, and answers what is quick. A request that needs real
work (running or changing code, investigating, research, writing a document)
becomes a **task** (`packages/daemon/src/tasks.mjs`), and a **worker** does it:
a separate session on the router's own runtime. The worker never talks to
people; it reports, and the router relays the report where the request was
made. The router stays the only outlet, so every check on the delivery path
(authority, freshness fence, thread placement, bot fuse, approvals, outbox
idempotency) applies to what a worker produced, without a second copy.

```
input ─▶ inbox ─▶ router ──task_create / task_route──▶ task (SQLite) ──▶ worker turn
                    ▲                                                      │
                    └──────── [task report] (through the inbox) ◀── task_report
router ──reply_message / send_message──▶ ActionService ──▶ Connector
```

### Tasks

A task is a row in `tasks` (created on first use, like `inbox_pending`): its
origin (the input the result answers: `event=`, `msg=`, chat, thread, requester),
title, the router's note (≤ 30 words of routing facts, never a plan), status,
the worker's session, and the latest summary, draft, question and artifacts.
`task_inputs` holds every line routed to it, in order. `task_input_claims`
associates inputs with the turn reading them (including `task_check`); only a
successful turn acknowledges them. Failed or interrupted turns release their
claims so the inputs can be replayed. `task_reports` is the append-only list of
reports.

Status: `queued` (waiting for a slot), `running`, `waiting` (a question for a
person), `blocked`, and the ended `done`, `dropped`, `failed`, `stopped`. A
requester's follow-up resumes an ended task, except a stopped one.

### Router tools

| Tool | Does |
|---|---|
| `task_create` | A task from the named input lines (`trigger_ids`, the first one is the origin), a title and an optional note. |
| `task_route` | More lines for a task: `owner` (adds to, changes or answers the request; resumes an ended task) or `context` (background). |
| `task_stop` | Stops a task and its worker; only when the requester or the owner asks. |
| `task_list` | The working set (every live task and the 32 most recently ended), one task in detail, or a search over titles. |

Routing an input to a task records it as decided (`decisions`, `silent`), so the
turn-end check does not ask again; the relayed result records the delivery over
it. A `[daemon tasks]` line on an input names the tasks it may belong to and why:
same thread, a reply to the task's messages, a person the task is waiting for,
the same requester, similar words (character-pair overlap, ranking only). Only
the owner's lines are matched against tasks in other chats.

### Workers

A worker turn is one run of the router's runtime CLI in its non-interactive
mode, resuming the task's own session (`worker-executor.mjs`):

| Runtime | Turn |
|---|---|
| Pi | `pi --mode json -p --session-id <task> ...` |
| Codex | `codex exec --json ...`, then `codex exec resume <thread>` |
| Claude Code | `claude --print --output-format stream-json --session-id/--resume <task>` |

No process runs between turns, so an idle task holds nothing; Durable,
OpenCode, TraeX and Ark have no worker mode, and `task_create` tells the router
to handle the request itself there. A task keeps the runtime it started on.
Three turns run at once, plus one slot only a follow-up to an existing task may
take. Each task works in `<workspace>/tasks/<id>/`.

A worker's policy is `agent/worker.md` plus the instance Role. Its first input
is the brief: the original input lines, who asked and where, the router's note
and its directory. Its Core tools are `task_check` (lines routed to it since it
last looked), `task_report` and the reads (chat and thread history, search,
media, mail, OpenViking reads); never a delivery, a decision, a timer or a
memory write. Its authority token is valid for its turn only.

A turn ends with the latest final `task_report` (`done` with a ready-to-send
draft, `needs_user` with one question, `blocked`, `dropped`, `failed`), or,
without one, the worker's last words as an `unstructured=1` result. Before a
report is relayed, inputs the requester added during the turn must be in it:
if any were not read, the worker runs again (up to three times). A turn that
failed for a passing reason (429, 5xx, a reset connection) runs again, twice at
most.

A successful turn records a session checkpoint. A failed or interrupted turn
starts a new session with the persisted request history, routing note, previous
summary/draft and artifacts, and an instruction to check existing work before
repeating outside actions. A preallocated session ID or turn counter alone is
never evidence that Claude can resume a session. Recovery can repeat work;
outside actions are not guaranteed to run exactly once.

### What the router sees

```
[task report] task=t3 state=done title="排查飞书名字泄露" requester=陈锦
origin: chat=oc_... thread=omt_... source=lark msg=om_... event=...
summary:
| ...
draft:
| ...
```

Worker text is quoted (`| `), so it cannot pass for an input line. The router
receives the full stored draft (up to the 20,000-character reporting limit)
and relays it with `reply_message` to the origin `msg=`.

Ending a task and selecting its final report for delivery are committed together
in SQLite (`task_report_deliveries`). The inbox row, report's delivery timestamp
and report-to-inbox receipt then commit in one transaction, before waking the
router. The report ID is the idempotency key; consuming the inbox row does not
erase its receipt. Failed writes retry automatically and pending deliveries are
recovered at startup, without rerunning the worker. Task reports in the inbox
do not expire under the incoming-event age limit. This guarantees the handoff
to the inbox, not that the model ultimately sends the report to the requester.

### Interplay with the Core

- **OnIt.** The router acknowledges a task with a temporary OnIt. A turn's end
  takes OnIt reactions down except in a scope where a task is still `queued` or
  `running`; the relayed result takes those down.
- **Router tools.** On Pi the router's own bash commands are capped at 30
  seconds and may not `sleep`, and each run gets six calls of its own work
  tools (`bash`, `read`, `edit`, `write`, `grep`, `find`, `ls`); past them the
  call is refused with a pointer to `task_create` (a `tool_call` handler in
  `pi-mcp-extension.mjs`). A per-command cap alone did not stop a router from
  investigating with many short commands. Core reads are not counted. On Codex
  and Claude Code this is policy text only.
- **Restart.** Tasks, inputs and reports are in SQLite. A turn cut by a
  shutdown is not reported; at start its task runs again from the persisted
  request history, told to check what it already did before repeating an outside
  action. Selected final reports still awaiting the inbox are delivered again
  through their idempotent receipt.

Activities: `task.created`, `task.turn_started`, `task.turn_completed` (with
usage), `task.reported`, `task.ended`.
