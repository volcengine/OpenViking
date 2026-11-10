export function deriveWorkspacePeerId(cwd: unknown): string;
export function resolveEffectivePeerId(input?: {
  cfg?: { peerId?: string; workspacePeer?: boolean };
  cwd?: string;
}): { peerId: string; source: "explicit" | "workspace" | "none" };
