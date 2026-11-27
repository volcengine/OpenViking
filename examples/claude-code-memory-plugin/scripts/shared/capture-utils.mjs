// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { applyInputFilters, compileInputFilters } from "./input-filters.mjs";

const TEXT_BLOCK_TYPES = new Set(["text", "input_text", "output_text"]);
const TOOL_CALL_TYPES = new Set([
  "tool_call",
  "toolcall",
  "tool_use",
  "tooluse",
  "function_call",
  "functioncall",
]);
const TOOL_RESULT_TYPES = new Set([
  "tool_result",
  "toolresult",
  "tool_output",
  "tooloutput",
  "function_call_output",
  "functioncalloutput",
]);

const CONTENT_TEXT_TYPES = new Set(["text", "inputtext", "outputtext"]);
const CONTENT_IMAGE_TYPES = new Set(["image", "inputimage", "outputimage"]);

// Tool output is reported verbatim; the server owns truncation via
// tool_output_externalization (threshold_chars, default 20000). This cap only
// guards against pathological payloads.
const DEFAULT_TOOL_MAX_CHARS = 1000000;

const METADATA_KEYS = [
  "session_id",
  "sessionid",
  "sessionkey",
  "conversation_id",
  "conversationid",
  "channel",
  "sender",
  "user_id",
  "userid",
  "agent_id",
  "agentid",
  "timestamp",
  "timezone",
  "cwd",
  "model",
  "permission_mode",
];

function normalizeType(value) {
  return String(value || "").toLowerCase().replace(/[-\s]/g, "_");
}

function isToolCallBlock(block) {
  const type = normalizeType(block?.type || block?.kind || block?.role);
  return TOOL_CALL_TYPES.has(type) || Boolean(block?.tool_calls) || Boolean(block?.function?.name);
}

function isToolResultBlock(block) {
  const type = normalizeType(block?.type || block?.kind || block?.role);
  return TOOL_RESULT_TYPES.has(type) || type === "tool" || type === "function";
}

