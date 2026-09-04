# Verification SOP — codex plugin (v0.6.0)

End-to-end smoke test against a live OpenViking server. Run this whenever the
hook scripts change. Takes ~3 minutes; the only async wait is OV's memory
extractor (~30–60 s).

## 0. Prereqs

- `ov` CLI installed and reachable
- `~/.openviking/ovcli.conf` (or a per-tenant variant like `ovcli.conf.bob`)
  pointing at the OV server you want to write to. The plugin sends
  `X-API-Key`, `X-OpenViking-Account`, `X-OpenViking-User` from this file.
- Node.js 22+

```bash
export OV_CONF=$HOME/.openviking/ovcli.conf.bob   # or whichever tenant
export PLUGIN=/path/to/OpenViking/examples/codex-memory-plugin
export STATE_DIR=/tmp/codex-plugin-verify
rm -rf "$STATE_DIR" && mkdir -p "$STATE_DIR"
```

## 1. Stop hook — first turn appends

```bash
cat > "$STATE_DIR/transcript.jsonl" <<'EOF'
{"payload":{"role":"user","content":"My favorite color is fuchsia."}}
{"payload":{"role":"assistant","content":"Got it — fuchsia noted."}}
EOF

OPENVIKING_CONFIG_FILE=$OV_CONF \
OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
CODEX_PLUGIN_ROOT=$PLUGIN \
echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl"}' \
  | node $PLUGIN/scripts/auto-capture.mjs
```

Expect: `{"systemMessage":"appended 2 turn(s) to OpenViking session cx-verify-sess"}`.

State file:
```bash
cat $STATE_DIR/state/verify-sess.json
# {"codexSessionId":"verify-sess","ovSessionId":"cx-verify-sess","capturedTurnCount":2,...}
```

OV side:
```bash
OPENVIKING_CONFIG_FILE=$OV_CONF ov read viking://user/sessions/cx-verify-sess/messages.jsonl
# 2 JSONL records: user "fuchsia", assistant "noted"
```

## 2. Stop hook idempotency — re-run without changes is a no-op

```bash
echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/auto-capture.mjs
```

Expect: `{}` (no new turns). `capturedTurnCount` still 2.

## 3. Stop hook — incremental append

Append two more turns to the transcript and re-run:

```bash
cat >> "$STATE_DIR/transcript.jsonl" <<'EOF'
{"payload":{"role":"user","content":"Actually, mint green."}}
{"payload":{"role":"assistant","content":"Updated to mint green."}}
EOF

echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/auto-capture.mjs
```

Expect: `appended 2 turn(s)` (only the new ones). Re-read
`viking://user/sessions/cx-verify-sess/messages.jsonl` — 4 records now.

## 4. PreCompact — commit + reset

```bash
echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl","trigger":"manual"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/pre-compact-capture.mjs
```

Expect: `OpenViking session cx-verify-sess is committed`.

State file: `ovSessionId` is now `null`, `capturedTurnCount` stays at 4.

OV side:
```bash
OPENVIKING_CONFIG_FILE=$OV_CONF ov ls viking://user/sessions/cx-verify-sess
# messages.jsonl is now size 0 (archived)
# history/archive_001/ exists with the committed messages
OPENVIKING_CONFIG_FILE=$OV_CONF ov read viking://user/sessions/cx-verify-sess/history/archive_001/messages.jsonl
```

## 5. Post-compact Stop — same deterministic OV session id

Append more turns and run Stop. The same OV session id should appear:

```bash
cat >> "$STATE_DIR/transcript.jsonl" <<'EOF'
{"payload":{"role":"user","content":"After compaction: I prefer serif fonts."}}
{"payload":{"role":"assistant","content":"Noted serif preference."}}
EOF

echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/auto-capture.mjs
```

Expect: `appended 2 turn(s) to OpenViking session cx-verify-sess`.

## 6. SessionEnd commit + SessionStart fallback sweep

`SessionEnd` (Codex ≥ 0.145) is the primary commit path; `SessionStart`
`source=startup|clear` sweeps only what `SessionEnd` could not reach
(matcher = `clear|startup|resume`). `source=resume` never commits or sweeps;
all three sources inject the shared profile/background block by default, and
resume may additionally inject latest archive context if a committed archive
exists. Set `OPENVIKING_NO_AUTO_INJECT=1` when a cleanup-only smoke test needs
the historical `{}` output.
See `DESIGN.md` §2 + §5 for the full decision tree.

### 6a. SessionEnd — catch-up append + commit

After step 5, `verify-sess` has a live `cx-verify-sess` and a cursor of 6.
Append two more turns that `Stop` never saw, then run the worker path
directly (`OV_HOOK_WORKER=1` skips the detach, so the run is observable).

```bash
cat >> "$STATE_DIR/transcript.jsonl" <<'EOF'
{"payload":{"role":"user","content":"One last thing: I ship on Fridays."}}
{"payload":{"role":"assistant","content":"Noted — Friday releases."}}
EOF

echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl","cwd":"/tmp","hook_event_name":"SessionEnd","reason":"other"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    OV_HOOK_WORKER=1 \
    OPENVIKING_DEBUG=1 \
    node $PLUGIN/scripts/session-end.mjs
```

Expect: `{}` on stdout (SessionEnd output is ignored by Codex). In
`~/.openviking/logs/codex-hooks.log`: `appended_catchup` with `added: 2`,
then `commit` with `"reason":"session_end"`.

```bash
cat $STATE_DIR/state/verify-sess.json
# ovSessionId is null, capturedTurnCount is 8 (cursor preserved for resume)
ls $STATE_DIR/state/verify-sess.ended.*   # no such file — the marker was cleared
```

### 6b. SessionEnd parent — marker first, work detached

