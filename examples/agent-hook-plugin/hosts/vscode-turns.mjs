/**
 * Turn extraction for VS Code Local hook events.
 *
 * The Local hooks reference documents `prompt` on UserPromptSubmit and nothing
 * else a capture could read: Stop is a turn boundary that carries no assistant
 * response, and `transcript_path` comes with the warning that "the transcript
 * file format is not a stable hook API and might change between VS Code
 * releases". So a capture holds the user side only — the prompt as submitted,
 * or the one the prompt hook tracked when the Stop event itself carries none.
 */

const INJECTED_BLOCK_RE = /<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi;
const RELEVANT_MEMORIES_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi;
const SYSTEM_REMINDER_RE = /<system-reminder>[\s\S]*?<\/system-reminder>/gi;

export function cleanVscodeText(value) {
  return String(value || "")
    .replace(INJECTED_BLOCK_RE, "")
    .replace(RELEVANT_MEMORIES_RE, "")
    .replace(SYSTEM_REMINDER_RE, "")
    .trim();
}

export function buildVscodeTurns(input = {}, state = {}) {
  return [
    { role: "user", content: cleanVscodeText(input.prompt || state.pendingPrompt?.prompt) },
  ].filter((turn) => turn.content);
}