function oneLine(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function truncateCaptureText(text, maxChars = 2000) {
  const value = String(text || "").trim();
  if (!Number.isFinite(maxChars) || maxChars <= 0 || value.length <= maxChars) return value;
  return `${value.slice(0, Math.max(0, maxChars - 20)).trimEnd()}\n[truncated]`;
}

function stringifyCompact(value, maxChars) {
  if (value == null) return "";
  if (typeof value === "string") return truncateCaptureText(value, maxChars);
  try {
    return truncateCaptureText(JSON.stringify(value), maxChars);
  } catch {
    return truncateCaptureText(String(value), maxChars);
  }
}

function parseMaybeJson(value) {
  if (value == null || typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function blockText(block) {
  if (!block || typeof block !== "object") return "";
  if (typeof block.text === "string") return block.text;
  if (typeof block.output_text === "string") return block.output_text;
  if (typeof block.input_text === "string") return block.input_text;
  if (typeof block.content === "string") return block.content;
  return "";
}

function toolName(block) {
  return oneLine(
    block?.name ||
    block?.tool_name ||
    block?.toolName ||
    block?.tool ||
    block?.function?.name ||
    block?.call?.name ||
    "",
  );
}

function toolPayload(block, kind) {
  if (!block || typeof block !== "object") return "";
  if (kind === "call") {
    return block.input ??
      block.state?.input ??
      block.arguments ??
      block.args ??
      block.params ??
      block.function?.arguments ??
      block.command ??
      block.call?.input ??
      block.call?.arguments ??
      "";
  }
  const output = block.output ??
    block.state?.output ??
    block.result ??
    block.error ??
    block.state?.error ??
    block.data ??
    block.content ??
    block.text ??
    "";
  return contentBlocksText(output) ?? output;
}

// pi, dsh and Codex dynamic tools return a list of content blocks, CodeBuddy a
// single one. Keep their text as text: stringified JSON turns line breaks into
// literal "\n".
function contentBlocksText(value) {
  const blocks = Array.isArray(value)
    ? value
    : value && typeof value === "object" && typeof value.type === "string" ? [value] : [];
  if (blocks.length === 0) return null;
  const chunks = [];
  for (const block of blocks) {
    const type = normalizeType(block?.type).replace(/_/g, "");
    if (CONTENT_TEXT_TYPES.has(type) && typeof block.text === "string") chunks.push(block.text);
    else if (CONTENT_IMAGE_TYPES.has(type)) chunks.push("[image]");
    else return null;
  }
  return chunks.join("\n");
}

function toolId(block) {
  return oneLine(
    block?.call_id ||
    block?.callId ||
    block?.callID ||
    block?.tool_call_id ||
    block?.toolCallId ||
    block?.tool_use_id ||
    block?.toolUseId ||
    block?.function_call_id ||
    block?.functionCallId ||
    block?.id ||
    "",
  );
}

function toolStatus(block, kind) {
  if (kind === "call") return "running";
  if (block?.is_error || block?.isError || block?.state?.isError || block?.error || block?.state?.error) return "error";
  const status = oneLine(block?.status || block?.state?.status || "");
  return status || "completed";
}

function setToolInput(part, payload) {
  const input = parseMaybeJson(payload);
  if (input === "" || input == null) return;
  part.tool_input = typeof input === "object" && !Array.isArray(input)
    ? input
    : { value: input };
}

function buildToolPart(block, kind, { toolMaxChars = DEFAULT_TOOL_MAX_CHARS, toolNameById = {} } = {}) {
  const id = toolId(block);
  const name = toolName(block) || (id ? toolNameById[id] : "");
  const payload = toolPayload(block, kind);
  const part = {
    type: "tool",
    tool_id: id || undefined,
    tool_name: name || undefined,
    tool_status: toolStatus(block, kind),
  };
  if (kind === "call") {
    setToolInput(part, payload);
  } else {
    if (block?.state?.input !== undefined) {
      setToolInput(part, block.state.input);
    }
    part.tool_output = stringifyCompact(payload, toolMaxChars);
  }
  return part;
}

function formatToolBlock(block, kind, maxChars) {
  const name = toolName(block);
  const payload = toolPayload(block, kind);
  const body = oneLine(stringifyCompact(payload, maxChars));
  const label = kind === "call" ? "tool-call" : "tool-result";
  return body
    ? `[${label}${name ? ` ${name}` : ""}] ${body}`
    : `[${label}${name ? ` ${name}` : ""}]`;
}

function blockToText(block, options) {
  if (!block) return "";
  if (typeof block === "string") return block;
  if (Array.isArray(block)) return extractTextFromContent(block, options);
  if (typeof block !== "object") return "";

  if (block.item && typeof block.item === "object") {
    const itemText = blockToText(block.item, options);
    if (itemText) return itemText;
  }

  const type = normalizeType(block.type || block.kind || block.role);
  if (TEXT_BLOCK_TYPES.has(type)) return blockText(block);
  if (isToolCallBlock(block)) return formatToolBlock(block, "call", options.toolMaxChars);
  if (isToolResultBlock(block)) return formatToolBlock(block, "result", options.toolMaxChars);
  if (Array.isArray(block.content)) return extractTextFromContent(block.content, options);
  if (!type) return blockText(block);
  return "";
}

export function extractTextFromContent(content, options = {}) {
  const opts = { toolMaxChars: DEFAULT_TOOL_MAX_CHARS, ...options };
  if (!content) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => blockToText(block, opts))
      .filter(Boolean)
      .join("\n\n");
  }
  if (typeof content === "object") {
    return blockToText(content, opts) || stringifyCompact(content, opts.toolMaxChars);
  }
  return "";
}

export function extractTextFromPayload(payload, options = {}) {
  if (!payload || typeof payload !== "object") return "";
  const chunks = [];
  const directType = normalizeType(payload.type || payload.kind || payload.role);
  if (TOOL_RESULT_TYPES.has(directType) || directType === "tool" || TOOL_CALL_TYPES.has(directType)) {
    const direct = blockToText(payload, { toolMaxChars: DEFAULT_TOOL_MAX_CHARS, ...options });
    if (direct) return direct;
  }

  if (payload.message && typeof payload.message === "object") {
    const messageText = extractTextFromPayload(payload.message, options);
    if (messageText) chunks.push(messageText);
  } else if (payload.content !== undefined) {
    const contentText = extractTextFromContent(payload.content, options);
    if (contentText) chunks.push(contentText);
  }

  for (const key of ["tool_calls", "toolCalls", "function_call", "functionCall", "tool_call", "toolCall"]) {
    const value = payload[key];
    if (!value) continue;
    const toolText = extractTextFromContent(value, options);
    if (toolText) chunks.push(toolText);
  }

  if (chunks.length === 0) {
    const direct = blockToText(payload, { toolMaxChars: DEFAULT_TOOL_MAX_CHARS, ...options });
    if (direct) chunks.push(direct);
  }

  return chunks.join("\n\n");
}

/**
 * Map every tool call id in a transcript to the name of the tool it invoked.
 *
 * A result block names the call by id only, so the name has to come from the
 * call that preceded it. Pass the map as `toolNameById` to the extractors.
 */
export function collectToolNamesByIdFromEntries(entries) {
  const map = {};
  for (const entry of entries || []) {
    const payload = entry?.payload && typeof entry.payload === "object" ? entry.payload : entry;
    collectToolNamesByIdFromPayload(payload, map);
  }
  return map;
}

function collectToolNamesByIdFromPayload(payload, out) {
  if (!payload || typeof payload !== "object") return;
  if (payload.message && typeof payload.message === "object") {
    collectToolNamesByIdFromPayload(payload.message, out);
  }
  const candidates = [];
  if (Array.isArray(payload.content)) candidates.push(...payload.content);
  for (const key of ["tool_calls", "toolCalls", "function_call", "functionCall", "tool_call", "toolCall"]) {
    const value = payload[key];
    if (!value) continue;
    if (Array.isArray(value)) candidates.push(...value);
    else candidates.push(value);
  }
  if (isToolCallBlock(payload)) candidates.push(payload);
  for (const block of candidates) {
    if (!block || typeof block !== "object") continue;
    if (!isToolCallBlock(block)) continue;
    const id = toolId(block);
    const name = toolName(block);
    if (id && name) out[id] = name;
  }
}

function extractPartsFromContent(content, options = {}) {
  const opts = { toolMaxChars: DEFAULT_TOOL_MAX_CHARS, toolNameById: {}, ...options };
  const parts = [];
  if (!content) return parts;
  if (typeof content === "string") {
    if (content.trim()) parts.push({ type: "text", text: content });
    return parts;
  }
  if (!Array.isArray(content)) {
    const text = blockToText(content, opts);
    if (text.trim()) parts.push({ type: "text", text });
    return parts;
  }
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    if (isToolCallBlock(block)) {
      parts.push(buildToolPart(block, "call", opts));
    } else if (isToolResultBlock(block)) {
      parts.push(buildToolPart(block, "result", opts));
    } else {
      const text = blockToText(block, opts);
      if (text.trim()) parts.push({ type: "text", text });
    }
  }
  return parts;
}

