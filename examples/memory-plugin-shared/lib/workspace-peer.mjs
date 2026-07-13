export function deriveWorkspacePeerId(cwd) {
  return String(cwd || "").replace(/[^A-Za-z0-9]/g, "-");
}

export function resolveEffectivePeerId({ cfg = {}, cwd = "" } = {}) {
  const explicit = String(cfg.peerId || "").trim();
  if (explicit) return { peerId: explicit, source: "explicit" };

  if (cfg.workspacePeer !== false) {
    const peerId = deriveWorkspacePeerId(cwd);
    if (peerId) return { peerId, source: "workspace" };
  }

  return { peerId: "", source: "none" };
}
