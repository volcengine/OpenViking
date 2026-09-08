export function deriveWorkspacePeerId(cwd: unknown): string;
export function resolvePluginPeerId(input?: {
  settings?: { peerId?: string };
  configured?: { has(name: string): boolean } | null;
  /** Which layer supplied each knob, as `resolveSettings()` reports it. */
  sources?: Record<string, string>;
  credentials?: { peerId?: string; credentialSource?: string };
  /** A peer the host handed the plugin directly. */
  hostInput?: string;
  env?: Record<string, string | undefined>;
  credentialSource?: string;
}): string;
export function resolveEffectivePeerId(input?: {
  cfg?: { peerId?: string; workspacePeer?: boolean; peerSource?: unknown };
  cwd?: string;
  onWarn?: ((message: string) => void) | null;
}): {
  peerId: string;
  source: "explicit" | "workspace" | "none";
  origin: string;
  /** The pre-git id, when it differs from `peerId`; otherwise empty. */
  legacyPeerId: string;
};
