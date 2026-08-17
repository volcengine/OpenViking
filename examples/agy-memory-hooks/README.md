# OpenViking Memory Hooks for Antigravity CLI (agy)

Lifecycle hooks that give the Antigravity CLI (`agy`) automatic memory recall and
session capture against OpenViking, mirroring the TRAE CLI adapter
([`examples/trae-cli-memory-hooks`](../trae-cli-memory-hooks)).

Contrast with the Claude Code plugin: agy capture does **not** read prompt fields
from the hook payload. It parses the session transcript that agy writes to
`transcriptPath` (CLI: `~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`)
and stores sessions with the `ag-` prefix.

This adapter depends on the shared hook runtime in
[`examples/memory-plugin-shared/lib`](../memory-plugin-shared/lib) (same commit as
this adapter), which reads credentials from `~/.openviking/ovcli.conf` /
`ov.conf` exactly like the other harness adapters.

## Events

| agy event       | Adapter event    | Script                       |
| --------------- | ---------------- | ---------------------------- |
| `PreInvocation` | `pre-invocation` | `auto-recall.mjs`            |
| `Stop`          | `stop`           | `auto-capture.mjs`           |
| `SessionStart`  | `session-start`  | `session-start.mjs` (opt-in) |

- `PreInvocation` replays pending writes, loads the actor profile once per
  session, then recalls for the latest user turn from the transcript and injects
  it via `injectSteps[].ephemeralMessage`.
- `Stop` parses the transcript, captures turns that were not captured yet and
  commits them to the session.

`SessionStart` is implemented but not registered in `hooks/hooks.json`: the event
is not part of the documented agy hook API, and `PreInvocation` already covers
the profile bootstrap. Register it only if your agy build emits it.

## Tuning

`~/.openviking/ov.conf` section `agy`:

```json
{
  "agy": {
    "bypassSessionPatterns": ["**sensitive-project**"]
  }
}
```

`bypassSessionPatterns` follows env-first precedence
(`OPENVIKING_BYPASS_SESSION_PATTERNS` CSV overrides the `ov.conf` array);
`enabled`, `autoRecall`, `autoCapture`, `workspacePeer`, `scoreThreshold` and
`recallLimit` can also be set in this section.

## Transcript parsing

`source` tells you who produced a record, `type` tells you what the record is.
Tool results carry the model's own source (`MODEL/VIEW_FILE`,
`MODEL/RUN_COMMAND`, …) and IDE edit notices carry the user's
(`USER_EXPLICIT/CODE_ACTION`), so selecting on `source` alone would push raw
command output and file dumps into memory. Turns are therefore taken from
`USER_INPUT` and `PLANNER_RESPONSE` records only; a record with no `type` is
allowed through so other agy builds keep working.

agy also appends an `<ADDITIONAL_METADATA>` block carrying the local time to
user prompts, and wraps them in `<USER_REQUEST>`; both are stripped so stored
turns hold the prompt itself.

Ordering follows `step_index`, not file order — agy flushes the transcript
asynchronously and records can land out of order around `Stop`.

Deduplication is by hash of `stepKey + role + content` in the hook state rather
than a monotonic step cursor, so re-scans are idempotent and no turn is silently
dropped.

## Install (manual, until install.sh supports `agy`)

```bash
# 1. copy the adapter into a stable location (use the AGY_ROOT value below)
mkdir -p "$HOME/.openviking/agent-integrations/agy"
rsync -a --delete examples/agy-memory-hooks/ "$HOME/.openviking/agent-integrations/agy/"

# 2. register the global hook: merge hooks/hooks.json into ~/.gemini/config/hooks.json
#    replacing __OPENVIKING_AGY_ROOT__ with the absolute path above
#    (commands run via sh -c; ~ is expanded; node must be on PATH)

# 3. OPTIONAL bypass patterns for sensitive projects: add the "agy" section to ov.conf
```

## Tests

```bash
node examples/agy-memory-hooks/scripts/agy-hooks.test.mjs
```

See the [Antigravity CLI integration guide](../../docs/en/agent-integrations/16-agy.md).
