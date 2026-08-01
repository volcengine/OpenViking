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
 * The rollout files at ~/.zcode/cli/rollout/model-io-sess-*.jsonl contain
 * the COMPLETE conversation with this structure per line:
 *   { sessionId, turnId, type: "model_io",
 *     request: { messages: [{ role, content }] },
 *     response: { text, toolCalls, finishReason } }
 *
 * Strategy:
 * 1. Try stdin payload fields for per-turn user/assistant content
 * 2. If stdin lacks user content, read the rollout file for the session
 *    and extract the last user + assistant pair from request.messages
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
 * ZCode stores rollout at ~/.zcode/cli/rollout/model-io-sess-<sessionId>.jsonl
 */
function resolveRolloutPath(input = {}) {
  const sessionId =
    input.session_id || input.sessionId || input.conversation_id || "";
  if (!sessionId) return null;
  return join(process.env.HOME || process.env.USERPROFILE || "", ".zcode", "cli", "rollout", `model-io-sess-${sessionId}.jsonl`);
}

/**
 * Read the last user + assistant turn from a ZCode rollout file.
 * Each line is a JSON object with request.messages (full history) and response.text.
 * Returns { role, content } pairs for the last user message and last assistant response.
 */
function extractFromRollout(rolloutPath) {
  if (!rolloutPath) return [];
  let raw;
  try {
    raw = readFileSync(rolloutPath, "utf8");
  } catch {
    return [];
  }

  const lines = raw.trim().split("\n").filter(Boolean);
  if (lines.length === 0) return [];

  // Parse the last entry — it has the full request.messages including the latest user turn
  let lastEntry;
  try {
    lastEntry = JSON.parse(lines[lines.length - 1]);
  } catch {
    return [];
  }

  const turns = [];
  const messages = lastEntry?.request?.messages || [];

  // Extract the last user message
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") {
      const content = typeof msg.content === "string"
        ? msg.content
        : Array.isArray(msg.content)
          ? msg.content.filter((b) => b?.type === "text").map((b) => b.text).join("\n")
          : "";
      const cleaned = cleanZcodeText(content);
      if (cleaned) {
        turns.push({ role: "user", content: cleaned });
      }
      break;
    }
  }

  // Extract the assistant response from the same entry
  const responseText = lastEntry?.response?.text || "";
  const cleanedResponse = cleanZcodeText(responseText);
  if (cleanedResponse) {
    turns.push({ role: "assistant", content: cleanedResponse });
  }

  return turns;
}

/**
 * Extract user/assistant turns from a ZCode Stop-event payload.
 *
 * Tries stdin payload fields first (responseText/responsePreview for assistant,
 * prompt/last_user_message for user). Falls back to rollout file if user
 * content is missing from stdin (which is the known ZCode limitation).
 *
 * @param {object} input - Raw hook stdin JSON.
 * @param {object} state - Persistent hook state (may contain pendingPrompt).
 * @returns {Array<{role: string, content: string}>} Non-empty turns.
 */
export function buildZcodeTurns(input = {}, state = {}) {
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

  // Build turns from stdin payload
  let turns = [
    { role: "user", content: cleanZcodeText(userContent) },
    { role: "assistant", content: cleanZcodeText(assistantContent) },
  ].filter((turn) => turn.content);

  // If user content is missing from stdin (the known ZCode limitation),
  // fall back to the rollout file for the complete conversation.
  if (turns.length < 2 || turns[0]?.role !== "user") {
    const rolloutPath = resolveRolloutPath(input);
    const rolloutTurns = extractFromRollout(rolloutPath);
    if (rolloutTurns.length > 0) {
      // Prefer rollout turns — they have verified user + assistant content
      turns = rolloutTurns;
    }
  }

  return turns;
}
