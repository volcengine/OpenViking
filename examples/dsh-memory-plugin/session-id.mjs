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
