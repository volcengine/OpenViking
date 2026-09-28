import {
  finalAssistantKeepMask,
  isToolTransportRole,
  shapeCapturePayload,
} from "../shared/capture-utils.mjs";

function normalizeRole(role) {
  const value = String(role || "").toLowerCase();
  if (value === "user") return "user";
  if (value === "assistant") return "assistant";
  if (value === "tool" || value === "tool_result" || value === "toolresult") return "user";
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
  const collected = [];

  for (const entry of entries.slice(start)) {
    const payload = entryPayload(entry);
    if (!payload) continue;

    const raw = payload.role || payload.type || payload.kind;
    const role = normalizeRole(raw);
    if (!role) continue;
    if (role === "assistant" && cfg.captureAssistantTurns === false) continue;

    const shaped = shapeCapturePayload(payload, role, cfg, {
      faithful: cfg.faithfulCapture || cfg.takeoverEnabled,
    });
    if (shaped.dropped) continue;
    const structuredParts = shaped.parts.filter((part) => part?.type !== "text");
    if (!shaped.text && structuredParts.length === 0) continue;

    // Tool-only payloads must not also send rendered tool output as text.
    const bodyParts = [
      ...(shaped.parts.some((part) => part?.type === "text") && shaped.text
        ? [{ type: "text", text: shaped.text }]
        : []),
      ...structuredParts,
    ];
    const body = bodyParts.length > 0
      ? { role, parts: bodyParts }
      : { role, content: shaped.text };
    if (cfg.peerId) body.peer_id = cfg.peerId;
    collected.push({ body, isToolTransport: isToolTransportRole(raw) });
  }

  // `captureAssistantFinalOnly` keeps one assistant reply per user turn. Tool
  // entries normalize onto user/assistant, so a group may only be opened by an
  // entry that is not tool transport — otherwise every tool result would split
  // one turn in two.
  if (cfg.captureAssistantFinalOnly === true) {
    const keep = finalAssistantKeepMask(collected.map((item) => ({
      role: item.body.role,
      isToolTransport: item.isToolTransport,
    })));
    collected.forEach((item, i) => {
      if (keep[i]) payloads.push(item.body);
    });
  } else {
    for (const item of collected) payloads.push(item.body);
  }

  return {
    payloads,
    nextEntryCount: entries.length,
    observedEntryCount: entries.length,
    resetWatermark,
  };
}
