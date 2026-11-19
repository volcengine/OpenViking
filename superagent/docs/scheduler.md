# Scheduler

How accepted inputs reach the model. Set per instance with `agents[].scheduler`
in `config.json` (`OV_SUPERAGENT_SCHEDULER` in the daemon env), read once at start.

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
  again; rows older than the event age limit (`OV_SUPERAGENT_MAX_EVENT_AGE_MS`) are
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
person), `blocked`, and the ended `done`, `dropped`, `failed`, `stopped`.

### A task is one result, pushed forward in rounds

A week of online traffic (221 tasks) showed what happens when a task takes
whatever arrives near it: 307 of 398 follow-ups reopened a task that had
already reported, some a day later, and a task became "the thread's worker".
About a fifth of the follow-ups asked for another result (a PR after an
assessment, a review of a new revision, an unrelated bug), and people's talk
among themselves went to the worker as if it were the requester's. A follow-up
sent to a task that was still working waited for that turn to end: four
minutes at the median and over two hours at p90.

Refusing every input to an ended task fixed that, at a cost. Work that keeps
going on one result after it was delivered (a pull request reviewed, fixed and
rebased over a dozen exchanges) became a chain of tasks, each following the
last. A replay of twelve such exchanges on one pull request made ten tasks:
the router was shown three same-thread tasks with near-identical titles, every
task cloned the repository again (17 clones), and every task's first turn
copied the whole chain's session (1.7 MB of sessions for a final session of
265 KB). The copy did not even keep the prefix cache: the first model call of
each forked task read the inherited history uncached (2048 of up to 52k tokens
cached), because the system prompt named each task's own directory (see
Workers), while the router, carrying on its own session, had nearly all of it
cached.

So a task is one result someone keeps pushing forward (an answer, a fix, a
pull request, a document), and it takes only what is about that result:

- **The router decides by what an input asks for.** Input that changes,
  narrows, corrects or adds a condition to a task's work, or answers its
  question, is `owner` input for it while it works. After it reported, more
  work on what it delivered (a change, fix or re-run of its pull request,
  branch or document; the fix for what its investigation found) is `owner`
  input too, and reopens it. Another result (another question or answer, a
  review, a document, a fix or change of something else) is a new task, which
  **follows** the task it builds on (`task_create` with `follows`,
  `parent_id`): its brief carries that task's request, its last round's
  request, summary, last answer, artifacts and directory. When unsure, the
  router creates the new task: a wrong split costs a fresh start from that
  task's work, a wrong merge makes a worker do unrelated work and keeps the
  person waiting behind it. People's talk, a remark on a result included,
  never reopens a task. The `[daemon tasks]` line lists the tasks near an
  input (same thread as any of their rounds, a reply to their messages,
  waiting for this person), what each is doing (`working`, `waiting for …`,
  `done 12m ago, 3 rounds`) and the first 40 characters of what its current
  round asks (`now on:`) or its last one asked (`last asked:`): a title is
  written when a task is created and says nothing of its later rounds. A
  reply to a message the router sent for a task counts as a reply to that
  task's message whether the router decided on the round's origin or on a
  later input there: the delivery that settled the task's report names that
  message (about 4% of relayed results in replays were otherwise not linked).
  "Same requester" is no longer a reason, and similar words only rank what an
  exact signal found, or point the owner across chats. Similar words look only
  at the working set (live tasks and the 32 that ended last, about a day of
  tasks online); the exact signals reach past it: the task a line replies to,
  and the latest ten tasks asked in its thread, however long ago. Every one of
  these reads goes through an index (the working set by end time, a message's
  task, a thread's tasks), so a hint costs about the same with 100 000 tasks as
  with 1 000 (1.5 ms measured; reading the working set by status took 89 ms at
  100 000). The rule for a working task is the one from before rounds,
  unchanged.
- **An ended task takes a new request as a round.** `owner` input to a task
  that is `done`, `dropped` or `failed` reopens it for its next round
  (`task_rounds`); `context` reopens nothing, and a `stopped` task stays
  stopped (work on it again is a new task that follows it). A round records
  where it was asked and by whom: its report says `round=N`, its origin is that
  round's message, so the answer goes there and an answer there settles that
  round's report (an answer on another round's message settles nothing of
  it). Reports of earlier rounds stay deliverable while the task works on the
  next. The working task holds its directory again: what the sweep did to it
  is undone where it can be (a removed directory is made again, empty, and its
  clean-up marks are cleared) and the brief says what is gone. The worker's
  session carries on when it is still there and under 4 MB; otherwise
  (deleted with the directory, or too large) the round starts a fresh session
  from the task record, round by round. A round may take the follow-up slot.
