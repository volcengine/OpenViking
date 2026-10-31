import { createHash } from "node:crypto";
import { join } from "node:path";
import { withPrivateState } from "./private-state.mjs";

export const recallDigest = (value) => createHash("sha256").update(value).digest("hex");

/** Object key order does not identify a different message; array order does. */
export function canonicalRecallValue(value) {
  if (Array.isArray(value)) return value.map(canonicalRecallValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalRecallValue(value[key])]));
  }
  return value;
}

// Anthropic cache annotations describe transport caching, not conversation
// identity. Normalize only protocol content blocks, never arbitrary tool input.
function anthropicContent(value) {
  if (typeof value === "string") return [{ type: "text", text: value }];
  if (!Array.isArray(value)) return value;
  return value.map((block) => {
    if (!block || typeof block !== "object" || Array.isArray(block)) return block;
    const { cache_control, ...semantic } = block;
    if (semantic.type === "tool_result") semantic.content = anthropicContent(semantic.content);
    return semantic;
  });
}

/** Prefix anchors distinguish repeated text, branches and replaced histories. */
export function recallAnchors(messages, prefix = {}, protocol) {
  const identityPrefix = protocol === "anthropic" ? { ...prefix, system: anthropicContent(prefix.system) } : prefix;
  const hash = createHash("sha256").update(JSON.stringify(canonicalRecallValue(identityPrefix)));
  return messages.map((message) => {
    const identity = protocol === "anthropic" ? { ...message, content: anthropicContent(message.content) } : message;
    hash.update("\0").update(JSON.stringify(canonicalRecallValue(identity)));
    return hash.copy().digest("hex");
  });
}


export async function withRecallLedger(directory, scope, signal, operation) {
  return withPrivateState(join(directory, "recall"), scope, signal, (state) => operation(state.decisions), {
    initial: () => ({ version: 1, decisions: {} }),
    validate: (state) => {
      if (state?.version !== 1 || !state.decisions || typeof state.decisions !== "object" || Array.isArray(state.decisions)) return false;
      return Object.entries(state.decisions).every(([anchor, decision]) => /^[a-f0-9]{64}$/.test(anchor)
        && decision && typeof decision.block === "string"
        && ["ready", "empty", "skipped", "unauthorized", "invalid", "unavailable"].includes(decision.outcome));
    },
  });
}
