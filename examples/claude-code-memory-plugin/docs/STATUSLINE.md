# Personalizing the OpenViking Statusline

A guide for an AI assistant (or you) to **customize** the OpenViking statusline beyond the defaults the installer sets up. This doc focuses on **personalization recipes** that env vars don't cover. For the canonical environment-variable reference and segment glossary, see `$REPO/docs/en/agent-integrations/02-claude-code.md` (path notation defined just below).

The intent is to give an assistant enough orientation to make tasteful changes without prescribing every tweak. If a user asks for something unusual, prefer the smallest local edit over a configurable knob.

---

## Where everything lives

The OpenViking repo and plugin code are checked out to a known location by the installer. Throughout this doc:

- **`$REPO`** = the OpenViking repo root. Default: `~/.openviking/openviking-repo` (override via `OPENVIKING_REPO_DIR` at install time). Verify with `ls "$REPO/examples/claude-code-memory-plugin"` — if that path is wrong, find the real one with `jq -r '.statusLine.command' ~/.claude/settings.json` (the registered command points into the plugin) or `find ~ -path '*/claude-code-memory-plugin/scripts/statusline.mjs' 2>/dev/null`.
- **`$PLUGIN`** = `$REPO/examples/claude-code-memory-plugin`. The plugin's own root.
- **`$STATE`** = `~/.openviking/state` (override via `OPENVIKING_HOME`). Where hook-written JSON snapshots live.

Resolve `$REPO` once before editing anything — every relative path below is anchored to it.

| Concern                                    | File                                              |
|--------------------------------------------|---------------------------------------------------|
| Composing the line / segment order         | `$PLUGIN/scripts/statusline.mjs`                  |
| Server-reachability probe + cache          | `$PLUGIN/scripts/lib/server-probe.mjs`            |
| Atomic state read/write under `$STATE/`    | `$PLUGIN/scripts/lib/state.mjs`                   |
| Recall summary (writes `last-recall.json`) | `$PLUGIN/scripts/auto-recall.mjs`                 |
| Capture summary (writes `last-capture.json`, `daily-stats.json`) | `$PLUGIN/scripts/auto-capture.mjs` |
| Resume/compact event marker (`last-session-event.json`) | `$PLUGIN/scripts/session-start.mjs`  |
| Where CC actually invokes the script       | `~/.claude/settings.json` `.statusLine.command`   |
| Per-host / per-shell env overrides         | `~/.zshrc` (or equivalent) — env vars beat config files |

State files live at `$STATE/`. They are small JSON snapshots written atomically (temp + rename); deleting them clears the corresponding segment until the next hook fires.

---

## Recipes

These are sketches, not scripts to copy verbatim. The composer in `$PLUGIN/scripts/statusline.mjs` is small enough that an assistant can read it end-to-end before editing.

### Drop a specific segment

Each segment in `$PLUGIN/scripts/statusline.mjs` is gated by an `if`. To make a segment user-suppressible, wrap its branch in an env-var check (e.g. `process.env.OPENVIKING_STATUSLINE_HIDE_DAILY === "1"`). To remove it permanently, delete the branch — the line keeps composing the rest.

### Reorder or change the separator

The output is `parts.join(dim(" │ "))`. Reorder the `parts.push(...)` calls to taste, or swap `│` for `·` / `—` / a colored separator. Width is bounded by `MAX_WIDTH` (default 80) — change there if your terminal is wider.

### Re-skin the colors

Helpers `green / red / yellow / cyan / dim` wrap an ANSI SGR code. Adjust the codes (e.g. swap `"32"` for `"92"` to get bright green), or build a new helper like `magenta = (s) => c("35", s)` and apply it to a chosen segment. Color is auto-disabled under `NO_COLOR`, `OPENVIKING_STATUSLINE_NO_COLOR`, or `TERM=dumb`.

### Compose with an existing statusline

