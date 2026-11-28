/**
 * pi has no transcript file: the wiring hands the session manager's branch
 * (`getBranch()`) in the payload, and each message entry is one turn keyed by
 * its entry id. Entry ids survive restarts, compaction and branch navigation.
 */

import { buildCaptureMessage, collectToolNamesByIdFromEntries, normalizeCaptureRole } from "./shared/capture-utils.mjs";
import { resumeTurns, turnKey } from "./shared/flow/turns.mjs";

function entryMessage(entry) {
  if (!entry || typeof entry !== "object") return null;
  if (entry.message && typeof entry.message === "object") return entry.message;
  return null;
}

/** The turns a slice of the branch holds, oldest first. */
export function branchTurns(branch) {
  const turns = [];
  for (const entry of Array.isArray(branch) ? branch : []) {
    const message = entryMessage(entry);
    if (!message || typeof entry.id !== "string" || !entry.id) continue;
    // pi spells a tool result `toolResult`; as `tool_result` it is the user side
    // of the wire and is left out with `captureAssistantTurns: false`.
    const raw = String(message.role || message.type || "");
    const role = raw === "toolResult" ? "tool_result" : raw;
    if (!normalizeCaptureRole(role)) continue;
    const content = typeof message.content === "string"
      ? message.content
      : Array.isArray(message.content)
        ? message.content.filter((block) => block?.type === "text").map((block) => block.text).join("\n")
        : "";
    turns.push({ role, content, turnId: entry.id, payload: message });
  }
  return turns;
}

/**
 * The cursor 0.4.x kept as an `ov-capture` entry on the branch (`lastEntryId`,
 * the branch tip it had captured through), as the flow's cursor: every turn at
 * or before that entry counts as captured.
 */
function fromEntryCursor(branch, lastEntryId, turns) {
  const at = branch.findIndex((entry) => entry?.id === lastEntryId);
  if (at < 0) return {};
  const before = new Set(branch.slice(0, at + 1).map((entry) => entry?.id));
  const captured = turns.filter((turn) => before.has(turn.turnId));
  return { lastTurnId: captured.at(-1)?.turnId ?? null, capturedTurnIds: captured.map(turnKey) };
}

/** @param {import("./shared/flow/host.d.mts").DecodeInput} input */
export function decode({ cursor, payload = {} }) {
  const branch = Array.isArray(payload.branch) ? payload.branch : [];
  const all = branchTurns(branch);
  const start = typeof cursor?.lastEntryId === "string" ? fromEntryCursor(branch, cursor.lastEntryId, all) : cursor;
  return resumeTurns(all, start && "capturedTurnIds" in start ? start : {});
}

/**
 * How many session messages a slice of the branch is sent as: the exact
 * `keep_recent_count` takeover asks the server for.
 */
export function captureCount(branchSlice, cfg = {}) {
  const turns = branchTurns(branchSlice);
  const toolNameById = collectToolNamesByIdFromEntries(turns);
  return turns.filter((turn) => buildCaptureMessage(turn.payload, turn.role, cfg, { toolNameById })).length;
}

/**
 * The cursor an earlier version left on the branch: its newest `ov-capture`
 * entry for this OpenViking session, or, on a branch that is not a fork, the
 * offset a published takeover checkpoint (`ov-takeover`) recorded.
 */
export function branchCursor(branch, ovSessionId, forked = false) {
  const entries = Array.isArray(branch) ? branch : [];
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry?.type !== "custom" || entry.customType !== "ov-capture") continue;
    const id = entry.data?.lastEntryId;
    if (entry.data?.sessionId !== ovSessionId || typeof id !== "string" || !id) continue;
    const anchor = entries.findIndex((item) => item?.id === id);
    if (anchor >= 0 && anchor < i) return { lastEntryId: id };
  }
  if (forked) return undefined;
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry?.type !== "custom" || entry.customType !== "ov-takeover") continue;
    const count = entry.data?.syncedEntryCount;
    if (Number.isSafeInteger(count) && count > 0 && count <= i) return { lastEntryId: entries[count - 1]?.id || "" };
    if (count === 0) return undefined;
  }
  return undefined;
}
