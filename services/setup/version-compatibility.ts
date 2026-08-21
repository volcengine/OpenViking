export type VersionCompatibility = "compatible" | "server_too_old" | "server_too_new" | "unknown";

export type VersionCompatibilityRange = {
  min?: string;
  max?: string;
};

export function parseVersionTuple(v: string): number[] | null {
  const cleaned = v.replace(/^v/i, "").split("-")[0];
  const parts = cleaned.split(".").map(Number);
  if (parts.some(isNaN)) return null;
  return parts;
}

export function compareVersions(a: number[], b: number[]): number {
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

export function checkVersionCompatibility(
  serverVersion: string,
  { min, max }: VersionCompatibilityRange,
): VersionCompatibility {
  if (!serverVersion) return "unknown";
  const sv = parseVersionTuple(serverVersion);
  if (!sv) return "unknown";

  if (min) {
    const minV = parseVersionTuple(min);
    if (minV && compareVersions(sv, minV) < 0) return "server_too_old";
  }
  if (max) {
    const maxV = parseVersionTuple(max);
    if (maxV && compareVersions(sv, maxV) > 0) return "server_too_new";
  }
  return "compatible";
}
