/** MCP content adapted to pi's text/image tool results. */
export const MAX_RESULT_BYTES = 50 * 1024;
export const MAX_RESULT_LINES = 2000;
const isPlainObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function joinText(blocks) {
  return (Array.isArray(blocks) ? blocks : [])
    .filter((block) => block && block.type === "text")
    .map((block) => String(block.text ?? ""))
    .join("\n");
}

/**
 * An MCP `tools/call` result as pi content blocks.
 *
 * pi's content union is text or image; everything else becomes one readable
 * text line so the model still learns what came back.
 */
export function mcpContentToPi(result) {
  const blocks = Array.isArray(result?.content) ? result.content : [];
  const out = [];
  for (const block of blocks) {
    if (!isPlainObject(block)) continue;
    if (block.type === "text") {
      out.push({ type: "text", text: String(block.text ?? "") });
    } else if (block.type === "image") {
      out.push({
        type: "image",
        data: String(block.data ?? ""),
        mimeType: String(block.mimeType ?? block.mime_type ?? "image/png"),
      });
    } else if (block.type === "audio") {
      out.push({ type: "text", text: `[audio content omitted (${String(block.mimeType ?? "unknown type")})]` });
    } else if (block.type === "resource") {
      const resource = isPlainObject(block.resource) ? block.resource : {};
      const inline = typeof resource.text === "string" ? `\n${resource.text}` : "";
      const mime = resource.mimeType ? ` (${resource.mimeType})` : "";
      out.push({ type: "text", text: `[resource ${String(resource.uri ?? "?")}${mime}]${inline}` });
    } else if (block.type === "resource_link") {
      const label = block.name ? ` — ${block.name}` : "";
      out.push({ type: "text", text: `[resource link ${String(block.uri ?? "?")}${label}]` });
    } else {
      out.push({ type: "text", text: `[unsupported MCP content block: ${String(block.type ?? "?")}]` });
    }
  }

  // structuredContent is appended only when it says something the upstream text
  // does not. FastMCP echoes a {"result": "<the same text>"} beside the text
  // block for every tool annotated `-> str`, so the comparison must be against
  // the UPSTREAM text — never against the notes synthesised above for
  // image/audio/resource blocks, which would make every such echo look new.
  const structured = result?.structuredContent;
  if (isPlainObject(structured)) {
    const text = joinText(blocks).trim();
    const serialized = JSON.stringify(structured);
    const duplicate =
      (typeof structured.result === "string" && structured.result.trim() === text)
      || serialized.trim() === text;
    if (!duplicate && serialized) out.push({ type: "text", text: serialized });
  }

  if (out.length === 0) out.push({ type: "text", text: "" });
  return out;
}

/** All text blocks share one budget, including the truncation notice. */
export function toPiResult(tool, result) {
  const content = mcpContentToPi(result);
  const text = joinText(content);
  const truncated = Buffer.byteLength(text) > MAX_RESULT_BYTES || text.split("\n").length > MAX_RESULT_LINES;
  if (truncated) {
    const hint = "\n[OpenViking] Output truncated. Request fewer items or use a narrower URI, offset or limit.";
    const lines = text.split("\n").slice(0, MAX_RESULT_LINES - 1).join("\n");
    const buffer = Buffer.from(lines);
    let end = Math.min(buffer.length, MAX_RESULT_BYTES - Buffer.byteLength(hint));
    while (end > 0 && (buffer[end] & 0xc0) === 0x80) end--;
    // Consolidating text keeps the limit independent of MCP block boundaries.
    const shortened = buffer.subarray(0, end).toString("utf8") + hint;
    if (result.isError) throw new Error(shortened);
    return {
      content: [{ type: "text", text: shortened }, ...content.filter((block) => block.type === "image")],
      details: { tool, truncated: true },
    };
  }
  if (result.isError) throw new Error(text.trim() || "OpenViking " + tool + " failed");
  return { content, details: { tool, truncated: false } };
}