export function extractPartsFromPayload(payload, options = {}) {
  if (!payload || typeof payload !== "object") return [];
  const opts = { toolMaxChars: DEFAULT_TOOL_MAX_CHARS, toolNameById: {}, ...options };
  if (payload.message && typeof payload.message === "object") {
    return extractPartsFromPayload(payload.message, opts);
  }

  const directType = normalizeType(payload.type || payload.kind || payload.role);
  if (TOOL_CALL_TYPES.has(directType) || isToolCallBlock(payload)) {
    return [buildToolPart(payload, "call", opts)];
  }
  if (TOOL_RESULT_TYPES.has(directType) || directType === "tool" || directType === "function") {
    return [buildToolPart(payload, "result", opts)];
  }

  const parts = [];
  if (payload.content !== undefined) {
    parts.push(...extractPartsFromContent(payload.content, opts));
  }
  for (const key of ["tool_calls", "toolCalls", "function_call", "functionCall", "tool_call", "toolCall"]) {
    const value = payload[key];
    if (!value) continue;
    if (Array.isArray(value)) {
      for (const block of value) parts.push(...extractPartsFromPayload(block, opts));
    } else {
      parts.push(...extractPartsFromPayload(value, opts));
    }
  }
  return parts;
}

function substituteValue(value, rules, role) {
  if (typeof value === "string") {
    let current = value;
    for (const rule of rules) {
      if (rule.op !== "s" || (rule.scope && rule.scope !== role)) continue;
      current = current.replace(rule.re, rule.replacement);
    }
    return current;
  }
  if (Array.isArray(value)) return value.map((item) => substituteValue(item, rules, role));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, substituteValue(item, rules, role)]),
    );
  }
  return value;
}

