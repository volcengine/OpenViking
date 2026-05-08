/**
 * Transcript canonicaliser — convert host-specific chat-history payloads
 * into the OVTurn[] shape `OVClient.appendTurns` expects.
 *
 * Pipeline (per turn):
 *   1. sanitize text via stripInjectedBlocks (whitespace-preserving;
 *      output is safe to push back to OV)
 *   2. drop turns whose sanitized text is empty
 *   3. drop assistant turns when captureAssistantTurns=false
 *   4. drop turns whose sanitized text exceeds captureMaxLength
 *
 * Step 4 follows the CC plugin's auto-capture.mjs:shouldCapture
 * semantic — captureMaxLength is a *rejection threshold*, not a
 * truncation cap. Tool I/O inlining can balloon a turn's size easily,
 * and storing a half-truncated message is worse than skipping it. The
 * commit-queue (#10) decides whether to retry such turns elsewhere.
 *
 * Two host adapters wrap the core:
 *   - `fromVSCodeChatHistory` — duck-typed, no `vscode` dependency
 *     (the host pre-extracts `response` to a string from the parts
 *     array; the shared package never imports `vscode`)
 *   - `fromCaptureToolArgs` — for the CLI MCP `openviking_capture`
 *     tool, where the model passes `{user, assistant}` directly
 */

import type { OVTurn } from "../ov-client.js";
import { stripInjectedBlocks } from "./sanitize.js";

export interface TranscriptOptions {
  /** When false, drop assistant turns (user-only capture). */
  captureAssistantTurns: boolean;
  /**
   * Reject turns whose sanitized text exceeds this length (chars).
   * Mirrors `cfg.captureMaxLength`; CC plugin uses it the same way.
   */
  captureMaxLength: number;
}

export interface CanonicalTurnInput {
  role: "user" | "assistant";
  text: string;
}

// ---------------------------------------------------------------------------
// Core canonicaliser
// ---------------------------------------------------------------------------

/**
 * Apply sanitize → drop empty → filter assistant → drop overlong.
 * Preserves input order. Returns a fresh array; never mutates input.
 */
export function canonicaliseTranscript(
  turns: ReadonlyArray<CanonicalTurnInput>,
  opts: TranscriptOptions,
): OVTurn[] {
  const cap = Math.max(0, Math.floor(opts.captureMaxLength));
  const out: OVTurn[] = [];

  for (const turn of turns) {
    if (!opts.captureAssistantTurns && turn.role === "assistant") continue;

    const sanitized = stripInjectedBlocks(turn.text ?? "");
    const trimmed = sanitized.trim();
    if (!trimmed) continue;
    if (cap > 0 && sanitized.length > cap) continue;

    out.push({ role: turn.role, content: sanitized });
  }

  return out;
}

// ---------------------------------------------------------------------------
// VS Code adapter (duck-typed; no `vscode` import)
// ---------------------------------------------------------------------------

/**
 * Minimal shape of `vscode.ChatRequestTurn`. Hosts pass real
 * `ChatRequestTurn` instances; we only consume the fields we need.
 */
export interface VSCodeChatRequestTurnLike {
  readonly prompt: string;
}

/**
 * Minimal shape of `vscode.ChatResponseTurn`. The real type's
 * `response` is a `(ChatResponseMarkdownPart | …)[]`. Hosts must
 * pre-flatten to a string before handing the turn off — the shared
 * package never imports `vscode` types.
 */
export interface VSCodeChatResponseTurnLike {
  readonly response: string;
}

export type VSCodeChatTurnLike = VSCodeChatRequestTurnLike | VSCodeChatResponseTurnLike;

function isResponseTurn(t: VSCodeChatTurnLike): t is VSCodeChatResponseTurnLike {
  return typeof (t as VSCodeChatResponseTurnLike).response === "string";
}

function isRequestTurn(t: VSCodeChatTurnLike): t is VSCodeChatRequestTurnLike {
  return typeof (t as VSCodeChatRequestTurnLike).prompt === "string";
}

/**
 * Convert a VS Code Chat history payload (request + response turns
 * interleaved) into OVTurn[]. Discriminates by structural shape:
 * `prompt: string` → user turn; `response: string` → assistant turn.
 * Anything else is silently skipped.
 */
export function fromVSCodeChatHistory(
  history: ReadonlyArray<VSCodeChatTurnLike>,
  opts: TranscriptOptions,
): OVTurn[] {
  const inputs: CanonicalTurnInput[] = [];
  for (const turn of history) {
    if (isResponseTurn(turn)) {
      inputs.push({ role: "assistant", text: turn.response });
    } else if (isRequestTurn(turn)) {
      inputs.push({ role: "user", text: turn.prompt });
    }
    // else: unrecognised turn shape — skip without erroring (defensive
    // against future VS Code API additions we don't model yet).
  }
  return canonicaliseTranscript(inputs, opts);
}

// ---------------------------------------------------------------------------
// CLI MCP capture-tool adapter
// ---------------------------------------------------------------------------

/**
 * Args the CLI's `openviking_capture` MCP tool receives from the
 * model: a paired user prompt and assistant reply for the turn.
 */
export interface CaptureToolArgs {
  user: string;
  assistant?: string;
}

/**
 * Convert one `{user, assistant}` capture-tool invocation into the
 * OVTurn[] the OVClient.appendTurns wants. When `assistant` is
 * missing or empty, only the user turn is produced.
 */
export function fromCaptureToolArgs(
  args: CaptureToolArgs,
  opts: TranscriptOptions,
): OVTurn[] {
  const inputs: CanonicalTurnInput[] = [];
  if (typeof args.user === "string") {
    inputs.push({ role: "user", text: args.user });
  }
  if (typeof args.assistant === "string") {
    inputs.push({ role: "assistant", text: args.assistant });
  }
  return canonicaliseTranscript(inputs, opts);
}
