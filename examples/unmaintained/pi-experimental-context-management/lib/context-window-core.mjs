/**
 * Pure, harness-agnostic core of the "agent manages its own context window"
 * mode (a pi port of Codex `features.context_management.experimental_mode`).
 *
 * Nothing in this file imports pi or touches the network: every side effect
 * goes through the injected `io` object, exactly like the takeover core of
 * examples/pi-coding-agent-extension. That keeps the whole reset state machine
 * unit-testable and portable to other harnesses (dsh).
 *
 * The reset itself is a *virtual* cut: the `context` hook replaces the message
 * list of a single provider request with `[window header, ...messages after the
 * anchoring tool call]`. Nothing is deleted from the session on disk.
 */

import { estimateTokens, flattenContent, truncateToTokens } from "./text-budget.mjs";

/** Custom session entry that carries the window state across processes. */
export const WINDOW_ENTRY_TYPE = "ov-context-window";
/** The window header opens with this exact string (recall's idempotency guard). */
export const WINDOW_HEADER_OPEN = '<openviking-context source="context-window">';
/** …and closes with this one, so a merged user message can be split off again. */
export const WINDOW_HEADER_CLOSE = "</openviking-context>";
/** customType of the per-prompt status line. */
export const STATUS_CUSTOM_TYPE = "ov-context-status";
/** customType of the one-shot soft/hard reminders. */
export const REMINDER_CUSTOM_TYPE = "ov-context-reminder";
/** First line of the handoff message archived as the last assistant message. */
export const HANDOFF_MARKER = "[Context Window Handoff]";
/** First token of the per-prompt status line. */
export const STATUS_MARKER = "[context-status]";
/** First token of a soft/hard reminder. */
export const REMINDER_MARKER = "[context-reminder]";
/**
 * `firstKeptEntryId` used when the anchoring tool batch is the tail of the
 * branch: it matches no entry, so pi builds a summary-only context.
 */
export const COMPACTION_SENTINEL = "ov-context-window-reset";

const HEADER_INTRO =
  "This is a fresh context window. The earlier conversation of this session was archived to " +
  "OpenViking and is no longer in context. Files, the working directory, running processes and " +
  "tools are unchanged.";

const TRUNCATION_MARKER = "\n...(truncated)";

/**
 * Handoff notes for a window pi compacted before the model called
 * `new_context`. The model wrote none for this boundary, and reusing the notes
 * of the previous one would archive them as if it had.
 */
const AUTO_COMPACT_NOTES =
  "(none — the harness compacted this window before new_context was called, " +
  "so no handoff notes were written for it)";

const FAIL_CLOSED_TAIL =
  "Nothing changed; keep working; the harness will compact for you if the window fills up.";

const DEFAULTS = {
  resetDeadlineMs: 60000,
  archivePollMs: 2000,
  overviewRefreshMaxAttempts: 20,
  overviewBudget: 3000,
  notesBudget: 1500,
  pendingRequestBudget: 400,
  softPercent: 70,
  hardPercent: 85,
  idleGapMinutes: 30,
  statusEveryTurn: true,
  historyItemMaxChars: 8000,
  recentResetGuardMs: 60000,
};

function clampInt(value, fallback, min, max) {
  const raw = Number(value);
  const base = Number.isFinite(raw) ? Math.floor(raw) : fallback;
  return Math.min(max, Math.max(min, base));
}

/**
 * Normalize + clamp the `contextWindow.*` config block. Idempotent: feeding an
 * already normalized object back in returns the same values.
 */
export function windowConfig(raw = {}) {
  const outer = raw && typeof raw === "object" ? raw : {};
  const nested = outer.contextWindow && typeof outer.contextWindow === "object" ? outer.contextWindow : null;
  const src = nested ? { ...outer, ...nested } : outer;

  const softPercent = clampInt(src.softPercent, DEFAULTS.softPercent, 10, 99);
  const hardPercent = Math.max(softPercent, clampInt(src.hardPercent, DEFAULTS.hardPercent, 10, 99));

  return {
    resetDeadlineMs: clampInt(src.resetDeadlineMs, DEFAULTS.resetDeadlineMs, 5000, 600000),
    archivePollMs: clampInt(src.archivePollMs, DEFAULTS.archivePollMs, 250, 30000),
    overviewRefreshMaxAttempts: clampInt(src.overviewRefreshMaxAttempts, DEFAULTS.overviewRefreshMaxAttempts, 0, 200),
    overviewBudget: clampInt(src.overviewBudget, DEFAULTS.overviewBudget, 100, 50000),
    notesBudget: clampInt(src.notesBudget, DEFAULTS.notesBudget, 100, 20000),
    pendingRequestBudget: clampInt(src.pendingRequestBudget, DEFAULTS.pendingRequestBudget, 0, 8000),
    softPercent,
    hardPercent,
    idleGapMinutes: clampInt(src.idleGapMinutes, DEFAULTS.idleGapMinutes, 0, 1440),
    statusEveryTurn: src.statusEveryTurn !== false,
    historyItemMaxChars: clampInt(src.historyItemMaxChars, DEFAULTS.historyItemMaxChars, 500, 100000),
    recentResetGuardMs: clampInt(src.recentResetGuardMs, DEFAULTS.recentResetGuardMs, 0, 600000),
  };
}

// --------------------------------------------------------------------------
// Cut
// --------------------------------------------------------------------------

function isToolResult(msg) {
  return !!msg && msg.role === "toolResult";
}

function toolCallBlocks(msg) {
  if (!msg || msg.role !== "assistant") return [];
  const content = msg.content;
  if (!Array.isArray(content)) return [];
  return content.filter((block) => block && typeof block === "object" && block.type === "toolCall");
}

function blockToolName(block) {
  if (!block || typeof block !== "object") return "";
  return String(block.name || block.toolName || block.tool_name || "");
}

/**
 * Locate the message range a reset must drop.
 *
 * `resultIndex` is the toolResult of the `new_context` call itself; the whole
 * contiguous run of toolResults it belongs to is dropped so that a parallel
 * batch never leaves an orphan tool_result behind (providers reject those).
 */
export function computeCutRange(messages, anchorToolCallId) {
  const list = Array.isArray(messages) ? messages : [];
  const anchor = String(anchorToolCallId || "");
  if (!anchor) return null;

  let resultIndex = -1;
  for (let i = list.length - 1; i >= 0; i--) {
    const msg = list[i];
    if (isToolResult(msg) && String(msg.toolCallId || "") === anchor) {
      resultIndex = i;
      break;
    }
  }
  if (resultIndex < 0) return null;

  let assistantIndex = -1;
  for (let i = resultIndex - 1; i >= 0; i--) {
    const blocks = toolCallBlocks(list[i]);
    if (blocks.some((block) => String(block.id || block.toolCallId || "") === anchor)) {
      assistantIndex = i;
      break;
    }
  }

  let start = resultIndex;
  if (assistantIndex >= 0 && isToolResult(list[assistantIndex + 1])) start = assistantIndex + 1;

  let cutEndIndex = start;
  while (isToolResult(list[cutEndIndex + 1])) cutEndIndex++;
  if (cutEndIndex < resultIndex) {
    // Non-contiguous batch (should not happen in pi): fall back to the anchor.
    start = resultIndex;
    cutEndIndex = resultIndex;
    while (isToolResult(list[cutEndIndex + 1])) cutEndIndex++;
  }

  const siblingToolNames = [];
  let droppedToolResults = 0;
  for (let i = start; i <= cutEndIndex; i++) {
    if (!isToolResult(list[i])) continue;
    droppedToolResults++;
    if (String(list[i].toolCallId || "") === anchor) continue;
    siblingToolNames.push(String(list[i].toolName || list[i].tool_name || ""));
  }
  if (siblingToolNames.length === 0 && assistantIndex >= 0) {
    for (const block of toolCallBlocks(list[assistantIndex])) {
      if (String(block.id || block.toolCallId || "") === anchor) continue;
      siblingToolNames.push(blockToolName(block));
    }
  }

  return {
    assistantIndex,
    resultIndex,
    cutEndIndex,
    droppedToolResults,
    siblingToolNames: siblingToolNames.filter(Boolean),
  };
}

function cloneMessage(msg) {
  try {
    return structuredClone(msg);
  } catch {
    return JSON.parse(JSON.stringify(msg));
  }
}

function mergeHeaderIntoUser(msg, headerText) {
  const copy = cloneMessage(msg);
  if (typeof copy.content === "string") {
    copy.content = `${headerText}\n\n${copy.content}`;
    return copy;
  }
  if (Array.isArray(copy.content)) {
    const idx = copy.content.findIndex((block) => block && typeof block === "object" && typeof block.text === "string");
    if (idx >= 0) {
      copy.content[idx] = { ...copy.content[idx], text: `${headerText}\n\n${copy.content[idx].text}` };
    } else {
      copy.content.unshift({ type: "text", text: headerText });
    }
    return copy;
  }
  copy.content = headerText;
  return copy;
}

/**
 * A pressure reminder is about the window that just ended, so it must never
 * survive into the new one: leaving it in front would tell a brand new window
 * that it is already 87% full and push the model straight into a second reset.
 *
 * The `context` hook runs on pi's *AgentMessage* list, before `convertToLlm`
 * rewrites custom messages to `user` (agent-loop.js: transformContext →
 * convertToLlm), so what a reminder actually looks like here is
 * `{role:"custom", customType:"ov-context-reminder"}`. Both shapes are matched:
 * the custom one is what pi emits, the user one is what a converted copy looks
 * like.
 */
