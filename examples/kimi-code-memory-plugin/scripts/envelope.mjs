/**
 * The one module in this adapter that writes stdout.
 *
 * Kimi Code reads a hook's answer differently per event: `UserPromptSubmit`
 * takes plain text (or a structured `message`) and wraps it in its own
 * `<hook_result>` part, `PreToolUse` takes `hookSpecificOutput`, and
 * `SessionStart`, `Stop`, `PreCompact` and `SessionEnd` read nothing at all.
 * Everything else — logs, diagnostics, a failure — goes to stderr, because a
 * stray line on stdout would be parsed as an injection or a decision.
 *
 * Every function here is a no-op for an event whose output the host discards.
 * The entries call them anyway: one place deciding "does this event speak?"
 * is cheaper to audit than six call sites remembering the table.
 */

import { denyHookSpecificOutput } from "./shared/uri-guard.mjs";

/** The events whose stdout the host consumes. */
const SPEAKING_EVENTS = new Set(["UserPromptSubmit", "PreToolUse"]);

/**
 * Remove boundary tags a caller did not write.
 *
 * Recall text is untrusted reference material and may quote a block that looks
 * like this plugin's or the host's own wrapper. Printing it back would close
 * the host's `<hook_result>` early and let the rest of the text escape the
 * wrapper as if the plugin had authored it. This is hygiene, not a security
 * boundary: the recalled text is still untrusted either way.
 */
export function stripWrapperTags(text) {
  return String(text || "")
    .replace(/<\/?hook_result\b[^>]*>/gi, "")
    .replace(/<\/?openviking-context\b[^>]*>/gi, "")
    .trim();
}

/**
 * Write `text` as this hook's answer, if this event has one.
 *
 * `UserPromptSubmit` accepts plain text on stdout; the host wraps it. The other
 * events get nothing, so an accidental call cannot corrupt their protocol.
 */
export function emitText(event, text) {
  if (!SPEAKING_EVENTS.has(event)) return false;
  if (event !== "UserPromptSubmit") return false;
  const body = stripWrapperTags(text);
  if (!body) return false;
  process.stdout.write(`${body}\n`);
  return true;
}

/**
 * Write the PreToolUse deny decision.
 *
 * The envelope comes from the shared `denyHookSpecificOutput()`, so its shape
 * is not maintained here. Only `permissionDecision` and
 * `permissionDecisionReason` are read by this host; `additionalContext` is not a
 * channel it consumes, which is why the notice branch of
 * `preToolUseOutput()` is not used. Exit code stays 0 — the refusal is the
 * answer, not an error.
 */
export function emitDeny(reason) {
  const body = String(reason || "").trim();
  if (!body) return false;
  process.stdout.write(`${JSON.stringify(denyHookSpecificOutput(body))}\n`);
  return true;
}

/** The empty answer: an event that has nothing to say writes nothing at all. */
export function emitNothing() {}

/**
 * A guard around a stage so a thrown error becomes the host's empty answer.
 *
 * A memory plugin must never block the user's turn: a missing config, a broken
 * state file or a network failure ends the hook quietly with exit 0. The one
 * exception is the URI guard's own deny, which is returned rather than thrown.
 */
export async function runSafely(logError, run) {
  try {
    return await run();
  } catch (error) {
    logError("uncaught", error);
    return undefined;
  }
}
