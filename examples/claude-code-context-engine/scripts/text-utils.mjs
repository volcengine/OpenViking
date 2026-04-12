/**
 * Text processing utilities for transcript parsing and capture decisions.
 */

const RELEVANT_MEMORIES_BLOCK_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi;
const OPENVIKING_CONTEXT_BLOCK_RE = /<openviking-context>[\s\S]*?<\/openviking-context>/gi;
const CJK_CHAR_RE = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/;

export function sanitize(text) {
  return text
    .replace(RELEVANT_MEMORIES_BLOCK_RE, " ")
    .replace(OPENVIKING_CONTEXT_BLOCK_RE, " ")
    .replace(/\u0000/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return data;
  } catch { /* not a JSON array */ }

  const lines = content.split("\n").filter(l => l.trim());
  const messages = [];
  for (const line of lines) {
    try { messages.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return messages;
}

export function extractAllTurns(messages) {
  const turns = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;

    let role = msg.role;
    let text = "";

    if (typeof msg.content === "string") {
      text = msg.content;
    } else if (Array.isArray(msg.content)) {
      const textParts = msg.content
        .filter(b => b && b.type === "text" && typeof b.text === "string")
        .map(b => b.text);
      text = textParts.join("\n");
    } else if (typeof msg.message === "object" && msg.message) {
      const inner = msg.message;
      role = inner.role || role;
      if (typeof inner.content === "string") {
        text = inner.content;
      } else if (Array.isArray(inner.content)) {
        const textParts = inner.content
          .filter(b => b && b.type === "text" && typeof b.text === "string")
          .map(b => b.text);
        text = textParts.join("\n");
      }
    }

    if (role !== "user" && role !== "assistant") continue;
    if (text.trim()) {
      turns.push({ role, text: text.trim() });
    }
  }
  return turns;
}

/** Estimate tokens from text (rough: chars / 4). */
export function estimateTokens(text) {
  return Math.ceil((text || "").length / 4);
}

/** Group adjacent same-role turns. */
export function groupTurns(turns) {
  if (turns.length === 0) return [];
  const groups = [];
  let current = { role: turns[0].role, texts: [turns[0].text] };

  for (let i = 1; i < turns.length; i++) {
    if (turns[i].role === current.role) {
      current.texts.push(turns[i].text);
    } else {
      groups.push(current);
      current = { role: turns[i].role, texts: [turns[i].text] };
    }
  }
  groups.push(current);
  return groups;
}
