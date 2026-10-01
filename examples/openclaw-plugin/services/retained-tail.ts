// Recent messages kept verbatim after an auto-commit archives the whole session.
// Module scope on purpose: the host builds a new context engine per turn, so
// the tail must outlive any single engine instance.
import type { OVMessage } from "../client.js";

const MAX_RETAINED_SESSIONS = 1024;
const retainedTails = new Map<string, OVMessage[]>();

/**
 * Prepend the part of `tail` that is older than `messages`. The tail is cut at
 * its first message that also appears in `messages`: the server may drop
 * middle messages from a budgeted context, and putting them back per id would
 * place them ahead of messages the server kept.
 */
export function mergeTail<T extends { id: string }>(tail: T[], messages: T[]): T[] {
  const ids = new Set(messages.map((m) => m.id));
  const overlap = tail.findIndex((m) => ids.has(m.id));
  return [...(overlap === -1 ? tail : tail.slice(0, overlap)), ...messages];
}

export function getRetainedTail(ovSessionId: string): OVMessage[] | undefined {
  return retainedTails.get(ovSessionId);
}

export function setRetainedTail(ovSessionId: string, tail: OVMessage[]): void {
  retainedTails.delete(ovSessionId);
  retainedTails.set(ovSessionId, tail);
  if (retainedTails.size > MAX_RETAINED_SESSIONS) {
    retainedTails.delete(retainedTails.keys().next().value as string);
  }
}

export function deleteRetainedTail(ovSessionId: string): void {
  retainedTails.delete(ovSessionId);
}
