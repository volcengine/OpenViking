# OpenViking Healthcheck Tool

`ov-healthcheck.py` verifies the OpenClaw + OpenViking plugin pipeline end-to-end. It drives a real Gateway conversation, then inspects the resulting OpenViking state.

## Quick Start

```bash
python examples/openclaw-plugin/ov-healthcheck.py
```

No extra dependencies — standard library only. Addresses and tokens are auto-discovered from `openclaw.json`.

## Prerequisites

### Gateway HTTP Endpoints Must Be Enabled

Phase 1 conversation injection requires the Gateway's `/v1/responses` endpoint, which is **disabled by default**. Enable it in `openclaw.json`:

```json
{
  "gateway": {
    "http": {
      "endpoints": {
        "chatCompletions": {
          "enabled": true
        },
        "responses": {
          "enabled": true
        }
      }
    }
  }
}
```

Restart Gateway to apply:

```bash
openclaw gateway restart
```

Without this, Phase 1 will fail with:

```
[FAIL] Chat turn 1 failed (POST http://127.0.0.1:18789/v1/responses failed with HTTP 404: Not Found)
```

## Expected Output

A successful run looks like this:

```
OpenViking Plugin Healthcheck
Gateway: http://127.0.0.1:18789
OpenViking: http://127.0.0.1:1933
...

[PASS] OpenClaw config discovered (...)
[PASS] plugins.slots.contextEngine is openviking
[PASS] Gateway health check succeeded
[PASS] OpenViking health check succeeded

Phase 1: real conversation
[PASS] Chat turn 1 succeeded (reply_len=151)
[PASS] Chat turn 2 succeeded (reply_len=131)
[PASS] Chat turn 3 succeeded (reply_len=115)
[PASS] Chat turn 4 succeeded (reply_len=77)

Phase 2: OpenViking session inspection
[PASS] Probe session located in OpenViking (...)
[PASS] Captured session context contains the probe marker
[PASS] Captured session context contains seeded facts (go,postgresql,redis,70)

Phase 3: commit, context, and memory checks
[PASS] OpenViking commit accepted (accepted)
Waiting up to 300s for commit, archive, and memory extraction...
[PASS] Session commit_count is greater than zero (1)
[PASS] Memory extraction produced results (total=1)
[PASS] Context endpoint returned latest_archive_overview

Phase 4: follow-up through Gateway
[PASS] Same-session follow-up recalled earlier facts (go,postgresql,redis,70)
[PASS] Fresh-session recall returned seeded stack facts (...)

Phase 5: cleanup
[PASS] Deleted synthetic session (...)
[PASS] Deleted synthetic memory (viking://user/default/memories/...)

Summary
PASS=20 WARN=0 FAIL=0 SKIP=0

Healthcheck passed.
```

Phase 3 waits for the async commit to finish (up to 300s by default). This is normal — commit involves LLM calls for archiving and memory extraction.

All test messages are prefixed with `[OPENVIKING-HEALTHCHECK]` and carry a unique probe marker, but the body of the conversation is written like normal memory-bearing user dialogue. The seeded Kafka topic, callback host, and debug tag are also derived from that probe so the run creates uniquely identifiable artifacts. By default the script deletes the synthetic sessions and only the probe-scoped leaf memories from the current run. Shared summary files such as `profile.md`, preferences, or abstract files are intentionally left alone even if they contain synthetic facts, to avoid deleting mixed real user memory. Use `--keep-artifacts` only when you explicitly want to inspect the leftovers for debugging.

## How It Works

The script validates the plugin by injecting a controlled conversation and tracing its effects through the system.

**Probe marker** — Each run generates a unique random marker (e.g. `probe-a1b2c3d4`). This marker is embedded in the first message and later used to locate the exact session in OpenViking, ensuring the script never confuses its own test session with real user data.

**Phase 1: Conversation injection** — The script sends 4 probe-tagged messages through the Gateway `/v1/responses` endpoint, simulating a real user conversation. The messages contain known facts (tech stack, Kafka topic, service address, etc.) that serve as verification anchors later. The `[OPENVIKING-HEALTHCHECK]` prefix and the probe marker identify the run, while the body remains normal conversation content so memory extraction is exercised realistically.

**Phase 2: Capture verification** — After a short wait (`--capture-wait`), the script queries the OpenViking sessions API and scans each session's context for the probe marker. Finding the marker proves that the plugin's `afterTurn` hook successfully captured the conversation from Gateway into OpenViking.

**Phase 3: Commit and memory verification** — The script triggers a commit via the OpenViking API, then polls (up to `--commit-wait` seconds) until three conditions are all met:
- `commit_count > 0` — the commit completed
- `latest_archive_overview` exists — the conversation was archived
- `memories_extracted > 0` — memory extraction produced results

This confirms the full async pipeline: conversation archiving, overview generation, and memory extraction.