function isReminderCarrier(msg) {
  return !!msg && (msg.role === "user" || msg.role === "custom");
}

function isStaleReminder(msg) {
  if (!isReminderCarrier(msg)) return false;
  if (messageCustomType(msg) === REMINDER_CUSTOM_TYPE) return true;
  return flattenContent(msg).trimStart().startsWith(REMINDER_MARKER);
}

/**
 * Apply the virtual cut. Pure: the input array and its objects are never
 * mutated (only the merged first kept message is cloned and rewritten).
 */
export function applyWindowCut(messages, opts = {}) {
  const list = Array.isArray(messages) ? messages : [];
  const headerText = String(opts.headerText || "");
  const range = computeCutRange(list, opts.anchorToolCallId);
  if (!range || !headerText) return { messages: list, applied: false, droppedCount: 0 };

  let keptStart = range.cutEndIndex + 1;
  while (keptStart < list.length && isStaleReminder(list[keptStart])) keptStart++;
  const kept = list.slice(keptStart);
  const droppedCount = keptStart;

  if (kept.length > 0 && kept[0] && kept[0].role === "user") {
    const merged = mergeHeaderIntoUser(kept[0], headerText);
    return { messages: [merged, ...kept.slice(1)], applied: true, droppedCount };
  }

  const header = { role: "user", content: headerText };
  const ts = Number(opts.headerTimestamp);
  if (Number.isFinite(ts)) header.timestamp = ts;
  return { messages: [header, ...kept], applied: true, droppedCount };
}

// --------------------------------------------------------------------------
// Text builders
// --------------------------------------------------------------------------

function normalizeSteps(value) {
  if (!Array.isArray(value)) return [];
  return value.map((step) => String(step || "").trim()).filter(Boolean);
}

function normalizeNames(value) {
  if (!Array.isArray(value)) return [];
  return value.map((name) => String(name || "").trim()).filter(Boolean);
}

/** How many `{windowId, archiveId}` pairs the persisted entry carries. */
const ARCHIVE_HISTORY_LIMIT = 100;

function normalizeArchives(value) {
  if (!Array.isArray(value)) return [];
  const out = [];
  for (const row of value) {
    if (!row || typeof row !== "object" || Array.isArray(row)) continue;
    const windowId = String(row.windowId || "").trim();
    const archiveId = String(row.archiveId || "").trim();
    if (!windowId || !archiveId) continue;
    out.push({ windowId, archiveId, archiveUri: String(row.archiveUri || "").trim() });
  }
  return out.slice(-ARCHIVE_HISTORY_LIMIT);
}

function truncateWithMarker(text, budget) {
  const raw = String(text || "").trim();
  const limit = Math.max(0, Math.floor(Number(budget) || 0));
  if (!raw || limit <= 0) return "";
  const cut = truncateToTokens(raw, limit);
  return cut === raw ? raw : `${cut}${TRUNCATION_MARKER}`;
}

/**
 * The assistant message appended to the OpenViking session right before the
 * commit, so the server-side Working Memory prompt folds reason + notes into
 * Current State / Open Issues without any server change.
 */
export function buildHandoffMessage(opts = {}) {
  const windowId = String(opts.windowId || "w?");
  const nextWindowId = String(opts.nextWindowId || "w?");
  const reason = String(opts.reason || "").trim() || "(none given)";
  const notes = String(opts.notes || "").trim() || "(none)";
  const steps = normalizeSteps(opts.nextSteps);

  const lines = [
    `${HANDOFF_MARKER} ${windowId} -> ${nextWindowId}`,
    `Reason: ${reason}`,
    "Handoff notes:",
    notes,
  ];
  if (steps.length > 0) {
    lines.push("Next steps:");
    steps.forEach((step, i) => lines.push(`${i + 1}. ${step}`));
  }
  return lines.join("\n");
}

function isoSecond(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value <= 0) return "";
  try {
    return new Date(value).toISOString().replace(/\.\d{3}Z$/, "Z");
  } catch {
    return "";
  }
}

/**
 * The frozen window header (plan §3). Generated exactly once per reset so that
 * every later provider request repeats the same bytes and the prompt cache
 * survives.
 */
export function buildWindowHeader(opts = {}) {
  const cfg = windowConfig(opts.config || {});
  const windowId = String(opts.windowId || "w?");
  const previousWindowId = String(opts.previousWindowId || "");
  const archiveId = String(opts.archiveId || "");
  const opened = isoSecond(opts.openedAt);

  const attrs = [`id="${windowId}"`];
  if (previousWindowId) attrs.push(`previous="${previousWindowId}"`);
  if (archiveId) attrs.push(`archive="${archiveId}"`);
  if (opened) attrs.push(`opened="${opened}"`);

  const lines = [WINDOW_HEADER_OPEN, `<context_window ${attrs.join(" ")}>`, HEADER_INTRO];

  const reason = String(opts.reason || "").trim();
  if (reason) {
    lines.push("", `Reason you gave: ${reason}`);
  }

  const notes = truncateWithMarker(opts.notes, cfg.notesBudget);
  if (notes) {
    lines.push("", "Your handoff notes:", `<handoff-notes>${notes}</handoff-notes>`);
  }

  const steps = normalizeSteps(opts.nextSteps);
  if (steps.length > 0) {
    lines.push("", "Next steps you planned:");
    steps.forEach((step, i) => lines.push(`${i + 1}. ${step}`));
  }

  const pending = truncateWithMarker(opts.pendingRequest, cfg.pendingRequestBudget);
  if (pending) {
    lines.push(
      "",
      "The user's most recent message in the previous window was:",
      `<pending-request>${pending}</pending-request>`,
    );
  }

  const overview = truncateWithMarker(opts.overview, cfg.overviewBudget);
  const archiveLabel = archiveId || "the archived window";
  let state = opts.overviewState === "ready" || opts.overviewState === "unavailable" ? opts.overviewState : "pending";
  if (state === "ready" && !overview) state = "pending";

  lines.push("");
  if (state === "ready") {
    lines.push(
      "Working Memory of the archived window, generated by OpenViking:",
      `<working-memory archive="${archiveId}">${overview}</working-memory>`,
    );
  } else if (state === "unavailable") {
    lines.push(
      `Working Memory for ${archiveLabel} is unavailable: OpenViking could not summarize it. ` +
        "Rely on your handoff notes above and use history to read the archived messages directly.",
    );
  } else {
    lines.push(
      `Working Memory for ${archiveLabel} is not ready yet: OpenViking is still summarizing it. ` +
        "Rely on your handoff notes above and use history to read the archived messages directly.",
    );
    const previousOverview = truncateWithMarker(opts.previousOverview, cfg.overviewBudget);
    if (previousOverview) {
      // This block is the Working Memory of an *earlier* window, never of the
      // archive named one line above — tagging it with `archiveId` would
      // present a summary as belonging to the archive that has none.
      const staleArchive = String(opts.previousArchiveId || "").trim();
      const attrs = ['status="stale"'];
      if (staleArchive) attrs.push(`archive="${staleArchive}"`);
      attrs.push('describes="an earlier window, not the archive above"');
      lines.push(`<working-memory ${attrs.join(" ")}>${previousOverview}</working-memory>`);
    }
  }

  const siblings = normalizeNames(opts.siblingToolNames);
  if (siblings.length > 0) {
    const singular = siblings.length === 1;
    lines.push(
      "",
      `${siblings.length} other tool result${singular ? " was" : "s were"} discarded with the same batch; ` +
        `re-run ${singular ? "it" : "them"} if needed. Tools: ${siblings.join(", ")}.`,
    );
  }

  const undelivered = Math.max(0, Math.floor(Number(opts.undeliveredCount) || 0));
  if (undelivered > 0) {
    const singular = undelivered === 1;
    lines.push(
      "",
      `${undelivered} message${singular ? "" : "s"} of this session ${singular ? "was" : "were"} ` +
        `rejected by OpenViking and ${singular ? "is" : "are"} missing from the archive, ` +
        `so history cannot show ${singular ? "it" : "them"}.`,
    );
  }

  const previousLabel = previousWindowId || windowId;
  lines.push(
    "",
    'To recover anything not covered above: history {"action":"list_windows"} / ' +
      `{"action":"list_items","window":"${previousLabel}"} / ` +
      // A literal index would usually be out of range; list_items is what hands
      // out the real ones.
      `{"action":"read_item","item":"${previousLabel}:<index from list_items>"} / ` +
      '{"action":"search_contents","query":"..."}.',
    "Continue from the notes above. If the pending request is not finished, resume it now.",
    "</context_window>",
    "</openviking-context>",
  );

  return lines.join("\n");
}

/** "38k", "38.4k", "262k", "512". */
export function formatTokens(n) {
  const value = Number(n);
  if (!Number.isFinite(value) || value <= 0) return "0";
  const rounded = Math.round(value);
  if (rounded < 1000) return String(rounded);
  const k = rounded / 1000;
  if (k < 100) {
    const text = k.toFixed(1);
    return `${text.endsWith(".0") ? text.slice(0, -2) : text}k`;
  }
  return `${Math.round(k)}k`;
}