function substituteToolPart(part, rules, role) {
  if (part.type !== "tool") return part;
  const out = { ...part };
  if (part.tool_input !== undefined) out.tool_input = substituteValue(part.tool_input, rules, role);
  if (part.tool_output !== undefined) out.tool_output = substituteValue(part.tool_output, rules, role);
  return out;
}

/**
 * Prune blank text parts and run `cfg.captureFilters` over the ones that remain.
 *
 * One drop verdict is taken per turn, on the aggregate of its text parts; the
 * individual parts are then rewritten with the substitutions only, so a rule can
 * never both keep and drop the same turn. Tool call/result parts get the
 * substitutions too, on their input values and output, so a redaction rule also
 * covers a secret that reached a command or its output; only text parts decide
 * the drop verdict, and a turn dropped on its text takes its tool parts with it.
 */
export function filterCaptureParts(parts, role, cfg = {}) {
  const kept = [];
  for (const part of parts || []) {
    if (!part) continue;
    if (part.type !== "text") {
      kept.push(part);
      continue;
    }
    const text = typeof part.text === "string" ? part.text.trim() : "";
    if (!text) continue;
    kept.push(text === part.text ? part : { ...part, text });
  }

  const blank = { parts: kept, dropped: false };
  const compiled = compileInputFilters(cfg?.captureFilters);
  if (!compiled.rules.length || kept.length === 0) return blank;

  const textParts = kept.filter((part) => part.type === "text");
  if (textParts.length === 0) {
    return { parts: kept.map((part) => substituteToolPart(part, compiled.rules, role)), dropped: false };
  }

  const verdict = applyInputFilters(textParts.map((part) => part.text).join("\n\n"), compiled.rules, {
    role,
  });
  if (verdict.dropped) return { parts: [], dropped: true };

  const out = [];
  for (const part of kept) {
    if (part.type !== "text") {
      out.push(substituteToolPart(part, compiled.rules, role));
      continue;
    }
    const shaped = applyInputFilters(part.text, compiled.rules, { role, substituteOnly: true });
    if (!shaped.text) continue;
    out.push(shaped.text === part.text ? part : { ...part, text: shaped.text });
  }
  return { parts: out, dropped: out.length === 0 };
}

/** Sanitize already-extracted message parts before applying capture rules. */
export function shapeCaptureParts(parts, role, cfg = {}) {
  const sanitized = (parts || []).map((part) => part?.type === "text"
    ? { ...part, text: sanitizeCapturedText(part.text, { role }) }
    : part);
  return filterCaptureParts(sanitized, role, cfg);
}

/**
 * One host message as the session message to send, or null when nothing in it
 * is to be captured. `role` is the host's own role for the message. Text parts
 * lose injected context and go through `captureFilters`; tool calls and results
 * stay structured parts, in the order the host had them, and never decide keep
 * or drop. With `captureAssistantTurns` off only what the user wrote is sent:
 * no assistant message, no tool call and no tool result.
 */
