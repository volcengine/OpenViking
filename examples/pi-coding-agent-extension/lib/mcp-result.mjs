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

/** Only canonical, unambiguous Rule blocks can be shortened safely. */
function boundedRules(text) {
  const matched = /^Matched (\d+) active Rule\(s\)\.$/m.exec(text);
  const starts = [...text.matchAll(/^## \[[^\n]+\] [^\n]*\n\nConstraint:\n/gm)];
  const blocks = starts.map((start, index) =>
    text.slice(start.index, starts[index + 1]?.index ?? text.length).trimEnd());
  const valid = text.startsWith("# Applicable OpenViking Rules\n")
    && matched && Number(matched[1]) === blocks.length
    && blocks.every((block) => /\nCompiled Rule URI: viking:\/\/[^\n]+(?:\nPolicy sources:\n(?:- [^\n]+\n?)+)?$/.test(block));
  const total = valid ? blocks.length : null;
  const header = valid && starts.length ? text.slice(0, starts[0].index).trimEnd() : "";
  const fits = (value) => Buffer.byteLength(value) <= MAX_RESULT_BYTES
    && value.split("\n").length <= MAX_RESULT_LINES;
  const kept = [];
  const notice = (count) => {
    const uri = valid ? /\nCompiled Rule URI: ([^\n]+)/.exec(blocks[count] ?? "")?.[1] : null;
    return `[OpenViking] delivery.complete=false; omitted_rules=${total === null ? "unknown" : total - count}. `
      + "Output budget reached. Do not act until remaining Rules are checked. "
      + "Retrieve smaller results or read compiled Rule URIs."
      + (uri && uri.length <= 500 ? ` First omitted Rule: ${uri}` : "");
  };
  if (valid) {
    for (const block of blocks) {
      if (!fits([header, ...kept, block, notice(kept.length + 1)].join("\n\n"))) break;
      kept.push(block);
    }
  }
  let output = [header, ...kept, notice(kept.length)].filter(Boolean).join("\n\n");
  // Even malformed/oversized headers must not evade the budget.
  if (!fits(output)) {
    kept.length = 0;
    output = notice(0);
  }
  return { text: output, delivery: { complete: false, omitted_rules: total === null ? null : total - kept.length } };
}

/** All text blocks share one budget, including the truncation notice. */
export function toPiResult(tool, result) {
  const content = mcpContentToPi(result);
  const text = joinText(content);
  const truncated = Buffer.byteLength(text) > MAX_RESULT_BYTES || text.split("\n").length > MAX_RESULT_LINES;
  if (tool === "rule_search" && truncated && !result.isError) {
    const bounded = boundedRules(text);
    return { content: [{ type: "text", text: bounded.text }], details: { tool, truncated: true, delivery: bounded.delivery } };
  }
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
  return { content, details: { tool, truncated: false, ...(tool === "rule_search" ? { delivery: { complete: true } } : {}) } };
}
