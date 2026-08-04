export function cleanZcodeText(value) {
  return String(value || "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi, "")
    .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, "")
    .trim();
}

export function buildZcodeTurns(input = {}, state = {}) {
  return [
    { role: "user", content: cleanZcodeText(input.prompt || state.pendingPrompt?.prompt) },
    { role: "assistant", content: cleanZcodeText(input.last_assistant_message || input.text_content) },
  ].filter((turn) => turn.content);
}
