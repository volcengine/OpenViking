/**
 * Standalone diagnostic for the capture path (issue #29).
 *
 * Loads a transcript JSON file, runs each turn through the
 * sanitiser + canonicaliser + token-estimate, and prints a verbose
 * report so a user can see WHY a turn was kept or dropped, what
 * sanitised text would actually land in OpenViking, and how the
 * commit-token threshold compares to the resulting payload.
 *
 * Transcript JSON shape:
 *   [
 *     {"role": "user", "text": "..."},
 *     {"role": "assistant", "text": "..."}
 *   ]
 *
 * Vscode-free + host-free. The CLI bin (`--debug-capture=<path>`)
 * is the production caller; tests inject their own readFile fn so
 * fixtures stay in-memory.
 */

import { readFileSync } from "node:fs";
import type { PluginConfig } from "../config.js";
import type { OVTurn } from "../ov-client.js";
import {
  canonicaliseTranscript,
  type CanonicalTurnInput,
} from "../capture/transcript.js";
import { stripInjectedBlocks } from "../capture/sanitize.js";
import { estimateTokens } from "../recall/rank.js";

export interface DebugCaptureArgs {
  /** Path to a JSON transcript file. */
  path: string;
}

export interface DebugCaptureDeps {
  cfg: PluginConfig;
  /** Inject for tests; defaults to fs.readFileSync at runtime. */
  readFile?: (path: string) => string;
  /** Buffered output sink. Defaults to a string accumulator. */
  write?: (chunk: string) => void;
}

export interface DebugCaptureResult {
  exitCode: number;
  /** Captured output (also streamed via `write` when provided). */
  output: string;
}

const SEPARATOR = "─".repeat(60);

interface PerTurnReport {
  index: number;
  role: string;
  rawLen: number;
  sanitisedLen: number;
  trimmedLen: number;
  kept: boolean;
  dropReason?: "empty-after-sanitise" | "filtered-assistant" | "too-long" | "bad-shape";
}

/**
 * Run the diagnostic. Always resolves; never throws.
 */