export function buildCaptureMessage(payload, role, cfg = {}, { toolNameById = {} } = {}) {
  const wireRole = normalizeCaptureRole(role);
  if (!wireRole) return null;
  const userOnly = cfg.captureAssistantTurns === false;
  if (userOnly && isAssistantSideCaptureRole(role)) return null;

  const options = { toolMaxChars: cfg.captureToolMaxChars, toolNameById };
  let parts = extractPartsFromPayload(payload, options);
  if (parts.length === 0) {
    // Some hosts supply an array of plain strings: text, but no structured parts.
    const text = extractTextFromPayload(payload, options);
    if (text) parts = [{ type: "text", text }];
  }
  if (userOnly) parts = parts.filter((part) => part?.type === "text");
  const shaped = shapeCaptureParts(parts, wireRole, cfg).parts;
  return shaped.length ? { role: wireRole, parts: shaped } : null;
}

// What `captureMode: "keyword"` looks for in a user's words: an explicit ask to
// remember something, or a statement that reads as a lasting personal fact.
const CAPTURE_TRIGGERS = [
  /\b(remember|memorize|store|save|capture|note|record)\b|记住|保存|记录|记忆/i,
  /preference|prefer|important|decision|decided|always|never/i,
  /偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
];

/** Whether a user's text carries a `captureMode: "keyword"` trigger. */
export function hasCaptureTrigger(text) {
  const sanitized = sanitizeCapturedText(text);
  return CAPTURE_TRIGGERS.some((trigger) => trigger.test(sanitized));
}

/**
 * Rollout entries (`{ payload }` or bare messages) as session messages, one per
 * entry that `buildCaptureMessage` keeps. `text` is the message's text parts
 * joined, so on a user turn it is what the user wrote.
 */
export function extractCaptureTurns(rolloutEntries, cfg = {}) {
  const toolNameById = collectToolNamesByIdFromEntries(rolloutEntries);
  const turns = [];
  for (const entry of rolloutEntries || []) {
    if (!entry || typeof entry !== "object") continue;
    const payload = entry.payload && typeof entry.payload === "object" ? entry.payload : entry;
    const message = payload.message && typeof payload.message === "object" ? payload.message : null;
    const rawRole = message?.role || payload.role || payload.type || payload.kind;
    const built = buildCaptureMessage(payload, rawRole, cfg, { toolNameById });
    if (!built) continue;
    const text = built.parts
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n\n");
    turns.push({ role: built.role, text, parts: built.parts });
  }
  return turns;
}

/**
 * Index of the last turn that came from a human prompt, or -1.
 *
 * `role === "user"` alone is not enough: normalizeCaptureRole() maps tool
 * results onto the user role too, and those carry `tool` parts rather than
 * `text` parts. Used by the post-compact shrink path to find where the current
 * interaction starts.
 */
export function findLastHumanTurnIndex(turns) {
  const list = Array.isArray(turns) ? turns : [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const turn = list[i];
    if (turn?.role !== "user") continue;
    if (turn.parts?.some((part) => part?.type === "text")) return i;
  }
  return -1;
}

// pi spells these toolResult / toolCall, which normalizeType folds to one word.
const TOOL_RESULT_ROLES = new Set(["tool", "tool_result", "toolresult", "function", "function_call_output"]);
const TOOL_CALL_ROLES = new Set(["tool_call", "toolcall", "function_call"]);

export function normalizeCaptureRole(role) {
  const value = normalizeType(role);
  if (value === "user") return "user";
  if (value === "assistant") return "assistant";
  if (TOOL_RESULT_ROLES.has(value)) return "user";
  if (TOOL_CALL_ROLES.has(value)) return "assistant";
  return null;
}

export function isAssistantSideCaptureRole(role) {
  const value = normalizeType(role);
  return value === "assistant" || TOOL_RESULT_ROLES.has(value) || TOOL_CALL_ROLES.has(value);
}

function stripMetadataFences(text) {
  return String(text || "").replace(/```(?:json)?\s*([\s\S]*?)```/gi, (match, body) => {
    const lower = body.toLowerCase();
    let hits = 0;
    for (const key of METADATA_KEYS) {
      const re = new RegExp(`["']?${key}["']?\\s*:`, "i");
      if (re.test(lower)) hits += 1;
    }
    return hits >= 3 ? "" : match;
  });
}