The installer detects an existing `.statusLine.command` and offers replace / skip. To run **both**, write a small wrapper that fans the same JSON payload to each and concatenates the outputs (substitute the actual `$PLUGIN` path you resolved above):

```bash
#!/usr/bin/env bash
INPUT=$(cat)
ov=$(node "$PLUGIN/scripts/statusline.mjs" <<<"$INPUT")
mine=$(your_other_statusline <<<"$INPUT")
printf '%s  %s\n' "$mine" "$ov"
```

Then point `.statusLine.command` at that wrapper. Keep the OV portion last so it gets truncated (not your line) when the terminal narrows.

### Tighten or relax the network timeout

`REQUEST_TIMEOUT_MS` in `$PLUGIN/scripts/lib/server-probe.mjs`. The default 1000 ms is generous for LAN and tight for transcontinental SaaS. The probe is cached for 5 s across processes (file-locked under `$STATE/`), so this only affects the first hit per cache window.

### Add a custom segment

Read the existing segments for the pattern: read state (or call a fast endpoint), guard with a condition, `parts.push(dim("⋯ ..."))`. Keep custom segments cheap — the whole script must finish in well under CC's statusline timeout (≈300 ms wall clock is the working budget). Anything that needs the network should go through the file-cached probe pattern in `$PLUGIN/scripts/lib/server-probe.mjs`.

### Reset stale state without restarting CC

```bash
rm -f "$STATE"/last-*.json
```

The next hook fire repopulates whichever segments are currently active. This is handy after testing or when a session ID got out of sync.

### Disable temporarily / permanently

- Off for one shell: `export OPENVIKING_STATUSLINE=off`
- Off everywhere (keep registration): set the env var in your shell rc
- Remove registration: `jq 'del(.statusLine)' ~/.claude/settings.json` (back up first)

---

## State file shapes

For an assistant adding a segment that consumes existing state. All files live under `$STATE/` and use the same atomic-write helper in `$PLUGIN/scripts/lib/state.mjs`; readers should pass `{ maxAgeMs }` to filter stale snapshots.

`last-recall.json`:
```jsonc
{
  "reason": "ok" | "bypass" | "offline" | "no_results" | "filtered_out" | "short_query" | ...,
  "count": 6,                        // memories actually injected
  "top_score": 0.92,                 // max score among picked items
  "latency_ms": 180,
  "cc_session_id": "ff875009-...",
  "ts": 1778139288759
}
```

`last-capture.json`:
```jsonc
{
  "turns_captured": 3,
  "turns_failed": 0,
  "pending_tokens": 12450,           // sawtooth — resets to 0 on commit
  "commit_threshold": 20000,
  "committed": false,                // true on the turn a commit happened
  "commit_count": 2,                 // total archives in this session
  "total_message_count": 412,
  "ov_session_id": "cc-62e5af67...",
  "cc_session_id": "ff875009-...",   // statusline filters by exact match
  "ts": 1778139288759
}
```

`last-session-event.json` (1-minute TTL in the consumer):
```jsonc
{
  "source": "resume" | "compact",
  "had_context": true,               // false when OV had no archive to inject
  "cc_session_id": "...",
  "ov_session_id": "cc-...",
  "ts": 1778139288759
}
```

`daily-stats.json` (UTC date rollover):
```jsonc
{ "date": "2026-05-07", "archives": 3 }
```

---

## When to ask the user

If a request can't be served by an env var or a small local edit (a custom backend, a domain-specific signal, deeper restyling), the cleanest path is:

1. Identify which segment block the change belongs near (or whether it warrants a new one).
2. Propose the smallest patch that delivers it.
3. Suggest a feature branch and a quick eyeball test: `node "$PLUGIN/scripts/statusline.mjs" <<<'{"session_id":"...","cwd":"/tmp"}'`.

Keep changes to `$PLUGIN/scripts/statusline.mjs` shallow; the value of this script is that it stays readable in one screen.
