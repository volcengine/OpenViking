export function cleanTraeText(value) {
  return String(value || "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi, "")
    .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, "")
    .trim();
}

export function buildTraeTurns(input = {}, state = {}) {
  return [
    { role: "user", content: cleanTraeText(input.prompt || state.pendingPrompt?.prompt) },
    { role: "assistant", content: cleanTraeText(input.last_assistant_message || input.text_content) },
  ].filter((turn) => turn.content);
}
