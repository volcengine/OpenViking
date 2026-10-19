/**
 * Kimi Code shaping on top of the shared capture model.
 *
 * The shared extractor owns sanitising, filtering and part building, but it
 * knows nothing about this host: the wire stamps a `turn_id` on every payload,
 * Kimi folds hook output into the prompt as tagged blocks, and the manifest's
 * `captureMaxLength` has to reach the parts that are actually shipped, not only
 * the summary text. Those three rules describe Kimi's wire, so they live here.
 */

import { extractCaptureTurns, truncateCaptureText } from "./shared/capture-utils.mjs";

// `notification` keeps its body: a finished background task or a cron firing is
// a real prompt, and for a turn with no user input it is the whole turn. The
// rest carry machine transport (a path, a loaded skill, a shell echo) with
// nothing the conversation authored, so the block goes whole.
const BODY_TAGS = ["notification"];
const BLOCK_TAGS = ["output-file", "skill-loaded", "bash-input", "bash-stdout", "bash-stderr"];
const BODY_RE = new RegExp(`<(${BODY_TAGS.join("|")})\\b[^>]*>([\\s\\S]*?)<\\/\\1>`, "gi");
const BLOCK_RE = new RegExp(`<(${BLOCK_TAGS.join("|")})\\b[^>]*>[\\s\\S]*?<\\/\\1>`, "gi");

/** Drop the host's transport wrappers from one captured text. */
export function stripHostEnvelopes(text) {
  return String(text || "")
    .replace(/<hook_result\b[^>]*>[\s\S]*?<\/hook_result>/gi, " ")
    .replace(BLOCK_RE, " ")
    .replace(BODY_RE, " $2 ");
}

/**
 * Shared capture turns stamped with the wire's turn id and capped the way the
 * server will store them.
 *
 * The turn id is what makes the acknowledgement set stable across hooks, so it
 * is attached per entry rather than derived from the shaped output — the
 * extractor drops entries it cannot shape, and zipping by index would shift
 * every id after the first drop. When the two passes disagree the ids are left
 * off and the dedup keys fall back to the content hash.
 */
export function shapeKimicodeTurns(entries, cfg = {}) {
  const list = entries || [];
  const turns = extractCaptureTurns(list, cfg);
  if (turns.length === 0) return [];

  const maxLength = Number(cfg.captureMaxLength) || 24000;
  const turnIds = [];
  for (const entry of list) {
    const produced = extractCaptureTurns([entry], cfg).length;
    for (let i = 0; i < produced; i += 1) turnIds.push(entry?.payload?.turn_id ?? null);
  }
  const aligned = turnIds.length === turns.length;

  return turns.map((turn, index) => {
    const turnId = aligned ? turnIds[index] : null;
    const shaped = {
      role: turn.role,
      text: truncateCaptureText(stripHostEnvelopes(turn.text), maxLength),
      parts: (turn.parts || []).map((part) => (part?.type === "text"
        ? { ...part, text: truncateCaptureText(stripHostEnvelopes(part.text), maxLength) }
        : part)),
    };
    if (turnId != null && String(turnId) !== "") shaped.turnId = String(turnId);
    return shaped;
  });
}
