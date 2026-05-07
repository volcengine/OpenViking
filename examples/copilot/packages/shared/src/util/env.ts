/**
 * Tiny typed helpers for parsing values that may come from process.env or
 * from a JSON config file. Behaviour matches the Claude Code plugin's
 * scripts/config.mjs so a single config file drives both plugins identically.
 */

export function num(val: unknown, fallback: number): number {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val === "string" && val.trim()) {
    const n = Number(val);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

export function str(val: unknown, fallback: string): string;
export function str(val: unknown, fallback: null): string | null;
export function str(val: unknown, fallback: string | null): string | null {
  if (typeof val === "string" && val.trim()) return val.trim();
  return fallback;
}

export function envBool(name: string): boolean | undefined {
  const v = process.env[name];
  if (v == null || v === "") return undefined;
  const lower = v.trim().toLowerCase();
  if (lower === "0" || lower === "false" || lower === "no") return false;
  if (lower === "1" || lower === "true" || lower === "yes") return true;
  return undefined;
}