**Phase 4: Recall verification** — Two final questions are sent through Gateway:
1. A same-session follow-up asking about facts from the earlier turns. The reply is checked for keywords (`go`, `postgresql`, `redis`, `70`). This verifies context continuity within a session.
2. A fresh-session question (new user ID) asking about facts that should only be available through memory recall. The reply is checked for the run-specific Kafka topic and callback host derived from the probe. This verifies that `autoRecall` is injecting stored memories into new sessions.

**Phase 5: Cleanup** — Unless `--keep-artifacts` is set, the script deletes the synthetic OpenViking sessions it created and removes synthetic memories only when they match the current run's probe-derived facts under the same user space. The memory root is resolved from the current runtime user space. This keeps repeated healthcheck runs from polluting the shared memory space without risking unrelated memories.

**Keyword matching** — The script does not require exact reproduction. It lowercases the model's reply and checks whether at least 2 out of 4 (or 2 out of 2) target keywords appear. This tolerates paraphrasing while still catching complete recall failures.

## Output Meaning

- `PASS` — verified working
- `INFO` — extra context, not a failure
- `WARN` — core path up, but this check was inconclusive
- `FAIL` — clear issue, script exits non-zero

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--gateway <url>` | auto | Gateway base URL |
| `--openviking <url>` | auto | OpenViking base URL |
| `--token <token>` | auto | Gateway bearer token |
| `--openviking-api-key <key>` | auto | OpenViking API key |
| `--agent-id <id>` | `default` | OpenViking agent id |
| `--user-id <id>` | random | User id for the test session |
| `--openclaw-config <path>` | auto | Path to `openclaw.json` |
| `--chat-timeout <seconds>` | `120` | Timeout per Gateway chat request |
| `--commit-wait <seconds>` | `300` | Max wait for commit, archive, and memory extraction |
| `--capture-wait <seconds>` | `4` | Wait after chat before inspecting OpenViking |
| `--delay <seconds>` | `1` | Delay between chat turns |
| `--session-scan-limit <n>` | `0` (all) | Max sessions to scan for probe (0 = scan all) |
| `--insecure` | off | Skip SSL certificate verification (self-signed certs) |
| `--keep-artifacts` | off | Preserve the synthetic sessions and memories created by this run |
| `--strict-warnings` | off | Exit non-zero on warnings |
| `--json-out <path>` | — | Write JSON report to file |
| `--verbose` / `-v` | off | Print extra debug output |

## Troubleshooting

### `Gateway health check failed`

```bash
openclaw gateway status
curl http://127.0.0.1:<port>/health
openclaw logs --follow
```

### `OpenViking health check failed`

```bash
curl http://127.0.0.1:<port>/health
cat ~/.openviking/ov.conf
```

Check `storage.workspace/log/openviking.log` for errors.

### `Chat turn 1 failed (POST /v1/responses failed with HTTP 404: Not Found)`

The most common Phase 1 failure. Gateway's `/v1/responses` and `/v1/chat/completions` endpoints are **disabled by default**. Enable them under `gateway.http.endpoints` in `openclaw.json`:

```json
{
  "gateway": {
    "http": {
      "endpoints": {
        "chatCompletions": { "enabled": true },
        "responses": { "enabled": true }
      }
    }
  }
}
```

Restart Gateway:

```bash
openclaw gateway restart
```

### `Probe session not found in OpenViking`

The conversation completed but the plugin did not persist it.

```bash
openclaw config get plugins.slots.contextEngine
openclaw config get plugins.entries.openviking.config
openclaw logs --follow
```

Common causes: plugin not loaded, `autoCapture` disabled, routing or write failure.

### `Session commit_count is still zero after waiting`

Commit is async and involves LLM calls. If it times out:

1. Check if the commit task is still running: `curl http://127.0.0.1:<port>/api/v1/tasks`
2. If running, re-run with `--commit-wait 600` to allow more time
3. If stuck, check `storage.workspace/log/openviking.log` for errors
4. Verify the LLM backend is reachable and responding

### `Context endpoint has no archive overview after waiting`

Archive overview is generated during commit. If commit succeeded but overview is missing:

```bash
curl "http://127.0.0.1:<port>/api/v1/sessions/<session_id>/context?token_budget=128000"
```

If empty there too, the issue is in OpenViking. If correct manually, verify the script points at the right instance.

### `Fresh-session recall was inconclusive`

Usually not a full breakage. Common reasons: `autoRecall` disabled, memory extraction not finished, model didn't use recalled facts this run. Re-run once first.

### `Direct backend memory search returned no results`

This is `INFO`, not a failure. If fresh-session recall answers correctly, the pipeline is healthy.

## Recommended Debug Order

1. Confirm `gateway.http.endpoints` is configured as described in Prerequisites
2. Check `plugins.slots.contextEngine` is `openviking`
3. Check Gateway `/health`
4. Check OpenViking `/health`
5. Check `openclaw logs --follow`
6. Check OpenViking log
7. Look at the specific failed phase in script output
