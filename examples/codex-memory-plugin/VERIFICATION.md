# Verification SOP — codex plugin (v0.3.0)

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

Expect: `{"systemMessage":"appended 2 turn(s) to OpenViking session <UUID>"}`.

State file:
```bash
cat $STATE_DIR/state/verify-sess.json
# {"codexSessionId":"verify-sess","ovSessionId":"<UUID>","capturedTurnCount":2,...}
```

OV side:
```bash
OPENVIKING_CONFIG_FILE=$OV_CONF ov read viking://session/<UUID>/messages.jsonl
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
`viking://session/<UUID>/messages.jsonl` — 4 records now.

## 4. PreCompact — commit + reset

```bash
echo '{"session_id":"verify-sess","transcript_path":"'"$STATE_DIR"'/transcript.jsonl","trigger":"manual"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/pre-compact-capture.mjs
```

Expect: `pre-compact commit: <UUID> → N memory item(s) extracted (archived)`.

State file: `ovSessionId` is now `null`, `capturedTurnCount` stays at 4.

OV side:
```bash
OPENVIKING_CONFIG_FILE=$OV_CONF ov ls viking://session/<UUID>
# messages.jsonl is now size 0 (archived)
# history/archive_001/ exists with the committed messages
OPENVIKING_CONFIG_FILE=$OV_CONF ov read viking://session/<UUID>/history/archive_001/messages.jsonl
```

## 5. Post-compact Stop — fresh OV session

Append more turns and run Stop. A new OV session UUID should appear:

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

Expect: `appended 2 turn(s) to OpenViking session <NEW_UUID>` — different
from step 4's UUID.

## 6. SessionStart(source=clear) — orphan commit on `/clear`

`/clear` orphans the previous session and starts a new one. The
SessionStart hook commits any state files whose codexSessionId differs
from the new one.

```bash
# Simulate /clear: the new SessionStart payload carries a brand-new session_id
echo '{"session_id":"clear-after-verify","source":"clear","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/session-start-commit.mjs
```

Expect: `/clear: committed N prior OpenViking session(s), …`. After this
the state dir contains nothing except (optionally) the just-cleared
session's fresh state. OV side: the post-compact `<NEW_UUID>` from step
5 is now archived (`messages.jsonl` size 0, `history/archive_001/`
exists).

Verify the negative path too — `source=startup` and `source=resume` MUST
be no-ops:

```bash
echo '{"session_id":"x","source":"startup","cwd":"/tmp","model":"x","permission_mode":"default","transcript_path":null,"hook_event_name":"SessionStart"}' \
  | OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/session-start-commit.mjs
# Expect: {} (no commit; short reconnects fire startup/resume too)
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