function stripInjectedDigestBlocks(text) {
  const lines = String(text || "").split(/\r?\n/);
  const out = [];
  let skipping = false;
  let skipUntilMcpHint = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (/^OpenViking session archive digest:/i.test(trimmed)) {
      skipping = true;
      skipUntilMcpHint = true;
      continue;
    }
    if (/^OpenViking memory digest:/i.test(trimmed)) {
      skipping = true;
      skipUntilMcpHint = false;
      continue;
    }
    if (skipping) {
      if (skipUntilMcpHint) {
        if (/^More detail: use the OpenViking MCP /i.test(trimmed)) {
          skipping = false;
          skipUntilMcpHint = false;
        }
        continue;
      }
      if (!trimmed) {
        skipping = false;
        continue;
      }
      if (
        /^(?:[-*]\s+|#{1,6}\s+|More detail:|Use OpenViking MCP|Latest committed archive|Resume continuity|viking:\/\/)/i.test(trimmed) ||
        /^\s{2,}\S/.test(line)
      ) {
        continue;
      }
      skipping = false;
    }
    out.push(line);
  }

  return out.join("\n");
}

// The usage footer a harness asks the model to append to its answer: a summary
// line ("OpenViking · 2 sources · 1 read"), optionally under a rule, and the
// detail lines that follow it up to the next blank line.
const USAGE_FOOTER_RE = /(?:^|\n)(?:[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*\n+)?[ \t>*_`]*OpenViking · \d+ sources?\b[^\n]*(?:\n[ \t]*\S[^\n]*)*/g;

/**
 * Drop everything in a turn that the conversation did not put there.
 *
 * Recall injects a context block into the prompt, and the host adds notes of
 * its own; captured back unchanged, this turn's injection becomes next turn's
 * memory and the loop feeds on itself. Formatting the conversation did author
 * — newlines, code fences — survives. The usage footer is stripped only from
 * an assistant's text (`role: "assistant"`); a user may paste one.
 */
export function sanitizeCapturedText(text, { role } = {}) {
  let value = String(text || "");
  value = value
    .replace(/\u0000/g, "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi, " ")
    .replace(/<relevant-memor(?:y|ies)\b[^>]*>[\s\S]*?<\/relevant-memor(?:y|ies)>/gi, " ")
    // Claude Code wraps its own out-of-band notes to the model in these two
    // shapes. They are the host talking to itself, not the conversation.
    .replace(/<system-reminder\b[^>]*>[\s\S]*?<\/system-reminder>/gi, " ")
    .replace(/^[ \t]*\[Subagent Context\][^\n]*$/gim, " ")
    .replace(/^\s*Sender\s*\([^)]+\)\s*```[\s\S]*?```\s*/gim, " ")
    .replace(/^\s*Conversation (?:metadata|info):\s*```[\s\S]*?```\s*/gim, " ")
    .replace(/^[ \t]*\[\d{4}-\d{2}-\d{2}[T ][-+0-9:.Z]{5,35}\][ \t]*/gm, "")
    .replace(/^\s*\d{10,13}\s+/gm, "");
  value = stripMetadataFences(value);
  value = stripInjectedDigestBlocks(value);
  value = value.replace(/^\s*More detail: use the OpenViking MCP .*$/gim, " ");
  if (role === "assistant") value = value.replace(USAGE_FOOTER_RE, "");
  return value
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * Is capture on for this config?
 *
 * The switch has four spellings in the wild: a boolean `autoCapture`, opencode's
 * `{ enabled }` object, dsh and pi's `syncTurns`, and the global `enabled`
 * that turns the whole plugin off. Reading it here rather than in each loader
 * is what keeps it from drifting a fifth time — and any spelling that says off
 * wins, so a config that disables capture under an older name still disables it.
 */
export function isCaptureEnabled(cfg = {}) {
  for (const value of [cfg.enabled, cfg.autoCapture, cfg.capture, cfg.syncTurns]) {
    if (value === false) return false;
    if (value && typeof value === "object" && !Array.isArray(value) && value.enabled === false) return false;
  }
  return true;
}