```bash
time (echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl","cwd":"/tmp","hook_event_name":"SessionEnd","reason":"other"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/session-end.mjs)
```

Expect: `{}` well under 1 s — the parent only writes
`$STATE_DIR/state/verify-sess.ended.<timestamp>` and detaches the worker. The worker
finds nothing live to commit and removes the marker again shortly after.

### 6c. `.ended.<ts>` marker → next SessionStart commits immediately

```bash
# A live session whose SessionEnd worker never finished: fresh timestamp,
# so only the marker can make the sweep pick it up.
NOW=$(node -e 'console.log(Date.now())')
mkdir -p "$STATE_DIR/state"
cat > "$STATE_DIR/state/sess-ended.json" <<EOF
{"codexSessionId":"sess-ended","ovSessionId":"cx-sess-ended","capturedTurnCount":2,"createdAt":$NOW,"lastUpdatedAt":$NOW}
EOF
printf '%s' "$NOW" > "$STATE_DIR/state/sess-ended.ended.$NOW"

echo '{"session_id":"sess-ccc","source":"startup","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    OPENVIKING_DEBUG=1 \
    node $PLUGIN/scripts/session-start-commit.mjs
```

Expect: `systemMessage` reports `cx-sess-ended` committed, and the log shows
`"reason":"ended_retry"` despite the fresh `lastUpdatedAt`. Afterwards
`sess-ended.json` has `ovSessionId: null` with `capturedTurnCount: 2`, and
`sess-ended.ended.$NOW` is gone.

### 6c-2. Held lock → sweep skips instead of racing

```bash
# Re-arm the same session, then hold its lock as a concurrent worker would.
cat > "$STATE_DIR/state/sess-ended.json" <<EOF
{"codexSessionId":"sess-ended","ovSessionId":"cx-sess-ended","capturedTurnCount":2,"createdAt":$NOW,"lastUpdatedAt":$NOW}
EOF
printf '%s' "$NOW" > "$STATE_DIR/state/sess-ended.ended.$NOW"
mkdir "$STATE_DIR/state/sess-ended.lock"

echo '{"session_id":"sess-fff","source":"startup","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    OPENVIKING_DEBUG=1 \
    node $PLUGIN/scripts/session-start-commit.mjs

rmdir "$STATE_DIR/state/sess-ended.lock"
```

Expect: the log shows `sweep_skip` with `"reason":"locked by another writer"`,
no `/commit` is issued, and `sess-ended.json` keeps `ovSessionId` and its
marker. (A lock older than 5 min is treated as stale and taken over instead.)

### 6d. Idle-TTL sweep

```bash
# A live state with no marker, backdated past IDLE_TTL_MS (default 30 min):
# the signal/crash/old-Codex path.
OLD=$(node -e 'console.log(Date.now() - 60*60*1000)')   # 1 hour ago
cat > "$STATE_DIR/state/sess-aaa.json" <<EOF
{"codexSessionId":"sess-aaa","ovSessionId":"cx-sess-aaa","capturedTurnCount":2,"createdAt":$OLD,"lastUpdatedAt":$OLD}
EOF

echo '{"session_id":"sess-ddd","source":"startup","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/session-start-commit.mjs
```

Expect: log shows `sweep` for `sess-aaa` with `"reason":"idle_ttl"`, followed
by the commit. `sess-aaa.json` is still present, with `ovSessionId: null` and
`capturedTurnCount: 2` for resume. Any other state file that is both fresh and
unmarked is left untouched — that is the whole point of dropping the old
active-window heuristic.

### 6d-2. Cursor retention

```bash
# sess-aaa is now cursor-only (ovSessionId: null, capturedTurnCount: 2).
# Re-run the same SessionStart with a 1 s committed TTL.
echo '{"session_id":"sess-eee","source":"startup","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    OPENVIKING_CODEX_COMMITTED_TTL_MS=1000 \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    OPENVIKING_DEBUG=1 \
    node $PLUGIN/scripts/session-start-commit.mjs
```

Expect: log shows `state_retire` for `sess-aaa` and the file is gone. With
the default 30-day TTL it stays, and no `/commit` is issued for it either
way — a cursor-only state has nothing left to commit.

### 6e. `source=resume` → no commit/sweep; optional archive inject

```bash
echo '{"session_id":"any","source":"resume","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/session-start-commit.mjs
# Expect without an existing archive: hookSpecificOutput.additionalContext
# containing the OpenViking profile block.
# Expect with an existing archive for cx-any: the same additionalContext also
# contains "OpenViking session archive digest" and a
# viking://user/sessions/cx-any/history/ URI.
```

### 6f. Compressor profile detect can be disabled for hook smoke tests

```bash
echo '{"session_id":"any","source":"startup","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP=0 \
    OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/session-start-commit.mjs
# Expect: normal SessionStart behavior without spawning `codex exec` for model probing.
```

## 7. Memory extraction landed in user namespace

Wait ~60 s for OV's extractor, then:

```bash
OPENVIKING_CONFIG_FILE=$OV_CONF ov ls viking://user/<your-user>/memories/
OPENVIKING_CONFIG_FILE=$OV_CONF ov read viking://user/<your-user>/memories/profile.md
```

Expect new entries describing the captured preferences (favorite color,
serif fonts, etc.) with timestamps from this run.

## 8. Codex CLI smoke test (requires codex auth)

```bash
codex plugin marketplace add /path/to/OpenViking-codex-marketplace   # if not already
codex                                                                 # interactive
# Have a brief conversation that mentions a clear preference,
# then /compact (manual PreCompact) to force a commit, then exit.
```

Verify with steps 4 + 7 above.

---

**Cleanup**: `rm -rf $STATE_DIR && rm -rf ~/.openviking/codex-plugin-state/verify-sess.json`
