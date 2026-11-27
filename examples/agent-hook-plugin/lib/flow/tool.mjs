/**
 * A tool call before it runs. `host.tool` says what the call is; the decision
 * is made here: a file tool pointed at a viking:// URI is denied, a shell
 * command that carries one gets a notice, and a session_history call that
 * names no session is given this one's.
 *
 * Nothing here loads the config or talks to the server: the hook that runs
 * before every tool call is a function of its payload.
 */

import { resolveNativeSessionId } from "../agent-hook-runtime.mjs";
import { deriveHarnessSessionId } from "../session-model.mjs";
import { evaluateUriGuard, evaluateUriNotice } from "../uri-guard.mjs";

const NO_FILES = { text: () => null };

/** The host's own id for the session an event belongs to, safe as a file name. */
export function nativeSessionId(host, event, payload, read = host.read(event, payload, NO_FILES)) {
  return resolveNativeSessionId(read?.sessionId ? { session_id: read.sessionId } : payload);
}

/** The OpenViking session a host session, or a child of it, is captured into. */
export function ovSessionId(host, nativeId, child = "") {
  return deriveHarnessSessionId(host.sessionPrefix, nativeId, child);
}

/** The same for an event: a host with a derivation of its own names the session in `read.session`. */
export function eventSessionId(host, read, nativeId) {
  return read?.session || ovSessionId(host, nativeId, String(read?.child || ""));
}

/** `session_history` arguments with this session filled in, or null when the model named one. */
function withHistorySession(args, session) {
  if (!args || typeof args !== "object" || Array.isArray(args)) return null;
  const given = args.session;
  if (given != null && !(typeof given === "string" && !given.trim())) return null;
  return { ...args, session };
}

export function toolCall(host, payload, event = "") {
  const call = host.tool?.(payload);
  if (!call) return {};
  if (call.history !== undefined) {
    const read = host.read(event, payload, NO_FILES);
    const session = eventSessionId(host, read, nativeSessionId(host, event, payload, read));
    const input = withHistorySession(call.history, session);
    return input ? { input } : {};
  }
  const options = host.toolHints ? { hints: host.toolHints } : {};
  const denied = evaluateUriGuard(call.name, call.input, options);
  if (denied) return { deny: denied.reason };
  const notice = evaluateUriNotice(call.name, call.input, options);
  return notice ? { notice: notice.reason } : {};
}
