import {
  extractPartsFromPayload,
  extractTextFromPayload,
  sanitizeCapturedText,
  shouldCaptureText,
  truncateCaptureText,
} from "../shared/capture-utils.mjs";

function normalizeRole(role) {
  const value = String(role || "").toLowerCase();
  if (value === "user") return "user";
  if (value === "assistant") return "assistant";
  if (value === "tool" || value === "tool_result") return "user";
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

export function extractBranchCapturePayloads(branch, syncedEntryCount = 0, cfg = {}) {
  const entries = Array.isArray(branch) ? branch : [];
  const previousCount = Math.max(0, Number(syncedEntryCount) || 0);
  const resetWatermark = entries.length < previousCount;
  const start = resetWatermark ? 0 : Math.min(previousCount, entries.length);
  const payloads = [];

  for (const entry of entries.slice(start)) {
    const payload = entryPayload(entry);
    if (!payload) continue;

    const role = normalizeRole(payload.role || payload.type || payload.kind);
    if (!role) continue;
    if (role === "assistant" && cfg.captureAssistantTurns === false) continue;

    const rawText = extractTextFromPayload(payload, { toolMaxChars: cfg.captureToolMaxChars });
    const parts = extractPartsFromPayload(payload, { toolMaxChars: cfg.captureToolMaxChars });
    const decision = cfg.faithfulCapture || cfg.takeoverEnabled
      ? faithfulDecision(rawText, cfg)
      : shouldCaptureText(rawText, role, cfg);
    const structuredParts = parts.filter((part) => part?.type !== "text");
    if (!decision.shouldCapture && structuredParts.length === 0) continue;

    const bodyParts = [
      ...(decision.shouldCapture && decision.text ? [{ type: "text", text: decision.text }] : []),
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
