import {
  extractPartsFromPayload,
  extractTextFromPayload,
  sanitizeCapturedText,
  truncateCaptureText,
} from "../shared/capture-utils.mjs";
import { flattenContent } from "./text-budget.mjs";

/**
 * pi delivers tool output as its own message whose role is "toolResult".
 * The non-experimental extension does not recognise it, which is why tool
 * output never reached the archive; here the `history` tool promises the
 * archive can be read back, so it has to.
 */
const TOOL_RESULT_ROLES = new Set(["toolresult", "tool_result", "tool"]);

function rawRole(payload) {
  return String(payload?.role ?? payload?.type ?? payload?.kind ?? "").toLowerCase();
}

function normalizeRole(role) {
  const value = String(role || "").toLowerCase();
  if (value === "user") return "user";
  if (value === "assistant") return "assistant";
  // OpenViking sessions have no tool role: tool output is archived as a user
  // turn, which is also how the server's extraction prompts read it.
  if (TOOL_RESULT_ROLES.has(value)) return "user";
  if (value === "tool_call" || value === "toolcall") return "assistant";
  return "";
}

function entryPayload(entry) {
  if (!entry || typeof entry !== "object") return null;
  if (entry.type === "message" && entry.message && typeof entry.message === "object") {
    return entry.message;
  }
  if (entry.message && typeof entry.message === "object") return entry.message;
  return entry;
}

/**
 * Capture is faithful in this extension: what the model said is what the
 * archive holds, because a closed context window can only be recovered from
 * the archive. Only plugin chatter and slash commands are dropped.
 */
function faithfulDecision(rawText, cfg) {
  const sanitized = sanitizeCapturedText(rawText);
  if (!sanitized) return { shouldCapture: false, reason: "empty", text: "" };
  const capped = truncateCaptureText(sanitized, cfg.captureMaxLength || 24000);
  const compact = String(capped || "").replace(/\s+/g, " ").trim();
  if (/^\[openviking-memory\]/i.test(compact)) {
    return { shouldCapture: false, reason: "plugin_status", text: "" };
  }
  if (/^\/[a-z0-9_-]{1,64}\b/i.test(compact)) {
    return { shouldCapture: false, reason: "slash_command", text: "" };
  }
  return { shouldCapture: true, reason: "faithful", text: capped };
}

function toolResultName(payload) {
  const name = payload?.toolName ?? payload?.tool_name ?? payload?.name ?? "";
  const value = String(name || "").trim();
  return value || "unknown";
}

function toolResultText(payload, cfg) {
  const direct = flattenContent(payload) || extractTextFromPayload(payload, {
    toolMaxChars: cfg.captureToolMaxChars,
  });
  const sanitized = sanitizeCapturedText(direct);
  if (!sanitized) return "";
  const body = truncateCaptureText(sanitized, cfg.captureToolMaxChars || 1000000);
  if (!body) return "";
  return `[tool-result ${toolResultName(payload)}] ${body}`;
}

export function extractBranchCapturePayloads(branch, syncedEntryCount = 0, cfg = {}) {
  const entries = Array.isArray(branch) ? branch : [];
  const previousCount = Math.max(0, Number(syncedEntryCount) || 0);
  const resetWatermark = entries.length < previousCount;
  const start = resetWatermark ? 0 : Math.min(previousCount, entries.length);
  const payloads = [];

  for (const entry of entries.slice(start)) {
    const payload = entryPayload(entry);
    if (!payload) continue;

    const raw = rawRole(payload);
    const role = normalizeRole(raw);
    if (!role) continue;

    if (TOOL_RESULT_ROLES.has(raw)) {
      if (cfg.captureToolResults === false) continue;
      const text = toolResultText(payload, cfg);
      if (!text) continue;
      const body = { role, parts: [{ type: "text", text }] };
      if (cfg.peerId) body.peer_id = cfg.peerId;
      payloads.push(body);
      continue;
    }

    if (role === "assistant" && cfg.captureAssistantTurns === false) continue;

    const rawText = extractTextFromPayload(payload, { toolMaxChars: cfg.captureToolMaxChars });
    const parts = extractPartsFromPayload(payload, { toolMaxChars: cfg.captureToolMaxChars });
    const decision = faithfulDecision(rawText, cfg);
    const structuredParts = parts.filter((part) => part?.type !== "text");
    if (!decision.shouldCapture && structuredParts.length === 0) continue;

    // decision.text is derived from rawText, which renders tool I/O as
    // "[tool-result ...]" lines. For a tool-only payload that would resend the
    // same output the tool part already carries, so only keep it when the
    // payload really had text of its own.
    const hasTextPart = parts.some((part) => part?.type === "text");
    const bodyParts = [
      ...(hasTextPart && decision.shouldCapture && decision.text
        ? [{ type: "text", text: decision.text }]
        : []),
      ...structuredParts,
    ];
    const body = bodyParts.length > 0
      ? { role, parts: bodyParts }
      : { role, content: decision.text };
    if (cfg.peerId) body.peer_id = cfg.peerId;
    payloads.push(body);
  }

  return {
    payloads,
    nextEntryCount: entries.length,
    observedEntryCount: entries.length,
    resetWatermark,
  };
}
