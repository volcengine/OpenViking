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

export function parseCursorTranscript(raw) {
  const turns = [];
  for (const line of String(raw || "").split("\n")) {
    if (!line.trim()) continue;
    let item;
    try { item = JSON.parse(line); } catch { continue; }
    if (item?.role !== "user" && item?.role !== "assistant") continue;
    const content = extractCursorText(item.message?.content ?? item.content);
    if (!content) continue;
    turns.push({ role: item.role, content });
  }
  return turns;
}
