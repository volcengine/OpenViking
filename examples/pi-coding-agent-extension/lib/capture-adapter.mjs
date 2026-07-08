import {
  extractPartsFromPayload,
  extractTextFromPayload,
  shouldCaptureText,
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
    const decision = shouldCaptureText(rawText, role, cfg);
    if (!decision.shouldCapture && parts.length === 0) continue;

    const body = parts.length > 0
      ? { role, parts }
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
