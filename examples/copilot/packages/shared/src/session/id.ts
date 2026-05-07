/**
 * OpenViking session-id derivation for the Copilot plugins.
 *
 * Mirrors the Claude Code plugin's `cc-<sha256(cc_session_id)>` pattern but
 * scoped to the Copilot side: every OpenViking session id is
 *
 *     cp-<sha256(host + ':' + hostSessionId)>
 *
 * - The `cp-` prefix lets the OpenViking server tell Copilot sessions apart
 *   from Claude Code (`cc-`) sessions at a glance, without having to
 *   inspect the agent header.
 * - `host` is the Copilot host identifier (`"copilot-vscode"` or
 *   `"copilot-cli"`). Different hosts hash differently even when they share
 *   the same upstream session id, so the VS Code extension and the CLI
 *   plugin running in parallel never collide on a single OpenViking
 *   session.
 * - `hostSessionId` is whatever the host treats as its conversation id.
 *   When a host doesn't expose one (e.g. some CLI invocations), callers
 *   should pass a stable digest of `cwd + start-time` instead — the
 *   shared package doesn't dictate how that's computed.
 */

import { createHash } from "node:crypto";

/** Constant prefix on every Copilot-side OpenViking session id. */
export const SESSION_ID_PREFIX = "cp-" as const;

/**
 * Build the OpenViking session id for a Copilot host conversation.
 *
 * Pure + deterministic: the same `(host, hostSessionId)` pair always
 * produces the same id, across processes and across machines.
 */
export function deriveSessionId(host: string, hostSessionId: string): string {
  const digest = createHash("sha256")
    .update(`${host}:${hostSessionId}`)
    .digest("hex");
  return `${SESSION_ID_PREFIX}${digest}`;
}