/** "12s", "3m", "47m", "1h 12m", "2d 3h". */
export function formatDuration(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value <= 0) return "0s";
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) return restMinutes > 0 ? `${hours}h ${restMinutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours > 0 ? `${days}d ${restHours}h` : `${days}d`;
}

/**
 * pi auto-compacts as soon as `contextTokens > contextWindow - reserveTokens`
 * (compaction.js `shouldCompact`, reserveTokens default 16384). On a small model
 * that line sits at 50% of a 32k window, far below our 85% default, so the hard
 * reminder would never fire before pi compacted and the agent's notes would
 * never be written. Clamp both thresholds one point under it.
 */
export function effectiveThresholds({ softPercent, hardPercent, contextWindow, reserveTokens } = {}) {
  let soft = Math.max(1, Math.floor(Number(softPercent) || 0));
  let hard = Math.max(soft, Math.floor(Number(hardPercent) || 0));

  const total = Math.floor(Number(contextWindow) || 0);
  const reserve = Math.floor(Number(reserveTokens) || 0);
  if (total > 0 && reserve > 0 && reserve < total) {
    const line = Math.max(1, Math.floor(((total - reserve) / total) * 100) - 1);
    hard = Math.min(hard, line);
    soft = Math.min(soft, hard);
  }
  return { softPercent: soft, hardPercent: hard };
}

function percentOf(used, total) {
  const u = Number(used) || 0;
  const t = Number(total) || 0;
  if (t <= 0 || u <= 0) return 0;
  return Math.min(100, Math.round((u / t) * 100));
}

/**
 * One line appended after every user prompt (persistent custom message, so the
 * bytes stay stable and the prompt cache is not broken).
 */
export function buildStatusLine(opts = {}) {
  const windowId = String(opts.windowId || "w1");
  const turns = Math.max(0, Math.floor(Number(opts.turnsInWindow) || 0));
  const used = Math.max(0, Math.floor(Number(opts.usedTokens) || 0));
  const contextWindow = Math.max(0, Math.floor(Number(opts.contextWindow) || 0));
  const pct = percentOf(used, contextWindow);

  const parts = [
    `${STATUS_MARKER} window ${windowId}`,
    `${turns} turn${turns === 1 ? "" : "s"}`,
    `~${formatTokens(used)}/${formatTokens(contextWindow)} tokens (${pct}%${opts.estimated ? ", estimated" : ""})`,
  ];

  const since = Number(opts.sinceLastUserMs);
  if (Number.isFinite(since) && since > 0) {
    parts.push(`${formatDuration(since)} since your previous message`);
  }

  const lines = [parts.join(" · ")];

  // The NOTE is about the gap the user just came back from, which is
  // `sinceLastUserMs` — the status line is built in `before_agent_start`, where
  // the newest genuine user message is still the *previous* prompt.
  // `idleGapMs` (the gap before that message) is only a fallback for a caller
  // that has nothing else; it is what `get_context_remaining` reports on its
  // own line.
  const gapMs = Number.isFinite(since) && since > 0 ? since : Number(opts.idleGapMs);
  const idleGapMinutes = Math.max(0, Math.floor(Number(opts.idleGapMinutes) || 0));
  if (idleGapMinutes > 0 && Number.isFinite(gapMs) && gapMs > idleGapMinutes * 60000) {
    const minutes = Math.round(gapMs / 60000);
    lines.push(
      `NOTE: ${minutes} minutes passed since the previous user message. ` +
        "If this request starts unrelated work, consider new_context before you begin.",
    );
  }

  return lines.join("\n");
}

/** One-shot soft / hard pressure reminders (plan §5). */
export function reminderText(level, opts = {}) {
  const windowId = String(opts.windowId || "w1");
  const used = Math.max(0, Math.floor(Number(opts.usedTokens) || 0));
  const contextWindow = Math.max(0, Math.floor(Number(opts.contextWindow) || 0));
  const usage = `about ${percentOf(used, contextWindow)}% full (~${formatTokens(used)}/${formatTokens(contextWindow)} tokens)`;

  if (level === "hard") {
    return (
      `${REMINDER_MARKER} Context window ${windowId} is ${usage}. ` +
      "Save your handoff notes and make exactly one call to new_context now. " +
      "If the window overflows first, the harness compacts it for you and your notes are never written."
    );
  }
  return (
    `${REMINDER_MARKER} Context window ${windowId} is ${usage}. ` +
    "Finish or checkpoint the step you are on, then call new_context with complete notes: " +
    "goal, decisions and why, what is done, what is in flight, exact paths and identifiers, next steps. " +
    "If the window overflows first, the harness compacts it for you and your notes are never written."
  );
}

function messageCustomType(msg) {
  if (!msg || typeof msg !== "object") return "";
  return String(msg.customType || msg.custom_type || "");
}

/**
 * Strip a leading frozen window header. `applyWindowCut` merges the header into
 * the first kept user message, so "starts with the header" alone does not make a
 * message ours — only a message that is *nothing but* the header is.
 */
function withoutWindowHeader(text) {
  const raw = String(text || "").trimStart();
  if (!raw.startsWith(WINDOW_HEADER_OPEN)) return raw;
  const end = raw.indexOf(WINDOW_HEADER_CLOSE);
  if (end < 0) return "";
  return raw.slice(end + WINDOW_HEADER_CLOSE.length).trimStart();
}

function isSyntheticUserMessage(msg) {
  const customType = messageCustomType(msg);
  if (customType === STATUS_CUSTOM_TYPE || customType === REMINDER_CUSTOM_TYPE) return true;
  const raw = flattenContent(msg).trimStart();
  const hadHeader = raw.startsWith(WINDOW_HEADER_OPEN);
  const text = hadHeader ? withoutWindowHeader(raw) : raw;
  if (hadHeader && !text) return true;
  return text.startsWith(STATUS_MARKER) || text.startsWith(REMINDER_MARKER) || text.startsWith(HANDOFF_MARKER);
}

/**
 * Timestamps of the two newest messages the human actually typed, newest
 * first. Everything this extension injects is skipped.
 */
export function lastUserTimestamps(messages) {
  const list = Array.isArray(messages) ? messages : [];
  const found = [];
  for (let i = list.length - 1; i >= 0 && found.length < 2; i--) {
    const msg = list[i];
    if (!msg || msg.role !== "user") continue;
    if (isSyntheticUserMessage(msg)) continue;
    const ts = Number(msg.timestamp);
    if (!Number.isFinite(ts)) continue;
    found.push(ts);
  }
  return found;
}

/** Last text the human typed, taken from a pi branch (entries or messages). */
export function lastUserTextFromBranch(branch) {
  const list = Array.isArray(branch) ? branch : [];
  for (let i = list.length - 1; i >= 0; i--) {
    const msg = entryMessage(list[i]);
    if (!msg || msg.role !== "user") continue;
    if (isSyntheticUserMessage(msg)) continue;
    const text = withoutWindowHeader(flattenContent(msg)).trim();
    if (text) return text;
  }
  return "";
}

function entryMessage(entry) {
  if (!entry || typeof entry !== "object") return null;
  if (entry.message && typeof entry.message === "object") return entry.message;
  if (typeof entry.role === "string") return entry;
  return null;
}

function entryId(entry) {
  if (!entry || typeof entry !== "object") return null;
  if (entry.id !== undefined && entry.id !== null) return entry.id;
  if (entry.entryId !== undefined && entry.entryId !== null) return entry.entryId;
  return null;
}

/** pi stamps session entries with an ISO string; its messages carry epoch ms. */
function entryTimestamp(entry) {
  const raw = entry?.timestamp;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : 0;
  const parsed = Date.parse(String(raw ?? ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function appendBranchMessage(out, entry) {
  if (!entry || typeof entry !== "object") return;
  if (entry.type === "message") {
    if (entry.message && typeof entry.message === "object") out.push(entry.message);
    return;
  }
  if (entry.type === "custom_message") {
    out.push({
      role: "custom",
      customType: entry.customType,
      content: entry.content,
      display: entry.display,
      details: entry.details,
      timestamp: entryTimestamp(entry),
    });
    return;
  }
  if (entry.type === "branch_summary" && entry.summary) {
    out.push({
      role: "branchSummary",
      summary: entry.summary,
      fromId: entry.fromId,
      timestamp: entryTimestamp(entry),
    });
  }
}

/**
 * The provider-visible message list of a pi branch — the same list
 * `SessionManager.buildSessionContext()` would build from those entries:
 * `message` entries contribute their message, `custom_message` entries a
 * `{role:"custom"}` message, and a compaction replaces everything before its
 * `firstKeptEntryId` with the summary. Every other entry type (this
 * extension's own window state, model or thinking-level changes, labels)
 * carries no message.
 *
 * `before_agent_start` runs before the `context` hook ever fires in a process,
 * so this is the only message list a status line built there can work from —
 * without it the first prompt after `pi -c` reports a window with no turns and
 * no idle gap.
 */
export function messagesFromBranch(entries) {
  const list = Array.isArray(entries) ? entries : [];
  let compactionIndex = -1;
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i]?.type === "compaction") {
      compactionIndex = i;
      break;
    }
  }

  const out = [];
  if (compactionIndex < 0) {
    for (const entry of list) appendBranchMessage(out, entry);
    return out;
  }

  const compaction = list[compactionIndex];
  out.push({
    role: "compactionSummary",
    summary: compaction.summary,
    tokensBefore: compaction.tokensBefore,
    timestamp: entryTimestamp(compaction),
  });
  let kept = false;
  for (let i = 0; i < compactionIndex; i++) {
    if (entryId(list[i]) === compaction.firstKeptEntryId) kept = true;
    if (kept) appendBranchMessage(out, list[i]);
  }
  for (let i = compactionIndex + 1; i < list.length; i++) appendBranchMessage(out, list[i]);
  return out;
}

/**
 * Sibling tool names for the header: the other tool calls issued in the same
 * assistant message as the anchoring `new_context` call. At reset time the
 * toolResults do not exist yet, so this reads the assistant message instead.
 */
export function siblingToolNamesFromBranch(branch, anchorToolCallId) {
  const anchor = String(anchorToolCallId || "");
  if (!anchor) return [];
  const list = Array.isArray(branch) ? branch : [];
  for (let i = list.length - 1; i >= 0; i--) {
    const msg = entryMessage(list[i]);
    const blocks = toolCallBlocks(msg);
    if (blocks.length === 0) continue;
    if (!blocks.some((block) => String(block.id || block.toolCallId || "") === anchor)) continue;
    return blocks
      .filter((block) => String(block.id || block.toolCallId || "") !== anchor)
      .map(blockToolName)
      .filter(Boolean);
  }
  return [];
}

function safeJson(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value) || "";
  } catch {
    return "";
  }
}

/**
 * Token estimate for one pi message.
 *
 * `flattenContent` only sees `text`, so a bare `estimateTokens(flattenContent(m))`
 * scores an assistant message that writes a 40k-character file through a toolCall
 * block as ~2 tokens. The window estimate is the *only* signal when pi reports
 * `usage.tokens === null` (right after a native compaction), so tool-call
 * arguments and thinking blocks have to be counted too.
 */
export function messageTokens(msg) {
  if (!msg || typeof msg !== "object") return 0;
  let total = estimateTokens(flattenContent(msg));
  const content = msg.content;
  if (!Array.isArray(content)) return total;
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    if (block.type === "toolCall") {
      total += estimateTokens(blockToolName(block));
      total += estimateTokens(safeJson(block.arguments ?? block.input ?? block.parameters));
    } else if (typeof block.thinking === "string") {
      total += estimateTokens(block.thinking);
    }
  }
  return total;
}

function basename(uri) {
  const value = String(uri || "");
  if (!value) return "";
  const parts = value.split("/").filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : "";
}

// --------------------------------------------------------------------------
// Core
// --------------------------------------------------------------------------

export class ContextWindowCore {
  constructor({ config = {}, io = {} } = {}) {
    this.config = windowConfig(config);
    this.io = {
      syncBranch: io.syncBranch || (async () => ({ added: 0, tokens: 0, allDelivered: true })),
      flush: io.flush || (async () => true),
      postHandoff: io.postHandoff || (async () => false),
      commit: io.commit || (async () => null),
      readArchiveOverview: io.readArchiveOverview || (async () => null),
      getTask: io.getTask || (async () => null),
      persistEntry: io.persistEntry || (() => {}),
      getWatermark: io.getWatermark || (() => 0),
      now: io.now || (() => Date.now()),
      sleep: io.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms))),
      log: io.log || (() => {}),
      connected: io.connected || (() => true),
      sessionId: io.sessionId || (() => null),
      pendingCount: io.pendingCount || (() => 0),
      /** Messages the server rejected for good; they are missing from the archive. */
      droppedCount: io.droppedCount || (() => 0),
      /** trace_id of the last commit response, for a refusal the model can report. */
      commitTraceId: io.commitTraceId || (() => ""),
    };

    // Persisted state (plan §10).
    this.version = 1;
    this.ovSessionId = null;
    this.windowIndex = 1;
    this.anchorToolCallId = null;
    this.openedAt = 0;
    this.headerText = "";
    this.reason = "";
    this.notes = "";
    this.nextSteps = [];
    this.pendingRequest = "";
    this.archiveId = "";
    this.archiveUri = "";
    this.taskId = "";
    /**
     * Every archive this session produced, oldest first:
     * `{windowId, archiveId, archiveUri}`. `history` resolves window ids
     * through this map instead of numbering the server's archive list
     * positionally — a window that closed without producing an archive (an
     * external compaction) would otherwise shift every id by one.
     */
    this.archives = [];
    /** Archive of the window before the current one, for the stale WM block. */
    this.previousArchiveId = "";
    /** Messages OpenViking rejected for good; they are missing from the archive. */
    this.undeliveredCount = 0;
    this.overviewReady = false;
    this.overviewUnavailable = false;
    this.overviewAttempts = 0;
    // Persisted (truncated) so a window that opened with a stale Working Memory
    // block keeps it across `pi -c` instead of rendering a header without it.
    this.previousOverview = "";
    this.siblingToolNames = [];
    this.syncedEntryCount = 0;
    this.lastResetAt = 0;
    this.lastResetBy = "";

    // In-memory only.
    this.resetting = false;
    this.remindersSent = { soft: false, hard: false };
    this.awaitingFirstObservation = false;
    this.lastWindowTokens = 0;
    this.turnsInWindow = 0;
    this.overviewText = "";
    this.disarmLogged = false;
    this.lastPersisted = "";
    /**
     * Set when `restore()` adopted a window while the OpenViking session id was
     * still unknown; `validateSession()` settles it once the id exists.
     */
    this.pendingSessionCheck = false;
    /**
     * `{at, message}` of the last `io.commit` that threw. A throw after the
     * request left the process is indistinguishable from a request that never
     * arrived, so the archive may or may not exist.
     */
    this.lastCommitError = null;
  }

  get windowId() {
    return `w${this.windowIndex}`;
  }

  get armed() {
    return !!this.anchorToolCallId;
  }

  get state() {
    return {
      ...this.persistedState(),
      windowId: this.windowId,
      armed: this.armed,
      resetting: this.resetting,
      remindersSent: { ...this.remindersSent },
      awaitingFirstObservation: this.awaitingFirstObservation,
      lastWindowTokens: this.lastWindowTokens,
      turnsInWindow: this.turnsInWindow,
      lastCommitError: this.lastCommitError ? { ...this.lastCommitError } : null,
    };
  }

  persistedState() {
    return {
      version: 1,
      ovSessionId: this.ovSessionId,
      windowIndex: this.windowIndex,
      anchorToolCallId: this.anchorToolCallId,
      openedAt: this.openedAt,
      headerText: this.headerText,
      reason: this.reason,
      notes: this.notes,
      nextSteps: [...this.nextSteps],
      pendingRequest: this.pendingRequest,
      archiveId: this.archiveId,
      archiveUri: this.archiveUri,
      taskId: this.taskId,
      // Bounded: only the tail matters and the entry has to stay small.
      archives: this.archives.slice(-ARCHIVE_HISTORY_LIMIT).map((a) => ({ ...a })),
      previousArchiveId: this.previousArchiveId,
      undeliveredCount: this.undeliveredCount,
      overviewReady: this.overviewReady,
      overviewUnavailable: this.overviewUnavailable,
      overviewAttempts: this.overviewAttempts,
      // Truncated without the "(truncated)" marker: the entry only has to be
      // small, the frozen `headerText` already carries the rendered block.
      previousOverview: truncateToTokens(this.previousOverview || "", this.config.overviewBudget),
      siblingToolNames: [...this.siblingToolNames],
      syncedEntryCount: this.syncedEntryCount,
      lastResetAt: this.lastResetAt,
      lastResetBy: this.lastResetBy,
    };
  }

  // ---- persistence ------------------------------------------------------

  restore(entries) {
    const list = Array.isArray(entries) ? entries : [];
    const sid = this.safeSessionId();
    for (let i = list.length - 1; i >= 0; i--) {
      const entry = list[i];
      const isWindowEntry =
        (entry?.type === "custom" && entry.customType === WINDOW_ENTRY_TYPE) ||
        entry?.customType === WINDOW_ENTRY_TYPE ||
        entry?.type === WINDOW_ENTRY_TYPE;
      const data = isWindowEntry ? entry.data : null;
      if (!data || typeof data !== "object" || Array.isArray(data)) continue;
      if (sid && data.ovSessionId && String(data.ovSessionId) !== sid) continue;

      this.ovSessionId = typeof data.ovSessionId === "string" ? data.ovSessionId : null;
      this.windowIndex = Math.max(1, Math.floor(Number(data.windowIndex) || 1));
      this.anchorToolCallId = data.anchorToolCallId ? String(data.anchorToolCallId) : null;
      this.openedAt = Math.max(0, Math.floor(Number(data.openedAt) || 0));
      this.headerText = typeof data.headerText === "string" ? data.headerText : "";
      this.reason = typeof data.reason === "string" ? data.reason : "";
      this.notes = typeof data.notes === "string" ? data.notes : "";
      this.nextSteps = normalizeSteps(data.nextSteps);
      this.pendingRequest = typeof data.pendingRequest === "string" ? data.pendingRequest : "";
      this.archiveId = typeof data.archiveId === "string" ? data.archiveId : "";
      this.archiveUri = typeof data.archiveUri === "string" ? data.archiveUri : "";
      this.taskId = typeof data.taskId === "string" ? data.taskId : "";
      this.archives = normalizeArchives(data.archives);
      this.previousArchiveId = typeof data.previousArchiveId === "string" ? data.previousArchiveId : "";
      this.undeliveredCount = Math.max(0, Math.floor(Number(data.undeliveredCount) || 0));
      this.overviewReady = data.overviewReady === true;
      this.overviewUnavailable = data.overviewUnavailable === true;
      this.overviewAttempts = Math.max(0, Math.floor(Number(data.overviewAttempts) || 0));
      this.previousOverview = typeof data.previousOverview === "string" ? data.previousOverview : "";
      this.siblingToolNames = normalizeNames(data.siblingToolNames);
      this.syncedEntryCount = Math.max(0, Math.floor(Number(data.syncedEntryCount) || 0));
      this.lastResetAt = Math.max(0, Math.floor(Number(data.lastResetAt) || 0));
      this.lastResetBy = typeof data.lastResetBy === "string" ? data.lastResetBy : "";
      this.lastPersisted = JSON.stringify(this.persistedState());
      // The adapter often restores before the OpenViking session id is known
      // (the sync manager derives it from pi's session id, which arrives with
      // the first event). Remember that the ownership check is still owed.
      this.pendingSessionCheck = !sid && !!this.ovSessionId;
      this.log(
        `context-window: restored ${this.windowId}` +
          (this.armed ? ` anchored at ${this.anchorToolCallId}` : " (not armed)"),
      );
      return this.state;
    }
    return this.state;
  }

  /**
   * Settle a restore that happened before `io.sessionId()` was known: if the
   * window belonged to a different OpenViking session, drop it. Safe to call on
   * every context transform — it does nothing once the check is settled.
   */
  validateSession() {
    if (!this.pendingSessionCheck) return true;
    const sid = this.safeSessionId();
    if (!sid) return true;
    this.pendingSessionCheck = false;
    if (!this.ovSessionId || this.ovSessionId === sid) {
      this.ovSessionId = sid;
      return true;
    }
    this.disarm(`restored window belongs to ${this.ovSessionId}, this session is ${sid}; window dropped`);
    this.windowIndex = 1;
    this.headerText = "";
    this.archiveId = "";
    this.archiveUri = "";
    this.taskId = "";
    this.previousOverview = "";
    this.overviewReady = false;
    this.overviewUnavailable = false;
    this.ovSessionId = sid;
    this.lastPersisted = "";
    return false;
  }

  persist() {
    try {
      const state = this.persistedState();
      const key = JSON.stringify(state);
      if (key === this.lastPersisted) return;
      this.io.persistEntry(WINDOW_ENTRY_TYPE, state);
      this.lastPersisted = key;
    } catch {
      // Best effort: a missed entry only means the next process sees full history.
    }
  }

  async shutdown() {
    this.syncedEntryCount = this.safeWatermark();
    this.persist();
  }

  // ---- context transform ------------------------------------------------

  transformContext(messages) {
    return this.cutAndRecord(messages, true);
  }

  /**
   * The same cut and the same metrics, but without the right to release the
   * boundary — for a caller that only wants to describe the window (the status
   * line, built in `before_agent_start` from the session branch).
   *
   * Only the hook that actually feeds the provider may disarm: a list that is
   * missing the anchor because pi has not finished writing it would otherwise
   * release the boundary from a status readout, and the next request would hand
   * the model back a window it was told is archived.
   */
  observeMessages(messages) {
    return this.cutAndRecord(messages, false);
  }

  cutAndRecord(messages, mayDisarm) {
    const list = Array.isArray(messages) ? messages : [];
    // Opportunistic: by the time a context is built the session id always
    // exists, so a window restored from another session is dropped here at the
    // latest, even if the adapter never called `validateSession()` itself.
    this.validateSession();
    if (!this.armed || !this.headerText) {
      this.recordWindowMetrics(list, -1);
      return list;
    }

    const cut = applyWindowCut(list, {
      anchorToolCallId: this.anchorToolCallId,
      headerText: this.headerText,
      headerTimestamp: this.openedAt,
    });
    if (!cut.applied) {
      if (mayDisarm) this.disarm("anchor missing, boundary released");
      this.recordWindowMetrics(list, -1);
      return list;
    }

    this.recordWindowMetrics(cut.messages, 0);
    return cut.messages;
  }

  /**
   * Refresh `lastWindowTokens` / `turnsInWindow` from the message list that is
   * actually going to the provider.
   *
   * With a header at `headerIndex` the turn count is simply "assistants after
   * it". Without one (`headerIndex < 0`, i.e. the anchor was lost) the list is
   * the *whole* session again, so counting every assistant message would tell
   * the model that a window opened a minute ago is dozens of turns old. Fall
   * back to the timestamp of the last reset instead.
   */
  recordWindowMetrics(messages, headerIndex) {
    const list = Array.isArray(messages) ? messages : [];
    const since = headerIndex < 0 && this.lastResetAt > 0 ? this.lastResetAt : 0;
    let tokens = 0;
    let assistants = 0;
    for (let i = 0; i < list.length; i++) {
      const msg = list[i];
      tokens += messageTokens(msg);
      if (i <= headerIndex) continue;
      if (msg?.role !== "assistant") continue;
      if (since > 0) {
        const ts = Number(msg.timestamp);
        // An undated message is counted: pi always stamps them, so this only
        // happens for synthetic ones, and over-counting is the safe direction.
        if (Number.isFinite(ts) && ts < since) continue;
      }
      assistants++;
    }
    this.lastWindowTokens = tokens;
    this.turnsInWindow = assistants;
    if (assistants > 0) this.awaitingFirstObservation = false;
  }

  observeAssistantResponse() {
    this.awaitingFirstObservation = false;
  }

  /**
   * Release the virtual boundary. The window metrics described the cut window,
   * so they are reset too: keeping them would let the status line report "12
   * turns" for a window that no longer exists.
   */
  disarm(reason) {
    if (!this.disarmLogged) {
      this.log(`context-window: ${reason}`);
      this.disarmLogged = true;
    }
    this.anchorToolCallId = null;
    this.awaitingFirstObservation = false;
    this.turnsInWindow = 0;
    this.lastWindowTokens = 0;
  }

  // ---- reset ------------------------------------------------------------

  /**
   * The blocking reset pipeline (plan §2). Never throws: every failure comes
   * back as `{ok:false}` with text the model reads (fail-closed — a refusal
   * means nothing was cut, so no history is lost).
   */
  async requestReset(opts = {}) {
    const reason = String(opts.reason || "").trim();
    const notes = String(opts.notes || "").trim();
    const nextSteps = normalizeSteps(opts.nextSteps);
    const toolCallId = String(opts.toolCallId || "");
    const branch = Array.isArray(opts.branch) ? opts.branch : [];
    const signal = opts.signal || null;
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : () => {};

    if (this.resetting) {
      return {
        ok: false,
        kind: "noop",
        text:
          "Context window NOT reset: a reset is already in progress. " +
          "Nothing changed; keep working and do not call new_context again.",
        details: { stage: "busy" },
      };
    }

    const startedAt = this.io.now();
    const deadline = startedAt + this.config.resetDeadlineMs;
    /** Set to `{archiveUri, archiveId, taskId}` once the archive exists. */
    let committed = null;
    /** True once the handoff note is in the live session and may need retracting. */
    let handoffPosted = false;
    this.resetting = true;
    try {
      if (!toolCallId) {
        return this.refuse("this call has no tool call id to anchor the new window to", { stage: "anchor" });
      }
      if (signal?.aborted) return this.cancelled("aborted-early");
      const connected = this.io.connected();
      if (this.lastCommitError) {
        // A previous commit threw *after* the request went out, so we never
        // learned whether the server archived the session. Re-checking
        // connectivity is all we can do; if it is back we try again, which may
        // produce an empty second archive (commit answers `skipped`) — the
        // failure mode we accept, because the alternative is a window that can
        // never be reset again.
        if (!connected) {
          return this.refuse(
            "the previous archive commit failed in transport and OpenViking is still unreachable",
            { stage: "commit-transport", error: this.lastCommitError.message },
          );
        }
        this.log("context-window: retrying after an unresolved commit transport error");
        this.lastCommitError = null;
      }
      if (!connected || !this.safeSessionId()) {
        return this.refuse("OpenViking is unreachable, so this window cannot be archived", {
          stage: "connectivity",
        });
      }

      const synced = await this.io.syncBranch(branch);
      if (synced && synced.allDelivered === false) {
        return this.refuse(
          "the conversation so far could not be fully delivered to OpenViking",
          { stage: "sync", added: Number(synced.added) || 0 },
        );
      }

      if (signal?.aborted) return this.cancelled("aborted-before-flush");

      const flushed = await this.io.flush({ budgetMs: this.flushBudget(deadline) });
      if (!flushed) {
        const pending = Math.max(0, Math.floor(Number(this.io.pendingCount()) || 0));
        const detail = pending > 0 ? `${pending} captured message(s) are still queued` : "captured messages are still queued";
        return this.refuse(`the archive barrier did not clear — ${detail}`, { stage: "flush", pending });
      }

      // Both the abort and the deadline are checked *before* the handoff note
      // is written, because a refusal after it leaves a
      // "[Context Window Handoff]" message in the live OpenViking session for a
      // window that never closed. The paths that can still refuse after the
      // write retract it (`refuseAfterHandoff`), which is a repair, not a
      // rollback: OpenViking has no delete for a session message.
      if (signal?.aborted) return this.cancelled("aborted-before-handoff");
      if (this.io.now() >= deadline) {
        return this.refuse(
          "the reset deadline was exhausted while syncing this window to OpenViking",
          { stage: "deadline", deadlineMs: this.config.resetDeadlineMs },
        );
      }

      const nextWindowId = `w${this.windowIndex + 1}`;
      const handoff = buildHandoffMessage({
        windowId: this.windowId,
        nextWindowId,
        reason,
        notes,
        nextSteps,
      });
      const posted = await this.io.postHandoff(handoff);
      if (!posted) {
        return this.refuse("the handoff note could not be written to OpenViking", { stage: "handoff" });
      }
      // From here on the live OpenViking session already carries a
      // "[Context Window Handoff]" message for a window that has not closed
      // yet, so every refusal below has to retract it first — otherwise the
      // next archive would contain two handoffs and the Working Memory prompt
      // would read a window boundary that never happened.
      handoffPosted = true;

      if (signal?.aborted) return await this.cancelledAfterHandoff("aborted-before-commit");

      let commit;
      try {
        commit = await this.io.commit({ keepRecentCount: 0 });
      } catch (err) {
        // A throw here is ambiguous by construction: the request may have been
        // rejected before it left the process, or it may have archived the
        // session and lost the response. We fail closed (nothing is cut) and
        // remember it, so the next attempt at least re-checks connectivity
        // before it syncs and flushes again.
        this.lastCommitError = { at: this.safeNow(), message: String(err?.message || err) };
        this.log(`context-window: commit transport error; the archive may or may not exist (${this.lastCommitError.message})`);
        return await this.refuseAfterHandoff(
          "the archive commit failed in transport; the archive may or may not exist, so nothing was cut",
          { stage: "commit-transport", error: this.lastCommitError.message },
        );
      }
      if (!commit || typeof commit !== "object") {
        return await this.refuseAfterHandoff("OpenViking refused the archive commit", {
          stage: "commit",
          traceId: this.safeCommitTraceId(),
        });
      }
      const status = String(commit.status || "");
      // No `archive_uri` means there is no archive to point the model at, and
      // waiting for the Working Memory of an empty URI would burn the whole
      // deadline — so it refuses even when the status says `accepted`.
      if (status === "skipped" || !commit.archive_uri) {
        if (status === "skipped") {
          await this.retractHandoff();
          return {
            ok: false,
            kind: "noop",
            text:
              "Context window NOT reset: OpenViking had nothing to archive for this window. " +
              "Keep working; call new_context again once there is something worth archiving.",
            details: { stage: "commit", status, reason: commit.reason || "" },
          };
        }
        return await this.refuseAfterHandoff("OpenViking refused the archive commit", {
          stage: "commit",
          status,
          traceId: commit.trace_id || this.safeCommitTraceId(),
        });
      }

      // ---- past the point of no return: the archive exists -------------
      // Everything below MUST end in an open window. The commit emptied the
      // OpenViking session (keep_recent_count 0), so answering "NOT reset —
      // nothing changed" here would leave the model carrying the old context
      // while believing its history is still live.
      const archiveUri = String(commit.archive_uri || "");
      const archiveId = basename(archiveUri);
      const taskId = String(commit.task_id || "");
      // `baseWindowIndex` lets the recovery path below re-derive the window ids
      // even if the throw happened halfway through the assignments.
      committed = {
        archiveUri,
        archiveId,
        taskId,
        baseWindowIndex: this.windowIndex,
        previousOverview: this.overviewText,
      };

      let wait;
      try {
        wait = await this.waitForOverview({ archiveUri, archiveId, taskId, deadline, signal, onProgress });
      } catch {
        // An abort-aware io.sleep may reject; the window still has to open.
        wait = { state: "pending", overview: "", attempts: 0 };
      }

      const previousWindowId = this.windowId;
      const previousOverview = this.overviewText;

      this.windowIndex += 1;
      this.anchorToolCallId = toolCallId;
      this.openedAt = this.io.now();
      this.reason = reason;
      this.notes = notes;
      this.nextSteps = nextSteps;
      this.pendingRequest = lastUserTextFromBranch(branch);
      this.previousArchiveId = this.archiveId;
      this.recordArchive(previousWindowId, archiveId, archiveUri);
      this.undeliveredCount = this.safeDroppedCount();
      this.archiveId = archiveId;
      this.archiveUri = archiveUri;
      this.taskId = taskId;
      this.overviewReady = wait.state === "ready";
      this.overviewUnavailable = wait.state === "unavailable";
      this.overviewAttempts = 0;
      this.siblingToolNames = normalizeNames(
        opts.siblingToolNames || siblingToolNamesFromBranch(branch, toolCallId),
      );
      this.previousOverview = previousOverview;
      this.overviewText = wait.overview;
      this.headerText = this.rebuildHeader({ overviewState: wait.state, previousWindowId });
      this.previousWindowId = previousWindowId;
      this.remindersSent = { soft: false, hard: false };
      this.awaitingFirstObservation = true;
      this.disarmLogged = false;
      this.lastResetAt = this.io.now();
      this.lastResetBy = "agent";
      this.ovSessionId = this.safeSessionId();
      this.syncedEntryCount = this.safeWatermark();
      this.persist();
      this.log(
        `context-window: opened ${this.windowId} from ${archiveId || "(no archive)"} ` +
          `(working memory ${wait.state}, waited ${this.io.now() - startedAt}ms)`,
      );

      const memory =
        wait.state === "ready"
          ? "ready and included in the new window"
          : wait.state === "unavailable"
            ? "unavailable; the window header points at history instead"
            : "still being generated; the window header points at history instead";

      return {
        ok: true,
        kind: "reset",
        text:
          `Context window ${this.windowId} is open. ${previousWindowId} was archived to OpenViking as ` +
          `${archiveId || "an archive"}. Working Memory is ${memory}. ` +
          "This tool result is not part of the new window: the next sampling starts from the window header, " +
          "which carries your reason, your handoff notes and whatever Working Memory exists. Continue from there.",
        details: {
          windowId: this.windowId,
          previousWindowId,
          archiveId,
          archiveUri,
          taskId,
          overviewState: wait.state,
          attempts: wait.attempts,
          waitedMs: this.io.now() - startedAt,
        },
      };
    } catch (err) {
      if (committed) {
        const recovered = this.recoverAfterCommit(committed, err, {
          toolCallId,
          reason,
          notes,
          nextSteps,
          branch,
          siblingToolNames: opts.siblingToolNames,
          startedAt,
        });
        if (recovered) return recovered;
      }
      if (handoffPosted && !committed) await this.retractHandoff();
      return this.refuse(`the reset failed unexpectedly (${err?.message || err})`, { stage: "error" });
    } finally {
      this.resetting = false;
    }
  }

  /**
   * Last-resort open: the commit landed but something after it threw. Open the
   * window anyway with an `unavailable` Working Memory so the model at least
   * gets its own notes and the pointer to `history`.
   */
  recoverAfterCommit(committed, err, ctx) {
    try {
      const base = Math.max(1, Math.floor(Number(committed.baseWindowIndex) || 1));
      const previousWindowId = `w${base}`;
      const previousOverview = committed.previousOverview || "";
      this.windowIndex = base + 1;
      this.anchorToolCallId = ctx.toolCallId;
      this.openedAt = this.safeNow();
      this.reason = ctx.reason;
      this.notes = ctx.notes;
      this.nextSteps = ctx.nextSteps;
      try {
        this.pendingRequest = lastUserTextFromBranch(ctx.branch);
      } catch {
        this.pendingRequest = "";
      }
      this.previousArchiveId = this.archiveId;
      this.recordArchive(previousWindowId, committed.archiveId, committed.archiveUri);
      this.undeliveredCount = this.safeDroppedCount();
      this.archiveId = committed.archiveId;
      this.archiveUri = committed.archiveUri;
      this.taskId = committed.taskId;
      this.overviewReady = false;
      this.overviewUnavailable = false;
      this.overviewAttempts = 0;
      this.previousOverview = previousOverview;
      this.overviewText = "";
      try {
        this.siblingToolNames = normalizeNames(
          ctx.siblingToolNames || siblingToolNamesFromBranch(ctx.branch, ctx.toolCallId),
        );
      } catch {
        this.siblingToolNames = [];
      }
      this.headerText = this.rebuildHeader({ overviewState: "pending", previousWindowId });
      this.previousWindowId = previousWindowId;
      this.remindersSent = { soft: false, hard: false };
      this.awaitingFirstObservation = true;
      this.disarmLogged = false;
      this.lastResetAt = this.safeNow();
      this.lastResetBy = "agent";
      this.ovSessionId = this.safeSessionId();
      this.syncedEntryCount = this.safeWatermark();
      this.persist();
      this.log(
        `context-window: opened ${this.windowId} from ${committed.archiveId || "(no archive)"} ` +
          `after a post-commit failure (${err?.message || err})`,
      );
      return {
        ok: true,
        kind: "reset",
        text:
          `Context window ${this.windowId} is open. ${previousWindowId} was archived to OpenViking as ` +
          `${committed.archiveId || "an archive"}, but the wait for its Working Memory failed ` +
          `(${err?.message || err}). Work from your own handoff notes in the window header and use ` +
          "history to read the archived messages. This tool result is not part of the new window.",
        details: {
          windowId: this.windowId,
          previousWindowId,
          archiveId: committed.archiveId,
          archiveUri: committed.archiveUri,
          taskId: committed.taskId,
          overviewState: "pending",
          attempts: 0,
          error: String(err?.message || err),
          waitedMs: this.safeNow() - ctx.startedAt,
        },
      };
    } catch {
      return null;
    }
  }

  /**
   * Budget for one flush barrier: never more than 15s of the reset deadline,
   * but never less than 2s either — a barrier called with a nearly empty budget
   * reports "not delivered" for a queue that would have drained in a moment.
   */
  flushBudget(deadline) {
    const remaining = Number(deadline) - this.io.now();
    return Math.max(2000, Math.min(15000, Number.isFinite(remaining) ? remaining : 0));
  }

  /** Cancelled before anything irreversible happened. */
  cancelled(stage) {
    return {
      ok: false,
      kind: "noop",
      // Every non-success text starts with the same marker so the model has one
      // string to key off: "Context window NOT reset".
      text:
        "Context window NOT reset: the reset was cancelled before anything was archived. " +
        "Nothing changed; keep working.",
      details: { stage },
    };
  }

  /**
   * Retract the handoff note that is already in the live OpenViking session.
   * Best effort and never fatal: it is a second message, not a deletion, and
   * the Working Memory prompt reads it as "that boundary did not happen".
   */
  async retractHandoff() {
    try {
      await this.io.postHandoff(
        `${HANDOFF_MARKER} RETRACTED — the reset of ${this.windowId} failed, this window is still open. ` +
          "Ignore the handoff note above; it does not mark a window boundary.",
      );
    } catch {
      // The refusal itself is what matters; a failed retraction only leaves the
      // orphan note behind.
    }
  }

  async refuseAfterHandoff(cause, details = {}) {
    await this.retractHandoff();
    return this.refuse(cause, details);
  }

  async cancelledAfterHandoff(stage) {
    await this.retractHandoff();
    return this.cancelled(stage);
  }

  refuse(cause, details = {}) {
    const trace = details.traceId ? ` (trace ${details.traceId})` : "";
    return {
      ok: false,
      kind: "refused",
      text: `Context window NOT reset: ${cause}${trace}. ${FAIL_CLOSED_TAIL}`,
      details,
    };
  }

  /**
   * Poll `<archive_uri>/.overview.md` until it exists (the only readable
   * readiness signal — the archives route is blocked on the gateway).
   */
  async waitForOverview({ archiveUri, archiveId, taskId, deadline, signal, onProgress }) {
    let attempts = 0;
    let state = "pending";
    let overview = "";
    /**
     * Polls left after the archive task reached a terminal state without a
     * Working Memory; -1 while the task is still running. A commit whose phase 2
     * finishes with an empty summary never writes `.overview.md` at all
     * (session.py skips the write), and the task ends as `completed` — without
     * this the wait would burn the entire deadline on every single reset.
     */
    let graceLeft = -1;

    while (true) {
      if (signal?.aborted) {
        state = "pending";
        break;
      }

      attempts++;
      let text = null;
      try {
        text = await this.io.readArchiveOverview(archiveUri);
      } catch {
        text = null;
      }
      if (typeof text === "string" && text.trim()) {
        overview = text.trim();
        state = "ready";
        break;
      }

      if (taskId && graceLeft < 0 && (attempts === 2 || attempts % 5 === 0)) {
        let task = null;
        try {
          task = await this.io.getTask(taskId);
        } catch {
          task = null;
        }
        const status = task ? String(task.status || "") : "";
        if (status === "failed" || status === "cancelled") {
          state = "unavailable";
          break;
        }
        if (status === "completed") {
          // Phase 2 is over. Either the write is a moment behind this read, or
          // there is no Working Memory to wait for at all.
          graceLeft = 2;
        }
      } else if (graceLeft > 0) {
        graceLeft -= 1;
      } else if (graceLeft === 0) {
        state = "unavailable";
        this.log(`context-window: ${archiveId} finished without a Working Memory`);
        break;
      }

      const remaining = deadline - this.io.now();
      if (remaining <= 0) {
        state = "pending";
        break;
      }
      try {
        onProgress?.(`waiting for the Working Memory of ${archiveId} (attempt ${attempts})`);
      } catch {
        // progress reporting must never break the reset
      }
      await this.io.sleep(Math.min(this.config.archivePollMs, remaining), signal);
    }

    return { state, overview, attempts };
  }

  rebuildHeader({ overviewState, previousWindowId }) {
    return buildWindowHeader({
      windowId: this.windowId,
      previousWindowId: previousWindowId || this.previousWindowId || `w${Math.max(1, this.windowIndex - 1)}`,
      archiveId: this.archiveId,
      archiveUri: this.archiveUri,
      openedAt: this.openedAt,
      reason: this.reason,
      notes: this.notes,
      nextSteps: this.nextSteps,
      pendingRequest: this.pendingRequest,
      overview: this.overviewText,
      overviewState,
      previousOverview: this.previousOverview,
      previousArchiveId: this.previousArchiveId,
      undeliveredCount: this.undeliveredCount,
      siblingToolNames: this.siblingToolNames,
      config: this.config,
    });
  }

  /** Remember which window a fresh archive belongs to (plan §10, carry-over 2). */
  recordArchive(windowId, archiveId, archiveUri) {
    const id = String(archiveId || "").trim();
    if (!id) return;
    const row = { windowId: String(windowId || this.windowId), archiveId: id, archiveUri: String(archiveUri || "") };
    const existing = this.archives.findIndex((a) => a.archiveId === id);
    if (existing >= 0) this.archives[existing] = row;
    else this.archives.push(row);
    if (this.archives.length > ARCHIVE_HISTORY_LIMIT) {
      this.archives = this.archives.slice(-ARCHIVE_HISTORY_LIMIT);
    }
  }

  /**
   * Non-blocking follow-up for a degraded window: one `.overview.md` read per
   * turn until it lands (then the header is rebuilt once — a single cache miss)
   * or the attempt budget runs out.
   */
  async refreshPendingOverview() {
    if (!this.armed || this.overviewReady || this.overviewUnavailable) return false;
    if (!this.archiveUri) return false;

    if (this.overviewAttempts >= this.config.overviewRefreshMaxAttempts) {
      this.markOverviewUnavailable();
      return false;
    }

    this.overviewAttempts += 1;
    let text = null;
    try {
      text = await this.io.readArchiveOverview(this.archiveUri);
    } catch {
      text = null;
    }

    if (typeof text === "string" && text.trim()) {
      this.overviewText = text.trim();
      this.overviewReady = true;
      this.headerText = this.rebuildHeader({ overviewState: "ready" });
      this.persist();
      this.log(`context-window: working memory attached to ${this.windowId}`);
      return true;
    }

    if (this.overviewAttempts >= this.config.overviewRefreshMaxAttempts) {
      this.markOverviewUnavailable();
    }
    return false;
  }

  markOverviewUnavailable() {
    if (this.overviewUnavailable) return;
    this.overviewUnavailable = true;
    this.headerText = this.rebuildHeader({ overviewState: "unavailable" });
    this.persist();
    this.log(`context-window: working memory unavailable for ${this.archiveId || this.windowId}`);
  }

  // ---- signals ----------------------------------------------------------

  usedTokens(usage) {
    let used = Math.max(0, Math.floor(Number(this.lastWindowTokens) || 0));
    let estimated = true;
    const reported = usage && usage.tokens !== null && usage.tokens !== undefined ? Number(usage.tokens) : NaN;
    if (!this.awaitingFirstObservation && Number.isFinite(reported)) {
      used = Math.max(used, Math.floor(reported));
      estimated = false;
    }
    return { used, estimated };
  }

  /**
   * Soft/hard percentages for this call, clamped under pi's own auto-compaction
   * line when the caller knows `reserveTokens` (settings `compaction.reserveTokens`).
   */
  thresholdsFor(contextWindow, reserveTokens) {
    return effectiveThresholds({
      softPercent: this.config.softPercent,
      hardPercent: this.config.hardPercent,
      contextWindow,
      reserveTokens,
    });
  }

  dueReminder({ usage = null, contextWindow = 0, reserveTokens = 0 } = {}) {
    if (this.awaitingFirstObservation) return null;
    const total = Math.max(0, Math.floor(Number(contextWindow) || Number(usage?.contextWindow) || 0));
    if (total <= 0) return null;
    const { used } = this.usedTokens(usage);
    if (used <= 0) return null;

    const limits = this.thresholdsFor(total, reserveTokens);
    const pct = (used / total) * 100;
    if (pct >= limits.hardPercent && !this.remindersSent.hard) {
      this.remindersSent.hard = true;
      this.remindersSent.soft = true;
      return "hard";
    }
    if (pct >= limits.softPercent && !this.remindersSent.soft) {
      this.remindersSent.soft = true;
      return "soft";
    }
    return null;
  }

  statusSnapshot({ usage = null, now = null, messages = [], reserveTokens = 0 } = {}) {
    const nowMs = Number.isFinite(Number(now)) ? Number(now) : this.io.now();
    const stamps = lastUserTimestamps(messages);
    const sinceLastUserMs = stamps.length > 0 ? Math.max(0, nowMs - stamps[0]) : null;
    const idleGapMs = stamps.length >= 2 ? Math.max(0, stamps[0] - stamps[1]) : null;

    const contextWindow = Math.max(0, Math.floor(Number(usage?.contextWindow) || 0));
    const { used, estimated } = this.usedTokens(usage);
    const percent = percentOf(used, contextWindow);
    const tokensLeft = contextWindow > 0 ? Math.max(0, contextWindow - used) : null;

    const limits = this.thresholdsFor(contextWindow, reserveTokens);
    let advice = "no action needed";
    if (percent >= limits.hardPercent) advice = "save your notes and call new_context now";
    else if (percent >= limits.softPercent) {
      advice = "past the soft threshold: update your notes and reset at the next stopping point";
    }

    return {
      windowId: this.windowId,
      turnsInWindow: this.turnsInWindow,
      usedTokens: used,
      contextWindow,
      percent,
      tokensLeft,
      estimated,
      sinceLastUserMs,
      idleGapMs,
      archiveId: this.archiveId,
      // The real number of archives this session produced. Deriving it from the
      // window index would over-count: a window closed by an external
      // compaction advances the index without producing one.
      archiveCount: this.archives.length,
      archives: this.archives.map((a) => ({ ...a })),
      undeliveredCount: this.undeliveredCount,
      overviewReady: this.overviewReady,
      windowAgeMs: this.openedAt > 0 ? Math.max(0, nowMs - this.openedAt) : null,
      softPercent: limits.softPercent,
      hardPercent: limits.hardPercent,
      advice,
    };
  }

  // ---- pi compaction fallback ------------------------------------------

  /**
   * `session_before_compact`. Either takes over pi's compaction with an
   * OpenViking archive + our window header as the summary, or (right after a
   * reset) short-circuits it so a stale usage estimate cannot double-archive.
   */
  async handleBeforeCompact({ preparation = {}, branchEntries = [] } = {}) {
    const prep = preparation && typeof preparation === "object" ? preparation : {};
    const entries = Array.isArray(branchEntries) ? branchEntries : [];
    const tokensBefore = Number(prep.tokensBefore) || 0;

    const sinceReset = this.lastResetAt > 0 ? this.io.now() - this.lastResetAt : Infinity;
    const noNewSync = this.safeWatermark() <= this.syncedEntryCount;
    // After an *external* compaction our header describes an archive that no
    // longer covers what pi is about to drop, so it must not be reused as the
    // summary — let pi write its own instead.
    const ourHeader = this.headerText && this.lastResetBy !== "external";
    if (ourHeader && sinceReset < this.config.recentResetGuardMs && noNewSync) {
      this.log(`context-window: compaction suppressed, ${this.windowId} was opened ${sinceReset}ms ago`);
      return {
        compaction: {
          summary: this.headerText,
          firstKeptEntryId: this.pickFirstKeptEntryId(prep, entries),
          tokensBefore,
          details: { source: "openviking", reason: "recent-reset-guard" },
        },
      };
    }

    if (this.resetting) return undefined;
    this.resetting = true;
    try {
      if (!this.io.connected() || !this.safeSessionId()) return undefined;
      const deadline = this.io.now() + this.config.resetDeadlineMs;

      // pi hands us the branch it is about to compact; whatever of it the
      // capture path has not delivered yet must reach the archive first, or the
      // window header would claim messages that were never archived.
      const synced = await this.io.syncBranch(entries);
      if (synced && synced.allDelivered === false) return undefined;

      if (!(await this.io.flush({ budgetMs: this.flushBudget(deadline) }))) return undefined;

      const previousWindowId = this.windowId;
      const reason = "automatic: pi compaction threshold";
      // Not `this.notes`: those were written for the window that *opened* here,
      // and repeating them would present them as this window's handoff.
      const posted = await this.io.postHandoff(
        buildHandoffMessage({
          windowId: previousWindowId,
          nextWindowId: `w${this.windowIndex + 1}`,
          reason,
          notes: AUTO_COMPACT_NOTES,
          nextSteps: [],
        }),
      );
      if (!posted) return undefined;

      const commit = await this.io.commit({ keepRecentCount: 0 });
      if (!commit || typeof commit !== "object" || !commit.archive_uri) {
        await this.retractHandoff();
        return undefined;
      }

      const archiveUri = String(commit.archive_uri || "");
      const archiveId = basename(archiveUri);
      const taskId = String(commit.task_id || "");
      const wait = await this.waitForOverview({
        archiveUri,
        archiveId,
        taskId,
        deadline,
        signal: null,
        onProgress: null,
      });

      const firstKeptEntryId = this.pickFirstKeptEntryId(prep, entries);
      const previousOverview = this.overviewText;

      this.windowIndex += 1;
      // pi performs a native cut here, so the virtual anchor must go.
      this.anchorToolCallId = null;
      this.openedAt = this.io.now();
      this.reason = reason;
      // The notes belonged to the window that just closed, so they are not this
      // window's handoff; the header would otherwise label them as such.
      this.notes = "";
      this.nextSteps = [];
      this.pendingRequest = lastUserTextFromBranch(entries);
      this.previousArchiveId = this.archiveId;
      this.recordArchive(previousWindowId, archiveId, archiveUri);
      this.undeliveredCount = this.safeDroppedCount();
      this.archiveId = archiveId;
      this.archiveUri = archiveUri;
      this.taskId = taskId;
      this.overviewReady = wait.state === "ready";
      this.overviewUnavailable = wait.state === "unavailable";
      this.overviewAttempts = 0;
      this.siblingToolNames = [];
      this.previousOverview = previousOverview;
      this.overviewText = wait.overview;
      this.headerText = this.rebuildHeader({ overviewState: wait.state, previousWindowId });
      this.previousWindowId = previousWindowId;
      this.remindersSent = { soft: false, hard: false };
      this.awaitingFirstObservation = true;
      this.disarmLogged = false;
      this.lastResetAt = this.io.now();
      this.lastResetBy = "pi-compaction";
      this.ovSessionId = this.safeSessionId();
      this.syncedEntryCount = this.safeWatermark();
      this.persist();
      this.log(`context-window: pi compaction opened ${this.windowId} from ${archiveId || "(no archive)"}`);

      return {
        compaction: {
          summary: this.headerText,
          firstKeptEntryId,
          tokensBefore,
          details: { source: "openviking", reason: "pi-compaction" },
        },
      };
    } catch {
      return undefined;
    } finally {
      this.resetting = false;
    }
  }

  /**
   * Never hand pi a `firstKeptEntryId` that would resurrect messages the window
   * header already declares archived.
   */
  pickFirstKeptEntryId(preparation = {}, branchEntries = []) {
    const fallback = preparation?.firstKeptEntryId;
    if (!this.anchorToolCallId) return fallback;

    const entries = Array.isArray(branchEntries) ? branchEntries : [];
    let anchorIndex = -1;
    for (let i = entries.length - 1; i >= 0; i--) {
      const msg = entryMessage(entries[i]);
      if (isToolResult(msg) && String(msg.toolCallId || "") === this.anchorToolCallId) {
        anchorIndex = i;
        break;
      }
    }
    if (anchorIndex < 0) return fallback;

    let runEnd = anchorIndex;
    while (isToolResult(entryMessage(entries[runEnd + 1]))) runEnd++;

    if (fallback !== undefined && fallback !== null) {
      const prepIndex = entries.findIndex((entry) => entryId(entry) === fallback);
      if (prepIndex > runEnd) return fallback;
    }

    const next = entries[runEnd + 1];
    const nextId = entryId(next);
    if (nextId !== null && nextId !== undefined) return nextId;
    return COMPACTION_SENTINEL;
  }

  /**
   * pi (or another extension) compacted without us: drop the virtual cut.
   *
   * The window index still advances — the model's context really did change,
   * and the reminders have to re-arm for what is effectively a new window — but
   * no archive is produced, so this window id never appears in `this.archives`
   * and `history` never lists it. That is why window ids are resolved through
   * the recorded map rather than by numbering the server's archive list.
   */
  absorbExternalCompaction(reason = "external compaction") {
    this.anchorToolCallId = null;
    this.windowIndex += 1;
    this.openedAt = this.io.now();
    this.remindersSent = { soft: false, hard: false };
    this.awaitingFirstObservation = true;
    this.disarmLogged = false;
    this.lastResetAt = this.io.now();
    this.lastResetBy = "external";
    this.syncedEntryCount = this.safeWatermark();
    this.persist();
    this.log(`context-window: absorbed external compaction (${reason}), now ${this.windowId}`);
  }

  // ---- helpers ----------------------------------------------------------

  safeSessionId() {
    try {
      const sid = this.io.sessionId();
      return sid ? String(sid) : null;
    } catch {
      return null;
    }
  }

  safeNow() {
    try {
      const value = Number(this.io.now());
      return Number.isFinite(value) ? value : Date.now();
    } catch {
      return Date.now();
    }
  }

  safeDroppedCount() {
    try {
      const value = Number(this.io.droppedCount());
      return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : this.undeliveredCount;
    } catch {
      return this.undeliveredCount;
    }
  }

  safeCommitTraceId() {
    try {
      const value = this.io.commitTraceId();
      return value ? String(value) : "";
    } catch {
      return "";
    }
  }

  safeWatermark() {
    try {
      const value = Number(this.io.getWatermark());
      return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : this.syncedEntryCount;
    } catch {
      return this.syncedEntryCount;
    }
  }

  log(message) {
    try {
      this.io.log(message);
    } catch {
      // Logging must never affect pi's context path.
    }
  }
}
