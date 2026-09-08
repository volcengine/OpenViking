/**
 * Byte caps and well-formedness sanitation for outgoing text parts.
 *
 * The local vectordb's bytes_row format caps string fields at 65535 UTF-8
 * bytes (#2967), and the per-record `fields` JSON blob aggregates several
 * scalars on top of that (#3593, still open). An oversized text part -- e.g.
 * a slash-command/skill prompt expanded into the transcript as a user turn,
 * observed at 248KB in the wild -- therefore produces an addMessage the
 * server accepts but whose derived record can never serialize. The queue
 * consumer re-enqueues the failed task forever, and the retry loop can
 * starve the event loop until the server stops answering.
 *
 * Unpaired surrogates are the second, size-independent member of the same
 * poison class: JSON transcripts can legally carry a lone `\ud83d` escape,
 * and the server's Python utf-8 encode raises "surrogates not allowed" on
 * it. Every string returned from this module is well-formed UTF-16 (safe to
 * utf-8 encode) and byte-capped.
 */
export const TEXT_PART_MAX_BYTES = 16384;

// Per-message budget across all text parts: the 65535-byte record field must
// also hold sibling scalars and JSON overhead, so several maxed parts cannot
// be allowed to fill it (4 x 16384 already exceeds it).
export const TEXT_TOTAL_MAX_BYTES = 49152;

// The truncation marker must fit INSIDE the cap so callers can trust that a
// returned string never exceeds the requested byte budget (for budgets that
// can hold it -- tiny budgets degrade to marker-only, never to a hang).
const MARKER_RESERVE_BYTES = 64;

// Replace unpaired surrogates with U+FFFD (same byte count as the lone
// surrogate's WTF-8 encoding, so byte budgets are unaffected).
function toWellFormedText(s) {
  if (typeof s.toWellFormed === "function") return s.toWellFormed();
  return s.replace(
    /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g,
    "�",
  );
}

export function capTextPartBytes(t, maxBytes = TEXT_PART_MAX_BYTES) {
  const wellFormed = toWellFormedText(t);
  if (Buffer.byteLength(wellFormed, "utf8") <= maxBytes) return wellFormed;
  const budget = Math.max(0, maxBytes - MARKER_RESERVE_BYTES);
  let s = wellFormed.slice(0, budget);
  while (Buffer.byteLength(s, "utf8") > budget) {
    s = s.slice(0, Math.floor(s.length * 0.9));
  }
  // slice() cuts by UTF-16 code units and can split a surrogate pair even in
  // well-formed input; re-sanitize so the truncated string stays encodable.
  s = toWellFormedText(s);
  return s + `\n... [truncated, ${t.length - s.length} more chars]`;
}

// Cap every text part of a message, enforcing both the per-part cap and the
// per-message total. Non-text parts pass through untouched. This is the one
// policy point both capture hooks share -- keep call sites thin so the
// policy cannot drift between them.
export function capTextParts(parts, totalBudget = TEXT_TOTAL_MAX_BYTES) {
  let remaining = totalBudget;
  return parts.map((p) => {
    if (p.type !== "text" || typeof p.text !== "string") return p;
    const capped = capTextPartBytes(
      p.text,
      Math.max(0, Math.min(TEXT_PART_MAX_BYTES, remaining)),
    );
    remaining -= Buffer.byteLength(capped, "utf8");
    return capped === p.text ? p : { ...p, text: capped };
  });
}
