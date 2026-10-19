/**
 * Neutralise the tags this host reserves before retrieved text enters a prompt.
 *
 * The first two entries mirror `wrapContext` in the shared recall core, which
 * is private and only wraps recall blocks — the profile block is injected by
 * this adapter, so it repeats them here. The rest are the host wrappers and
 * out-of-band notes a resource import or an MCP `remember` can still carry
 * into either block. Change the mirrored pair together with the shared one.
 */

const RESERVED_TAGS = [
  [/<\/?openviking-context\b[^>]*>/gi, "openviking context marker"],
  [/<\/?relevant-memor(?:y|ies)\b[^>]*>/gi, "legacy memory wrapper"],
  [/<\/?hook_result\b[^>]*>/gi, "hook result marker"],
  [/<\/?system-reminder\b[^>]*>/gi, "system reminder marker"],
];

// A recall block already carries the shared wrapper's envelope, so only the
// host's own transport and notes are neutralised in it.
const HOST_ONLY_TAGS = RESERVED_TAGS.slice(2);

export function sanitizeHostTags(text) {
  let value = String(text || "");
  for (const [pattern, replacement] of HOST_ONLY_TAGS) {
    value = value.replace(pattern, replacement);
  }
  return value;
}

export function sanitizeRecallText(text) {
  let value = String(text || "");
  for (const [pattern, replacement] of RESERVED_TAGS) {
    value = value.replace(pattern, replacement);
  }
  return value;
}
