/**
 * Helpers for a host's `decode`: reading a JSONL transcript, and resuming a
 * list of decoded turns from the cursor an earlier capture stored.
 */

import { stableHash } from "../agent-hook-runtime.mjs";
import { collectToolNamesByIdFromEntries, normalizeCaptureRole } from "../capture-utils.mjs";

const SEEN_LIMIT = 1000;

/** A JSONL transcript's records; anything else reads as no records. */
export function parseJsonl(text) {
  const records = [];
  for (const line of String(text || "").split("\n")) {
    if (!line.trim()) continue;
    let record;
    try { record = JSON.parse(line); } catch { continue; }
    // A whole-file JSON array parses as one line too; it is not a record.
    if (!record || typeof record !== "object" || Array.isArray(record)) continue;
    records.push(record);
  }
  return records;
}

/**
 * The turns a decoder collected as `{ role, recordIndex, blocks }`, blocks in
 * record order. A turn's id is the index of the record that opened it; one with
 * tool blocks carries them as `payload` for the part builder.
 */
export function turnsFromBlocks(collected) {
  const turns = [];
  for (const { role, recordIndex, blocks } of collected) {
    if (blocks.length === 0) continue;
    const turn = {
      role: normalizeCaptureRole(role),
      turnId: String(recordIndex),
      content: blocks.filter((block) => block.type === "text").map((block) => block.text).join("\n\n"),
    };
    if (blocks.some((block) => block.type !== "text")) turn.payload = { role: turn.role, content: blocks };
    turns.push(turn);
  }
  return turns;
}

export function turnKey(turn) {
  return turn.turnId ? `${turn.turnId}:${turn.role}` : stableHash(turn.role, turn.content);
}

/**
 * What a `decode` returns for a transcript whose turns can be told apart: the
 * turns of `all` that `cursor` has not acknowledged, and the cursor to store
 * once the first `count` of them are settled.
 *
 * The cursor is `{ lastTurnId, capturedTurnIds }`. `lastTurnId` is where the
 * next read starts; it only moves past an id once every turn carrying that id
 * is acknowledged. `capturedTurnIds` holds the recent keys, so a transcript the
 * host rewrote, where `lastTurnId` is gone, does not send its turns again.
 */
export function resumeTurns(all, cursor, { key = turnKey } = {}) {
  const known = new Set(Array.isArray(cursor?.capturedTurnIds) ? cursor.capturedTurnIds : []);
  const last = cursor?.lastTurnId ?? null;
  const at = last === null || last === "" ? -1 : all.findLastIndex((turn) => turn.turnId === String(last));
  const tail = all.slice(at + 1).map((turn) => ({ turn, key: key(turn) }));
  const fresh = [];
  const taken = new Set();
  for (const item of tail) {
    if (known.has(item.key) || taken.has(item.key)) continue;
    taken.add(item.key);
    fresh.push(item);
  }
  return {
    turns: fresh.map((item) => item.turn),
    // Named over every turn: a result may answer a call in a turn already captured.
    toolNames: collectToolNamesByIdFromEntries(all),
    cursorAfter(count) {
      const acknowledged = new Set(known);
      for (const item of fresh.slice(0, count)) acknowledged.add(item.key);
      let lastTurnId = last;
      let open = null;
      for (const item of tail) {
        const id = item.turn.turnId || null;
        if (!id) continue;
        if (id !== open) {
          if (open) lastTurnId = open;
          open = id;
        }
        if (!acknowledged.has(item.key)) {
          open = null;
          break;
        }
      }
      if (open) lastTurnId = open;
      return { lastTurnId, capturedTurnIds: [...acknowledged].slice(-SEEN_LIMIT) };
    },
  };
}
