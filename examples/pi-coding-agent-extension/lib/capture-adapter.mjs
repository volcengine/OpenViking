import {
  buildCaptureMessage,
} from "../shared/capture-utils.mjs";

// pi spells these roles in camelCase; the shared capture knows them in snake_case.
const PI_ROLES = { toolresult: "tool_result", toolcall: "tool_call" };

function hostRole(role) {
  const value = String(role || "");
  return PI_ROLES[value.toLowerCase()] || value;
}

function entryPayload(entry) {
  if (!entry || typeof entry !== "object") return null;
  if (entry.type === "message" && entry.message && typeof entry.message === "object") {
    return entry.message;
  }
  if (entry.message && typeof entry.message === "object") return entry.message;
  return entry;
}

export function extractBranchCapturePayloads(branch, syncedEntryCount = 0, cfg = {}, syncedEntryId = "") {
  const entries = Array.isArray(branch) ? branch : [];
  const previousCount = Math.max(0, Number(syncedEntryCount) || 0);
  const resetWatermark = entries.length < previousCount
    || Boolean(syncedEntryId && entries[previousCount - 1]?.id !== syncedEntryId);
  const start = resetWatermark ? 0 : Math.min(previousCount, entries.length);
  const payloads = [];

  for (const entry of entries.slice(start)) {
    const payload = entryPayload(entry);
    if (!payload) continue;

    const body = buildCaptureMessage(payload, hostRole(payload.role || payload.type || payload.kind), cfg);
    if (!body) continue;
    if (cfg.peerId) body.peer_id = cfg.peerId;
    payloads.push(body);
  }

  return {
    payloads,
    nextEntryCount: entries.length,
    nextEntryId: entries.at(-1)?.id || "",
    observedEntryCount: entries.length,
    resetWatermark,
  };
}
