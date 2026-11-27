/**
 * What a session's events carried that its transcript will not: the prompt a
 * host leaves out of its turn-end payload, a tool result only a hook saw. A
 * host's `read` hands a note over, the flow appends it beside the session's
 * state under the session lock, and `decode` gets them all back until a
 * capture has settled every turn it decoded.
 */

import { appendFile, readFile, rm } from "node:fs/promises";

import { parseJsonl } from "./turns.mjs";

const notesFile = (stateFile) => `${stateFile}.notes.jsonl`;

export async function appendNote(stateFile, note, now = Date.now()) {
  await appendFile(notesFile(stateFile), `${JSON.stringify({ ...note, at: now })}\n`, { mode: 0o600 });
}

export async function readNotes(stateFile) {
  try {
    return parseJsonl(await readFile(notesFile(stateFile), "utf8"));
  } catch {
    return [];
  }
}

export async function clearNotes(stateFile) {
  await rm(notesFile(stateFile), { force: true });
}
