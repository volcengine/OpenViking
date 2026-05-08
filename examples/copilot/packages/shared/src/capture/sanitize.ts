/**
 * Capture-side sanitisation: strip plugin- and host-injected blocks from
 * conversation text BEFORE it gets pushed to OpenViking.
 *
 * Without this, the `<openviking-context>` block we inject as recall this
 * turn would be captured back as part of the user's "message" next turn,
 * creating a self-referential pollution loop. The Claude Code plugin's
 * scripts/auto-capture.mjs is the canonical implementation; this is a
 * 1:1 port with two additions:
 *   - `<copilot-context>` (symmetry — covers any host that surfaces a
 *     parallel block in the Copilot world)
 *   - the patterns array is exported so Phase 0 spike findings can
 *     extend it without touching this file's call sites.
 *
 * Two entry points by design:
 *   - `stripInjectedBlocks(text)` — preserves the user's original
 *     whitespace (newlines, code fences). Use this for content that will
 *     be stored back to OV.
 *   - `sanitize(text)` — also collapses whitespace. Use this for
 *     classification (trigger detection, capture-or-skip) where layout
 *     doesn't matter; never store the output back to OV.
 */

const RELEVANT_MEMORIES_BLOCK_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi;
const OPENVIKING_CTX_BLOCK_RE = /<openviking-context>[\s\S]*?<\/openviking-context>/gi;
const COPILOT_CTX_BLOCK_RE = /<copilot-context>[\s\S]*?<\/copilot-context>/gi;
const SYSTEM_REMINDER_BLOCK_RE = /<system-reminder>[\s\S]*?<\/system-reminder>/gi;
const SUBAGENT_CONTEXT_LINE_RE = /^\[Subagent Context\][^\n]*$/gim;
const NUL_RE = /\x00/g;

/**
 * Marker patterns scrubbed from every captured turn. Exported so
 * downstream code can read the catalogue (debug logging, tests) and so
 * Phase 0 spike findings can append new entries without touching the
 * sanitiser's call sites.
 */
export const INJECTED_BLOCK_PATTERNS: readonly RegExp[] = [
  RELEVANT_MEMORIES_BLOCK_RE,
  OPENVIKING_CTX_BLOCK_RE,
  COPILOT_CTX_BLOCK_RE,
  SYSTEM_REMINDER_BLOCK_RE,
  SUBAGENT_CONTEXT_LINE_RE,
  NUL_RE,
];

/**
 * Strip plugin-injected and host-injected blocks without collapsing
 * whitespace. Idempotent: stripping twice produces the same result as
 * stripping once.
 */
export function stripInjectedBlocks(text: string): string {
  if (typeof text !== "string" || text.length === 0) return text ?? "";
  let out = text;
  for (const pattern of INJECTED_BLOCK_PATTERNS) {
    out = out.replace(pattern, "");
  }
  return out;
}

/**
 * Strict sanitiser for classification: strip injected blocks AND collapse
 * any run of whitespace (including newlines) to a single space, then
 * trim. Suitable for trigger-regex matching and capture-or-skip decisions
 * — NOT for storage (use stripInjectedBlocks for that).
 */
export function sanitize(text: string): string {
  return stripInjectedBlocks(text)
    .replace(/\s+/g, " ")
    .trim();
}
