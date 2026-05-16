export function sanitizeCapturedText(text) {
  let out = String(text || "");
  if (/^\s*You are running as a subagent\b/i.test(out)) return "";
  out = out
    .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, "")
    .replace(/<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>[\s\S]*?<<<END_OPENCLAW_INTERNAL_CONTEXT>>>/g, "")
    .replace(/Conversation context \(untrusted metadata\):\s*```json[\s\S]*?```\s*/gi, "")
    .replace(/\[Inter-session message][\s\S]*?(?=\n\[[a-z]+]:|\n?$)/gi, "")
    .replace(/Full hook output saved to:\s*\S+/gi, "")
    .trim();
  if (/^(?:\[user]:\s*)*$/i.test(out)) return "";
  return out;
}
