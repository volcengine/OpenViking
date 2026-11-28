/**
 * TRAE hands its hooks no transcript: the prompt arrives in UserPromptSubmit
 * (kept as a note) and the answer in Stop's `last_assistant_message` /
 * `text_content`. A turn is keyed by the time of the prompt it belongs to, and
 * a sent turn by `stableHash(turnKey, role, content)`, so a Stop that runs
 * twice sends once while a later identical turn is sent again.
 */

import { stableHash } from "../../lib/agent-hook-runtime.mjs";

const KEEP = 1000;

export function cleanTraeText(value) {
  return String(value || "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi, "")
    .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, "")
    .trim();
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ cursor = {}, notes = [], payload = {} }) {
  const sent = Array.isArray(cursor.capturedHashes) ? cursor.capturedHashes : [];
  const prompts = notes.filter((note) => typeof note.prompt === "string");
  // State written before notes existed keeps the uncaptured prompt here.
  if (cursor.pendingPrompt?.prompt) prompts.unshift({ prompt: cursor.pendingPrompt.prompt, at: cursor.pendingPrompt.at });
  const lastKey = prompts.at(-1)?.at ?? cursor.lastTurnKey ?? cursor.promptHash ?? "unknown-turn";

  const candidates = prompts.map((note) => ({ key: note.at, role: "user", content: cleanTraeText(note.prompt) }));
  if (prompts.length === 0) candidates.push({ key: lastKey, role: "user", content: cleanTraeText(payload.prompt) });
  candidates.push({
    key: lastKey,
    role: "assistant",
    content: cleanTraeText(payload.last_assistant_message || payload.text_content),
  });
  const pending = candidates
    .filter((turn) => turn.content)
    .map((turn) => ({ ...turn, hash: stableHash(turn.key, turn.role, turn.content) }))
    .filter((turn) => !sent.includes(turn.hash));

  return {
    turns: pending.map(({ role, content }) => ({ role, content })),
    cursorAfter: (count) => ({
      capturedHashes: [...sent, ...pending.slice(0, count).map((turn) => turn.hash)].slice(-KEEP),
      lastTurnKey: lastKey,
    }),
  };
}
