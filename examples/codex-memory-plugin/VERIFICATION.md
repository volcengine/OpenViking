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

## 6. Idle-sweep — graceful-exit commit

The sweep only commits sessions whose state file is older than the idle TTL
(default 30 min). For verification we shorten the TTL to 1 second:

```bash
# Backdate the verify-sess state to force-stale it
python3 -c "
import json, sys
p = '$STATE_DIR/state/verify-sess.json'
s = json.load(open(p))
s['lastUpdatedAt'] = 1
open(p, 'w').write(json.dumps(s))
"

# Run a Stop for a brand-new session_id; idle-sweep at the tail commits the stale one
echo '{"session_id":"sweep-trigger","transcript_path":"/tmp/empty-nonexistent.jsonl"}' \
  | OPENVIKING_CODEX_IDLE_TTL_MS=60000 \
    OPENVIKING_CONFIG_FILE=$OV_CONF \
    OPENVIKING_CODEX_STATE_DIR=$STATE_DIR/state \
    CODEX_PLUGIN_ROOT=$PLUGIN \
    node $PLUGIN/scripts/auto-capture.mjs

ls $STATE_DIR/state/
# verify-sess.json is gone; only sweep-trigger.json remains
```

OV side: the post-compact `<NEW_UUID>` from step 5 is now archived (its
`messages.jsonl` is size 0; `history/archive_001/` exists).

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
