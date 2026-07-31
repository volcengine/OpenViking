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

const STATE_MAX_AGE_MS = 30 * 60_000;        // 30 min — older = "idle"
const SESSION_EVENT_MAX_AGE_MS = 60_000;     // 1 min — "🔗 resumed" fades
const MAX_WIDTH = 100;
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
const cyan = (s) => c("36", s);

function human(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "?";
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  return Math.round(n / 1000) + "k";
}

function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// Context usage straight from CC's statusline payload. Registering a custom
// statusLine replaces CC's native line — and with it the built-in context
// indicator — so we have to reproduce it. Color thresholds mirror native CC:
// <70% dim, 70–89% yellow, >=90% red.
function ctxSegment(stdin) {
  if ((process.env.OPENVIKING_STATUSLINE_CTX || "").toLowerCase() === "off") return null;
  const cw = stdin.context_window;
  if (!cw || typeof cw !== "object") return null;
  let pct = num(cw.used_percentage);
  if (pct === null) {
    const remaining = num(cw.remaining_percentage);
    if (remaining !== null) pct = 100 - remaining;
  }
  if (pct === null) {
    const used = num(cw.total_input_tokens);
    const size = num(cw.context_window_size);
    if (used !== null && size !== null && size > 0) pct = (used / size) * 100;
  }
  if (pct === null) return null;
  const rounded = Math.min(100, Math.max(0, Math.round(pct)));
  const seg = `ctx ${rounded}%`;
  if (rounded >= 90) return red(seg);
  if (rounded >= 70) return yellow(seg);
  return dim(seg);
}

// Model + context cluster, one segment: "Fable 5 · ctx 42%". The model name
// rarely changes but anchors the fast-moving percentage next to it.
function modelCtxSegment(stdin) {
  const name = typeof stdin.model?.display_name === "string" ? stdin.model.display_name.trim() : "";
  const ctx = ctxSegment(stdin);
  if (name && ctx) return `${dim(name)}${dim(" · ")}${ctx}`;
  if (ctx) return ctx;
  if (name) return dim(name);
  return null;
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
  // this session out of OV. The context indicator stays — it describes the
  // CC conversation, not OV, and this line is all the user gets.
  if (isBypassed(cfg, { sessionId, cwd })) {
    const bypassParts = [yellow("OV ⚡ bypass")];
    const modelCtx = modelCtxSegment(stdin);
    if (modelCtx) bypassParts.push(modelCtx);
    process.stdout.write(truncate(bypassParts.join(dim(" │ "))));
    return;
  }

  const recall = readJsonState("last-recall.json", { maxAgeMs: STATE_MAX_AGE_MS });
  const capture = readJsonState("last-capture.json", { maxAgeMs: STATE_MAX_AGE_MS });
  const sessionEvent = readJsonState("last-session-event.json", { maxAgeMs: SESSION_EVENT_MAX_AGE_MS });
  const daily = readJsonState("daily-stats.json");
  const probe = await probeServer(cfg);

  const parts = [];

  if (probe.healthy) {
    parts.push(green("OV ✓"));
  } else if (probe.error === "timeout") {
    // "slow" means the probe missed its 1 s budget — the server might be alive
    // but lagging (remote SaaS, GC pause). Yellow keeps it advisory; reserving
    // red for actual unreachability (refused, DNS, etc.) makes the distinction
    // legible at a glance.
    parts.push(yellow("OV ⚠ slow"));
  } else {
    parts.push(red("OV ✗ offline"));
  }

  const modelCtx = modelCtxSegment(stdin);
  if (modelCtx) parts.push(modelCtx);

  // Recall summary: only meaningful when we actually injected memories this
  // turn. Skip the segment for empty/bypass/no-results reasons to keep the
  // line tight. The (0.92) trailing parens is the top score among picked
  // items — quality hint without an extra segment. Token/char count is
  // omitted: the only number we have is a chars/4 heuristic, which is
  // misleading enough on CJK text that displaying it does more harm than
  // good. Count + score + latency convey the relevant signal.
  if (recall && recall.reason === "ok" && recall.count > 0) {
    const top = typeof recall.top_score === "number" && recall.top_score > 0
      ? ` (${recall.top_score.toFixed(2)})`
      : "";
    const seg = `↩ ${recall.count} mem${top}`
      + (typeof recall.latency_ms === "number" ? ` · ${recall.latency_ms}ms` : "");
    parts.push(dim(seg));
  }

  // Capture summary: pending-tokens progress toward the next archive, plus a
  // running total of archives this session has produced. The pending counter
  // is a sawtooth (climbs to commit_threshold, snaps back to 0 on commit), so
  // showing only "X/20k tok" of a long conversation can mislead — the
  // archived count makes the cumulative work visible.
  if (capture && capture.cc_session_id === sessionId) {
    const archived = Number(capture.commit_count || 0);
    const archivedTail = archived > 0 ? ` · ${archived} arch` : "";
    if (capture.committed) {
      parts.push(dim(`✎ committed${archivedTail}`));
    } else if (capture.pending_tokens > 0) {
      // No "tok" suffix: server-side counter is approximate (chars/N), and
      // mixing it with the recall side (which is a pure heuristic) under the
      // same label invites the wrong mental model. Pending/threshold ratio
      // is meaningful on its own.
      parts.push(dim(
        `✎ ${human(capture.pending_tokens)}/${human(capture.commit_threshold)}${archivedTail}`,
      ));
    } else if (archived > 0) {
      parts.push(dim(`✎ ${archived} arch`));
    }

    // Capture failure alert. turns_failed comes from THIS batch only —
    // auto-capture overwrites the state every Stop hook, so transient
    // single-turn failures clear themselves after the next successful
    // capture. Sustained failures stay visible (which is the point).
    if (Number(capture.turns_failed) > 0) {
      parts.push(red(`✗ ${capture.turns_failed} dropped`));
    }
  }

  // Resumed/compact indicator: 1-minute TTL so it shows once after the
  // SessionStart hook re-hydrated context, then fades.
  if (sessionEvent && sessionEvent.cc_session_id === sessionId) {
    const label = sessionEvent.source === "compact" ? "compact" : "resumed";
    parts.push(cyan(`🔗 ${label}`));
  }

  // Cross-session daily archive count. Tracks "how much OV digested today"
  // without hitting the server. Resets on date rollover. Hidden when 0 to
  // keep fresh-day mornings unobtrusive.
  const todayStr = new Date().toISOString().slice(0, 10);
  if (daily && daily.date === todayStr && Number(daily.archives) > 0) {
    parts.push(dim(`+${daily.archives} today`));
  }

  const line = parts.join(dim(" │ "));
  process.stdout.write(truncate(line));
}

main().catch(() => { /* statusline must never crash CC */ });