export async function runDebugCapture(
  args: DebugCaptureArgs,
  deps: DebugCaptureDeps,
): Promise<DebugCaptureResult> {
  const lines: string[] = [];
  let exitCode = 0;

  const write = (chunk: string) => {
    lines.push(chunk);
    deps.write?.(chunk);
  };
  const writeLn = (chunk = "") => write(`${chunk}\n`);

  writeLn("=== OpenViking debug-capture ===");
  writeLn();

  // ----- Config snapshot -----
  writeLn("Configuration");
  writeLn(`  agentId               : ${deps.cfg.agentId}`);
  writeLn(`  baseUrl               : ${deps.cfg.baseUrl}`);
  writeLn(`  autoCapture           : ${deps.cfg.autoCapture}`);
  writeLn(`  captureMode           : ${deps.cfg.captureMode}`);
  writeLn(`  captureAssistantTurns : ${deps.cfg.captureAssistantTurns}`);
  writeLn(`  captureMaxLength      : ${deps.cfg.captureMaxLength}`);
  writeLn(`  commitTokenThreshold  : ${deps.cfg.commitTokenThreshold}`);
  writeLn(`  bypassSession         : ${deps.cfg.bypassSession}`);
  writeLn();

  // ----- Read transcript file -----
  writeLn(`Transcript: ${args.path}`);
  let body: string;
  try {
    const reader = deps.readFile ?? defaultReadFile;
    body = reader(args.path);
  } catch (err) {
    writeLn(`  ERROR: ${err instanceof Error ? err.message : String(err)}`);
    return { exitCode: 2, output: lines.join("") };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch (err) {
    writeLn(`  ERROR: not valid JSON (${err instanceof Error ? err.message : String(err)})`);
    return { exitCode: 2, output: lines.join("") };
  }
  if (!Array.isArray(parsed)) {
    writeLn(`  ERROR: top-level JSON must be an array of {role, text} objects`);
    return { exitCode: 2, output: lines.join("") };
  }
  const turnsIn: CanonicalTurnInput[] = [];
  const reports: PerTurnReport[] = [];
  for (const [i, raw] of (parsed as unknown[]).entries()) {
    const obj = raw as Record<string, unknown> | null;
    if (!obj || typeof obj !== "object") {
      reports.push({ index: i, role: "?", rawLen: 0, sanitisedLen: 0, trimmedLen: 0, kept: false, dropReason: "bad-shape" });
      continue;
    }
    const role = obj["role"];
    const text = obj["text"];
    if (role !== "user" && role !== "assistant") {
      reports.push({ index: i, role: String(role), rawLen: 0, sanitisedLen: 0, trimmedLen: 0, kept: false, dropReason: "bad-shape" });
      continue;
    }
    if (typeof text !== "string") {
      reports.push({ index: i, role, rawLen: 0, sanitisedLen: 0, trimmedLen: 0, kept: false, dropReason: "bad-shape" });
      continue;
    }
    turnsIn.push({ role, text });
    reports.push({ index: i, role, rawLen: text.length, sanitisedLen: 0, trimmedLen: 0, kept: false });
  }

  writeLn(`  parsed turns          : ${turnsIn.length} (input shape OK)`);
  if (reports.some((r) => r.dropReason === "bad-shape")) {
    writeLn(`  bad-shape entries     : ${reports.filter((r) => r.dropReason === "bad-shape").length}`);
  }
  writeLn();

  // ----- Per-turn sanitise + canonicalise -----
  writeLn("Per-turn analysis");
  // We can't inspect canonicaliseTranscript's per-turn drop reasons from
  // the outside, so we replicate the gating predicates here. This keeps
  // the diagnostic report informative without coupling tightly to the
  // shared module's internals.
  const cap = Math.max(0, Math.floor(deps.cfg.captureMaxLength));
  const reportsByOrder: PerTurnReport[] = reports.filter((r) => r.dropReason !== "bad-shape");
  for (let i = 0; i < turnsIn.length; i++) {
    const turn = turnsIn[i]!;
    const r = reportsByOrder[i]!;
    const sanitised = stripInjectedBlocks(turn.text);
    r.sanitisedLen = sanitised.length;
    const trimmed = sanitised.trim();
    r.trimmedLen = trimmed.length;

    if (!deps.cfg.captureAssistantTurns && turn.role === "assistant") {
      r.kept = false;
      r.dropReason = "filtered-assistant";
    } else if (!trimmed) {
      r.kept = false;
      r.dropReason = "empty-after-sanitise";
    } else if (cap > 0 && sanitised.length > cap) {
      r.kept = false;
      r.dropReason = "too-long";
    } else {
      r.kept = true;
    }
  }
  for (const r of reports) {
    const tag = r.kept
      ? "KEEP"
      : `DROP (${r.dropReason ?? "unknown"})`;
    writeLn(
      `  [${String(r.index).padStart(2)}] ${r.role.padEnd(9)} ` +
        `raw=${String(r.rawLen).padStart(5)}  sanitised=${String(r.sanitisedLen).padStart(5)}  trimmed=${String(r.trimmedLen).padStart(5)}  ${tag}`,
    );
  }
  writeLn();

  // ----- Final OVTurn[] payload -----
  const finalTurns = canonicaliseTranscript(turnsIn, {
    captureAssistantTurns: deps.cfg.captureAssistantTurns,
    captureMaxLength: deps.cfg.captureMaxLength,
  });
  writeLn(`Final OVTurn[] payload: ${finalTurns.length} turn(s)`);
  if (finalTurns.length === 0) {
    writeLn(`  (nothing would land in OpenViking)`);
  } else {
    writeLn(`  ${SEPARATOR}`);
    for (const [i, t] of finalTurns.entries()) {
      writeLn(`  [${i}] role=${t.role} length=${t.content?.length ?? 0}`);
      const preview = (t.content ?? "").split("\n").slice(0, 3).join(" | ");
      const truncated = preview.length > 200 ? `${preview.slice(0, 200)}...` : preview;
      writeLn(`       preview: ${truncated}`);
    }
    writeLn(`  ${SEPARATOR}`);
  }
  writeLn();

  // ----- Token estimate vs threshold -----
  const totalTokens = sumTokens(finalTurns);
  const threshold = deps.cfg.commitTokenThreshold;
  const wouldCommit = totalTokens >= threshold;
  writeLn("Commit-queue projection");
  writeLn(`  estimated tokens      : ${totalTokens}`);
  writeLn(`  commitTokenThreshold  : ${threshold}`);
  writeLn(`  would trigger commit  : ${wouldCommit ? "YES" : "no"}`);

  if (deps.cfg.bypassSession) {
    writeLn();
    writeLn("Bypass note");
    writeLn("  bypassSession=true — OVClient.appendTurns + commit short-circuit;");
    writeLn("  the queue accepts the turns but no HTTP traffic is generated.");
  }
  if (!deps.cfg.autoCapture) {
    writeLn();
    writeLn("autoCapture note");
    writeLn("  autoCapture=false — the host's captureChatTurn would short-circuit");
    writeLn("  before this canonicaliser ever runs in production.");
  }

  return { exitCode, output: lines.join("") };
}

function sumTokens(turns: OVTurn[]): number {
  let total = 0;
  for (const t of turns) {
    if (typeof t.content === "string" && t.content.length > 0) {
      total += estimateTokens(t.content);
    }
  }
  return total;
}

function defaultReadFile(path: string): string {
  return readFileSync(path, "utf-8");
}