- **A round that is not the task's never happened.** When the worker hands
  back every request line of a round, nothing of that round is relayed (a
  report it filed is kept, settled as handed back), and the task goes back to
  how it ended, with the summary and answer of its last real round. The router
  decides on the handed-back lines as new inputs.
- **One request, one live task.** `task_create` refuses a line a live task
  already holds as part of its request; `task_route` refuses lines from another
  chat than the task's unless the owner wrote them.
- **Live input for workers.** New task inputs reach a working worker within a
  step instead of after its turn. Nothing is pushed: the worker's own runtime
  pulls them (`task_check` under the worker's authority) at two points, after a
  model step or tool call and when the run is about to end, and adds them to the
  session; at the second point the run goes on instead of ending.
  - Pi: `pi-mcp-extension.mjs` on `turn_end` and `agent_before_settle`.
  - Claude Code and Codex: `task-input-hook.mjs` as a `PostToolUse` hook
    (`additionalContext`) and a `Stop` hook (`decision: block`), the hook format
    both CLIs share. Claude Code gets them in the worker's `--settings`; Codex as
    `-c hooks.<event>=[…]` overrides for that run, which need
    `--dangerously-bypass-hook-trust` (worker runs already bypass approvals and
    the sandbox). Nothing is written to `~/.codex` or `~/.claude`.
  Both points are where Pi's own `steer` delivers too; a long tool call still
  finishes first.
- **Workers hand back what is not theirs.** `task_handback` (worker) returns
  routed lines that ask for something else; a task that fails or is stopped
  hands back the follow-ups no turn ever took. Both reach the router as one
  `[task handback]` inbox item with the original lines; it decides on them as
  new inputs and never routes them back to that task.

### Router tools

| Tool | Does |
|---|---|
| `task_create` | A task from the named input lines (`trigger_ids`, the first one is the origin), a title, an optional note, and `follows` when it builds on a task's result as a separate one. |
| `task_route` | Lines for a task: `owner` (something on the result it works toward or delivered; for an ended task, a new round) or `context` (background for a task at work). Refused for a stopped task, and `context` for an ended one. |
| `task_stop` | Stops a task and its worker; only when the requester or the owner asks. |
| `task_list` | The working set (every live task and the 32 most recently ended), one task in detail, the tasks one person asked for (`for_event`), or a search over titles. Each line says how long a task has been working and whether its latest result was sent (how long ago, as which message). |

Routing an input to a task records it as decided (`decisions`, `silent`), so the
turn-end check does not ask again; the relayed result records the delivery over
it.

### Workers

A worker turn is one run of the router's runtime CLI in its non-interactive
mode, resuming the task's own session (`worker-executor.mjs`):

| Runtime | Turn | First turn of a task that follows one on the same runtime |
|---|---|---|
| Pi | `pi --mode json -p --session-id <task> ...` | `--fork <its session file>` |
| Codex | `codex exec --json ...`, then `codex exec resume <thread>` | `codex exec ... fork <its thread>` |
| Claude Code | `claude --print --output-format stream-json --session-id/--resume <task>` | `--resume <its session> --fork-session --session-id <task>` |

A task that follows another starts from a copy of that task's session when the
session is still there and at most 4 MB, else from the summary in its brief.
The copy is meant to keep the provider's prefix cache, and how much it keeps
depends on the runtime. First model call of a copy into another task's
directory, measured:

- **Pi: 97% cached.** Pi names the working directory at the end of the system
  prompt and records a new one as a later system update, but folds that update
  into the first system message for providers without mid-conversation system
  messages (`openai-completions` by default): one changed character before all
  the history, and only 2048 tokens were cached. So every worker's system
  prompt names the same directory, "the task directory your brief names"
  (`pi-mcp-extension.mjs`, on `before_agent_start`); the brief names it, and
  the worker's shell and file tools start there.
- **Claude Code: the whole copy.** It appends the change of directory after
  the copied history.
- **Codex: only its fixed prefix** (system prompt and tools, about 12k
  tokens). Codex keys the prompt cache by thread (`prompt_cache_key`), which a
  fork changes, and nothing sets it. Resuming a task's own thread, as each
  later turn and round does, is 99% cached.

No process runs between turns, so an idle task holds nothing; Durable,
OpenCode, TraeX and Ark have no worker mode, and `task_create` tells the router
to handle the request itself there. A task keeps the runtime it started on.
Three turns run at once, plus one slot only a follow-up to an existing task may
take. Each task works in a directory under `<workspace>/tasks/` (see Task
directories).

A worker's own shell is not capped like the router's: it may build, test and
wait. Pi's `bash` alone has no default timeout, so a Pi worker's command
without a `timeout` is stopped after 300 seconds and none may ask for more than
3600; the worker's policy says so, for builds and test runs that need longer.
Codex returns a long command's output after at most 30 seconds and lets it run
on; Claude Code gives a command 2 minutes unless it asks for up to 10.
Every worker is told to search with the bundled `rg` rather than `grep -r`, in
its own directory first, and never over a whole home directory, `/`, or the
OpenViking SuperAgent installation and data. Its working directory is its task
directory, but nothing else confines it.

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
if any were not read, the worker runs again (up to three times). Past that the
report goes out as the result so far, marked `working=1`, while the worker goes
on with the newest input. A turn that failed for a passing reason (429, 5xx, a
reset connection) runs again, twice at most, unless its worker had already
filed its final report: that report stands, the inputs the worker had taken by
then are acknowledged, and only lines it took after the report make a catch-up
turn. A model error Pi retried past by itself does not fail the turn.

A successful turn records a session checkpoint. A failed or interrupted turn
starts a new session with the persisted request history, routing note, previous
summary/draft and artifacts, and an instruction to check existing work before
repeating outside actions. A preallocated session ID or turn counter alone is
never evidence that Claude can resume a session. Recovery can repeat work;
outside actions are not guaranteed to run exactly once.

### Task directories

A task works in `<workspace>/tasks/<id>/`, where its worker clones, installs
and builds; its policy says to keep all of that inside the directory. Nothing
deleted these directories, and each can hold a clone, `node_modules`, virtual
environments and build output. The download caches (`~/.npm`, pip, uv) are
already shared, since workers use the same home.

What an ended task left is deleted in stages, counted from when it ended:

| After | Deleted | Kept |
|---|---|---|
| 3 days (`OV_SUPERAGENT_TASK_DIR_PRUNE_DAYS`) | Dependency folders: `node_modules`, Python virtual environments (a folder with `pyvenv.cfg`), `__pycache__` and the test/type-checker caches, `.tox`, `.nox`, `.gradle`, `.next`, `.nuxt`, `.turbo`, `.parcel-cache`, and `target` next to `Cargo.toml` or `pom.xml` | Sources, changes, `.git`, results |
| 14 days (`OV_SUPERAGENT_TASK_DIR_REMOVE_DAYS`) | The whole directory, and its worker's sessions | The task record |

Past `OV_SUPERAGENT_TASK_DIR_BUDGET_GB` (20 by default) for all task
directories, dependency folders go first and whole directories after, those of
the task that ended longest ago first. 0 turns each limit off. A directory a
live task works in, or reads as its parent's, is never touched, nor are those
of a stopped task until its worker process has exited, nor anything outside
`<workspace>/tasks/t<n>`. Links are never followed: a folder is deleted only
if it is still a real directory reached with no link on the way, and the trash
is removed as a whole, so a link in its place goes as a link. A task that
follows one whose directory was deleted starts from its report, as when its
session cannot be forked; one whose parent lost its dependency folders is told
to install again what it needs.

The daemon sweeps a minute after it starts and every hour. Measuring runs in
the background; choosing what to delete and moving it to a trash folder next
to it happen in one step, so a task never starts in, or reads, a directory
being deleted, and the trash is emptied after. A round that reopens a task
makes it live again in that step's sense: what was already moved away is gone
(the round is told so), nothing more is. A directory a task used while
it was being measured waits for the next sweep. `/tasks` shows how much the
task directories took at the last sweep. Claude Code and Codex keep their own
session files in their homes; those are not swept.

### What the router sees

```
[task report] task=t3 round=2 state=done title="排查飞书名字泄露" requester=陈锦
origin: chat=oc_... thread=omt_... source=lark msg=om_... event=...
summary:
| ...
draft:
| ...
```

`round=` appears once a task has had more than one round; the origin is that
round's. Worker text is quoted (`| `), so it cannot pass for an input line. The router
receives the full stored draft (up to the 20,000-character reporting limit)
and relays it with `reply_message` to the origin `msg=`.

Ending a task and selecting its final report for delivery are committed together
in SQLite (`task_report_deliveries`). The inbox row, report's delivery timestamp
and report-to-inbox receipt then commit in one transaction, before waking the
router. The report ID is the idempotency key; consuming the inbox row does not
erase its receipt. Failed writes retry automatically and pending deliveries are
recovered at startup, without rerunning the worker. Task reports in the inbox
do not expire under the incoming-event age limit.

A report is **settled** when the router answers for its task, not when it was
shown: a final delivery or `stay_silent` that names the task (`task_ids`,
offered on those tools only with this scheduler) or is made on its origin. An
answer to a later input of the task settles nothing, since it may be an
acknowledgement. Each report carries a receipt (`task-report:<id>`) that the
runtimes return when a router turn ends:

- a finished turn that left it unsettled offers it once more, marked
  `undelivered=1`; past that it is logged as `task.report_unrelayed`;
- a failed, abandoned or stopped turn offers it again as it was (up to three
  times), and so does a restart after a turn that held it;
- a delivery naming a task whose worker is working again (new input from the
  requester's side came in after that report) is refused: its next report
  replaces the old one. A `working=1` report is the exception until it is
  settled: it went out on purpose, past the catch-up turns.

So a report is neither lost to a failed router turn nor relayed twice after one.
The settlement records how (`settled_by`: the tool, or `unrelayed`) and, for a
delivery, its outbox row, which is what `task_list` and `/tasks` show.

**Progress questions.** "Is mine done?" is answered from `task_list` without new
work: `for_event` with the question's `event=` lists what its sender asked for,
newest first, ended tasks included. In a group that is only what they asked in
that chat; in a direct chat, everything they asked for. A result already sent is
pointed to (when, which message) and its conclusion restated; a draft that was
never sent is sent then. Being the same person is not a routing signal (see the
hint above): it only scopes this lookup.
Settling on "the router turn succeeded" instead would do both: a turn that
skipped a report would bury it, and a turn that relayed it and then failed would
offer it again with nothing to say it was already sent (the outbox key includes
the reply text, so a rewritten reply is a new message).

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

The owner sees the tasks with `/tasks` in a direct chat (live ones, recent
ended ones, slots, whether each result reached the requester), one task with
`/tasks t12`, and stops one with `/tasks stop t12`; `/status` counts the tasks
at work. The Core answers these without the model.

Activities: `task.created` (with `parent`), `task.reopened` (round, the
status it had, what it found of its directory and session),
`task.turn_started`, `task.turn_completed` (with usage), `task.reported`,
`task.handed_back`, `task.round_handed_back`, `task.report_unrelayed`,
`task.ended`, `task.dirs_cleaned` (what a sweep deleted and freed).
