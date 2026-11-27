import { userTurnText } from "./codebuddy-turns.mjs";

export function extractCursorText(content) {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n")
    .replace(/\[REDACTED\]/g, "")
    .trim();
}

const TIMESTAMP_RE = /<timestamp\s*>.*?<\/timestamp\s*>/gis;

export function parseCursorTranscript(raw) {
  const turns = [];
  for (const line of String(raw || "").split("\n")) {
    if (!line.trim()) continue;
    let item;
    try { item = JSON.parse(line); } catch { continue; }
    if (item?.role !== "user" && item?.role !== "assistant") continue;
    const text = extractCursorText(item.message?.content ?? item.content);
    // Cursor stamps each ask and wraps it: `<timestamp>…</timestamp><user_query>…</user_query>`.
    const content = item.role === "user" ? userTurnText(text.replace(TIMESTAMP_RE, " ")) : text;
    if (!content) continue;
    turns.push({ role: item.role, content });
  }
  return turns;
}
