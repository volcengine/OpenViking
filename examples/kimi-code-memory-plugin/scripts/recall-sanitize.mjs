/**
 * Neutralise the tags this host reserves before retrieved text enters a prompt.
 *
 * The shared wrapper already strips the two legacy memory envelopes; the rest
 * are Kimi's own transport wrappers and out-of-band notes, which a resource
 * import or an MCP `remember` can still carry into a recall block or the user
 * profile. They describe this host, so the list lives here.
 */

const RESERVED_TAGS = [
  [/<\/?openviking-context\b[^>]*>/gi, "openviking context marker"],
  [/<\/?relevant-memor(?:y|ies)\b[^>]*>/gi, "legacy memory wrapper"],
  [/<\/?hook_result\b[^>]*>/gi, "hook result marker"],
  [/<\/?system-reminder\b[^>]*>/gi, "system reminder marker"],
];

export function sanitizeRecallText(text) {
  let value = String(text || "");
  for (const [pattern, replacement] of RESERVED_TAGS) {
    value = value.replace(pattern, replacement);
  }
  return value;
}
