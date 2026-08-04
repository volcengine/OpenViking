/**
 * Pure transcript parser for ZCode hook events.
 *
 * ZCode's Stop hook stdin payload is not fully documented. Based on
 * reverse-engineering (#3127 by @quinn-zenith) and rollout file analysis,
 * the Stop payload contains at least:
 *   - session_id / sessionId
 *   - cwd
 *   - transcript_path (points to a TEMP file with only the LAST assistant
 *     message — NOT a complete conversation)
 *   - responseText / responsePreview (the last assistant response text)
 *
 * The rollout files at ~/.zcode/cli/rollout/model-io-*.jsonl contain
 * the COMPLETE conversation with this structure per line:
 *   { sessionId, turnId, type: "model_io",
 *     request: { messages: [{ role, content }] },
 *     response: { text, toolCalls, finishReason } }
 *
 * Strategy:
 * 1. Use the rollout file as the authoritative incremental transcript and
 *    extract ALL unseen turns since the last acknowledged turnId.
 * 2. Use stdin + pendingPrompt only when the rollout file is unavailable.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const INJECTED_BLOCK_RE = /<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi;
const RELEVANT_MEMORIES_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi;
const SYSTEM_REMINDER_RE = /<system-reminder>[\s\S]*?<\/system-reminder>/gi;

/**
 * Strip plugin-injected blocks and trim whitespace.
 */
export function cleanZcodeText(value) {
  return String(value || "")
    .replace(INJECTED_BLOCK_RE, "")
    .replace(RELEVANT_MEMORIES_RE, "")
    .replace(SYSTEM_REMINDER_RE, "")
    .trim();
}

/**
 * Resolve the rollout file path for a given session ID.
 * ZCode stores rollout at ~/.zcode/cli/rollout/model-io-<sessionId>.jsonl
 */
function resolveRolloutPath(input = {}) {
  const sessionId =
    input.session_id || input.sessionId || input.conversation_id || "";
  if (!sessionId) return null;
  const home = process.env.HOME || process.env.USERPROFILE || "";
  // ZCode rollout files are named: model-io-<sessionId>.jsonl
  // sessionId already includes the "sess_" prefix, so no extra "sess-" needed.
  return join(home, ".zcode", "cli", "rollout", `model-io-${sessionId}.jsonl`);
}

/**
 * Extract the user message from a rollout entry's request.messages.
 * Finds the last user-role message in the array.
 */
function extractUserFromMessages(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") {
      const content = typeof msg.content === "string"
        ? msg.content
        : Array.isArray(msg.content)
          ? msg.content.filter((b) => b?.type === "text").map((b) => b.text).join("\n")
          : "";
      const cleaned = cleanZcodeText(content);
      if (cleaned) return cleaned;
      break;
    }
  }
  return "";
}

/**
 * Read ALL unseen turns from a ZCode rollout file since lastKnownTurnId.
 * Each rollout line is a JSON object with turnId, request.messages, response.text.
 *
 * @param {string} rolloutPath - Path to the rollout JSONL file.
 * @param {string} lastKnownTurnId - The last processed turnId (exclusive bound).
 * @returns {Array<{role: string, content: string, turnId: string}>} Unseen turns.
 */
export function extractUnseenRolloutTurns(rolloutPath, lastKnownTurnId = null) {
  return readUnseenRolloutTurns(rolloutPath, lastKnownTurnId).turns;
}

function readUnseenRolloutTurns(rolloutPath, lastKnownTurnId = null) {
  if (!rolloutPath) return { available: false, turns: [] };
  let raw;
  try {
    raw = readFileSync(rolloutPath, "utf8");
  } catch {
    return { available: false, turns: [] };
  }

  const lines = raw.trim().split("\n").filter(Boolean);
  if (lines.length === 0) return { available: true, turns: [] };

  // If we have a lastKnownTurnId, find its position and return everything after.
  // If not, return only the last entry (first-time capture).
  let startIndex = 0;
  if (lastKnownTurnId) {
    const foundIndex = lines.findIndex((line) => {
      try {
        return JSON.parse(line).turnId === lastKnownTurnId;
      } catch {
        return false;
      }
    });
    if (foundIndex >= 0) startIndex = foundIndex + 1;
  }
  // No lastKnownTurnId → capture ALL entries (first-time capture should not
  // lose prior turns). Previously only returned the last entry.

  const turns = [];
  for (let i = startIndex; i < lines.length; i++) {
    let entry;
    try {
      entry = JSON.parse(lines[i]);
    } catch {
      continue;
    }
    const turnId = entry.turnId || "";
    const userContent = extractUserFromMessages(entry?.request?.messages || []);
    const assistantContent = cleanZcodeText(entry?.response?.text || "");
    if (userContent) {
      turns.push({ role: "user", content: userContent, turnId });
    }
    if (assistantContent) {
      turns.push({ role: "assistant", content: assistantContent, turnId });
    }
  }

  return { available: true, turns };
}

/**
 * Extract user/assistant turns from a ZCode Stop-event payload.
 *
 * Uses the rollout file first so every normal Stop observes stable host turn
 * IDs and can recover missed Stop events. Stdin is only a compatibility
 * fallback for environments where no rollout file can be read.
 *
 * @param {object} input - Raw hook stdin JSON.
 * @param {object} state - Persistent hook state (may contain pendingPrompt, lastTurnId).
 * @returns {Array<{role: string, content: string, turnId?: string}>} Non-empty turns.
 */
export function buildZcodeTurns(input = {}, state = {}) {
  const rolloutPath = resolveRolloutPath(input);
  const rollout = readUnseenRolloutTurns(rolloutPath, state.lastTurnId || null);
  if (rollout.available) return rollout.turns;

  // Assistant content: try documented and reverse-engineered field names
  const assistantContent =
    input.responseText ||
    input.responsePreview ||
    input.last_assistant_message ||
    input.assistantMessage ||
    input.assistant_message ||
    input.text_content ||
    input.response ||
    "";

  // User content: try multiple field names
  const userContent =
    input.prompt ||
    input.user_prompt ||
    input.userMessage ||
    input.user_message ||
    input.last_user_message ||
    input.message ||
    state.pendingPrompt?.prompt ||
    "";

  return [
    { role: "user", content: cleanZcodeText(userContent) },
    { role: "assistant", content: cleanZcodeText(assistantContent) },
  ].filter((turn) => turn.content);
}
