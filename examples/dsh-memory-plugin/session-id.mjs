import { createHash } from "node:crypto";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";

// Keep identifiers valid as a single path component on Windows. The `dsh-`
// prefix also prevents reserved device names such as CON and NUL from matching
// the whole component.
const WINDOWS_UNSAFE = /[<>:"/\\|?*\u0000-\u001F]/g;
const WINDOWS_UNSAFE_TRAILING = /[. ]+$/g;

export function deriveDshSessionId(sessionId) {
  const original = deriveHarnessSessionId("dsh-", String(sessionId));
  const portable = original
    .replace(WINDOWS_UNSAFE, "_")
    .replace(WINDOWS_UNSAFE_TRAILING, match => "_".repeat(match.length));
  if (portable === original) return original;

  // Replacement alone is lossy (`im:a` and `im?a` both become `im_a`). Keep a
  // readable stem and add a stable digest whenever normalization was needed.
  const digest = createHash("sha256").update(original).digest("hex").slice(0, 12);
  return `${portable}__${digest}`;
}

// A seeded session (a fork, or a subagent that inherits its parent's turns)
// never captures its inherited prefix, so its record continues in the parent
// chain. Newest first, as session_history takes them.
export function historySessionIds(session, sessions) {
  const ids = [deriveDshSessionId(session.id)];
  let header = session.header;
  while (header?.isSeeded && header.parentSession != null && ids.length < 10) {
    ids.push(deriveDshSessionId(header.parentSession));
    header = sessions?.get?.(header.parentSession)?.header;
  }
  return ids.join(",");
}
