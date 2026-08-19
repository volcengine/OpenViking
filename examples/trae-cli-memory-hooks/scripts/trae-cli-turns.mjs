export function cleanTraeCliText(value) {
  return String(value || "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi, "")
    .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, "")
    .trim();
}

function firstTraeCliText(input, fields) {
  for (const field of fields) {
    const text = cleanTraeCliText(input?.[field]);
    if (text) return text;
  }
  return "";
}

export function resolveTraeCliPrompt(input = {}) {
  return firstTraeCliText(input, ["prompt", "user_prompt", "userPrompt", "message", "text"]);
}

export function resolveTraeCliResponse(input = {}) {
  return firstTraeCliText(input, [
    "last_assistant_message",
    "lastAssistantMessage",
    "assistant_message",
    "assistantMessage",
    "response",
    "output",
    "text_content",
  ]);
}

export function buildTraeCliTurns(input = {}, state = {}) {
  return [
    { role: "user", content: resolveTraeCliPrompt(input) || cleanTraeCliText(state.pendingPrompt?.prompt) },
    { role: "assistant", content: resolveTraeCliResponse(input) },
  ].filter((turn) => turn.content);
}
