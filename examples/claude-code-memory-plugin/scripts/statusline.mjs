#!/usr/bin/env node

/**
 * OpenViking statusline for Claude Code.
 *
 * Wired into ~/.claude/settings.json `.statusLine` by the plugin installer.
 * CC invokes this command on each conversation update, fresh process, with
 * a JSON payload on stdin (session_id, cwd, model, transcript_path, ...).
 *
 * We compose a one-line status from two sources:
 *   - Local state files written by auto-recall / auto-capture hooks
 *     (~/.openviking/state/last-recall.json, last-capture.json)
 *   - A 5 s shared cache of GET /health (+ /observer/queue best-effort)
 *
 * Output budget: <300 ms wall clock. Caching, AbortController, and
 * fail-soft branches all serve that budget. Empty stdout is a valid
 * statusline (CC just renders nothing for OV that turn).
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { isBypassed } from "./lib/ov-session.mjs";
import { readJsonState } from "./lib/state.mjs";
import { probeServer } from "./lib/server-probe.mjs";

const STATE_MAX_AGE_MS = 30 * 60_000; // 30 min — older = "idle"
const MAX_WIDTH = 80;
const ESC = "\x1b[";

function colorEnabled() {
  if (process.env.NO_COLOR) return false;
  if (process.env.OPENVIKING_STATUSLINE_NO_COLOR) return false;
  const term = process.env.TERM || "";
  if (term === "dumb") return false;
  return true;
}

const COLOR = colorEnabled();
const c = (code, s) => (COLOR ? `${ESC}${code}m${s}${ESC}0m` : s);
const dim = (s) => c("2", s);
const green = (s) => c("32", s);
const red = (s) => c("31", s);
const yellow = (s) => c("33", s);

function human(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "?";
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  return Math.round(n / 1000) + "k";
}

async function readStdin() {
  if (process.stdin.isTTY) return {};
  return await new Promise((resolve) => {
    const chunks = [];
    let settled = false;
    const settle = (val) => {
      if (settled) return;
      settled = true;
      resolve(val);
    };
    // Hard cap: CC always writes stdin promptly. If we don't see EOF in 50 ms,
    // assume there's no payload and proceed — never block the render.
    const timer = setTimeout(() => settle({}), 50);
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => {
      clearTimeout(timer);
      try {
        settle(JSON.parse(Buffer.concat(chunks).toString() || "{}"));
      } catch {
        settle({});
      }
    });
    process.stdin.on("error", () => {
      clearTimeout(timer);
      settle({});
    });
  });
}

function truncate(line) {
  // Strip ANSI for width measurement, then re-truncate the original.
  // Simple approximation: assume colors only at specific positions; for the
  // composer below this is fine because we never embed colored text mid-word.
  // eslint-disable-next-line no-control-regex
  const visible = line.replace(/\x1b\[[0-9;]*m/g, "");
  if (visible.length <= MAX_WIDTH) return line;
  // Cut visible to budget, replace tail with ellipsis. We append the reset
  // unconditionally so a truncated mid-color string doesn't bleed.
  let out = "";
  let visibleLen = 0;
  let i = 0;
  while (i < line.length && visibleLen < MAX_WIDTH - 1) {
    if (line[i] === "\x1b") {
      const m = line.slice(i).match(/^\x1b\[[0-9;]*m/);
      if (m) { out += m[0]; i += m[0].length; continue; }
    }
    out += line[i];
    visibleLen++;
    i++;
  }
  return out + "…" + (COLOR ? `${ESC}0m` : "");
}

async function main() {
  if (process.env.OPENVIKING_STATUSLINE === "off") return;
  if (!isPluginEnabled()) return;

  const cfg = loadConfig();
  const stdin = await readStdin();
  const sessionId = stdin.session_id;
  const cwd = stdin.cwd;

  // Bypass shortcut: don't even probe the server when the user has opted
  // this session out of OV.
  if (isBypassed(cfg, { sessionId, cwd })) {
    process.stdout.write(yellow("OV ⚡ bypass"));
    return;
  }

  const recall = readJsonState("last-recall.json", { maxAgeMs: STATE_MAX_AGE_MS });
  const capture = readJsonState("last-capture.json", { maxAgeMs: STATE_MAX_AGE_MS });
  const probe = await probeServer(cfg);

  const parts = [];

  if (probe.healthy) {
    parts.push(green("OV ✓"));
  } else {
    const reason = probe.error === "timeout" ? "slow" : "offline";
    parts.push(red(`OV ✗ ${reason}`));
  }

  // Recall summary: only meaningful when we actually injected memories this
  // turn. Skip the segment for empty/bypass/no-results reasons to keep the
  // line tight.
  if (recall && recall.reason === "ok" && recall.count > 0) {
    const seg = `↩ ${recall.count} mem`
      + (recall.tokens_used ? ` · ${human(recall.tokens_used)} tok` : "")
      + (typeof recall.latency_ms === "number" ? ` · ${recall.latency_ms}ms` : "");
    parts.push(dim(seg));
  }

  // Capture summary: shown when there are pending tokens not yet committed,
  // or recently committed in this session. Otherwise omitted.
  if (capture && capture.cc_session_id === sessionId) {
    if (capture.committed) {
      parts.push(dim(`✎ committed`));
    } else if (capture.pending_tokens > 0) {
      parts.push(dim(`✎ ${human(capture.pending_tokens)}/${human(capture.commit_threshold)} tok`));
    }
  }

  // Queue alert: server is up but its queue has errors. Rare; flagged loud
  // because it usually means extraction is stalled.
  if (probe.healthy && probe.queue_healthy === false) {
    parts.push(yellow("⚠ queue"));
  }

  const line = parts.join(dim(" │ "));
  process.stdout.write(truncate(line));
}

main().catch(() => { /* statusline must never crash CC */ });
