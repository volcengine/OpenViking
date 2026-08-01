/**
 * Pure transcript parser for ZCode hook events.
 *
 * ZCode's Stop payload shape is not yet fully documented. This parser follows
 * the same defensive strategy as trae-turns.mjs: read multiple candidate field
 * names, strip plugin-injected blocks, and return clean user/assistant turns.
 *
 * The field names probed (prompt, text_content, last_assistant_message, etc.)
 * mirror the conventions seen across Claude Code, TRAE, and Cursor payloads.
 * If ZCode uses different names, the shared runtime's readHookInput() provides
 * additional fallbacks, and this parser degrades gracefully (returns empty turns
 * rather than throwing).
 */

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
 * Extract user/assistant turns from a ZCode Stop-event payload.
 *
 * Probes multiple field names for resilience against undocumented payload shapes.
 * Falls back to a pending prompt stored in hook state if the current payload
 * has no prompt (e.g. Stop fires after the prompt was consumed).
 *
 * @param {object} input - Raw hook stdin JSON.
 * @param {object} state - Persistent hook state (may contain pendingPrompt).
 * @returns {Array<{role: string, content: string}>} Non-empty turns.
 */
export function buildZcodeTurns(input = {}, state = {}) {
  // User prompt: try multiple field names
  const userContent =
    input.prompt ||
    input.user_prompt ||
    input.userMessage ||
    input.user_message ||
    input.message ||
    state.pendingPrompt?.prompt ||
    "";

  // Assistant response: try multiple field names
  const assistantContent =
    input.last_assistant_message ||
    input.assistant_message ||
    input.assistantMessage ||
    input.last_assistant_message_text ||
    input.text_content ||
    input.responseText ||
    input.response ||
    "";

  return [
    { role: "user", content: cleanZcodeText(userContent) },
    { role: "assistant", content: cleanZcodeText(assistantContent) },
  ].filter((turn) => turn.content);
}
