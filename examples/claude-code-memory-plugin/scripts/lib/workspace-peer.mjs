import { readJsonState, writeJsonState } from "./state.mjs";
import { resolveEffectivePeerId } from "../shared/workspace-peer.mjs";

function stateName(sessionId) {
  const safe = String(sessionId || "").replace(/[^a-zA-Z0-9_-]/g, "_");
  return `ws-peer-${safe}.json`;
}

export function getEffectivePeerId(cfg, { sessionId = "", cwd = "" } = {}) {
  if (!sessionId) return resolveEffectivePeerId({ cfg, cwd });

  const name = stateName(sessionId);
  const cached = readJsonState(name);
  if (cached?.peerId && cached?.source) {
    if (String(cfg.peerId || "").trim()) {
      return resolveEffectivePeerId({ cfg, cwd });
    }
    if (cached.source === "workspace" && cfg.workspacePeer !== false) {
      return { peerId: String(cached.peerId), source: "workspace" };
    }
  }

  const resolved = resolveEffectivePeerId({ cfg, cwd });
  if (resolved.source === "workspace") {
    writeJsonState(name, {
      peerId: resolved.peerId,
      source: resolved.source,
      cwd: String(cwd || ""),
    });
  }
  return resolved;
}
