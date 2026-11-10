import { readFile, readdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { conversationExchanges, conversationAnchors } from "./conversation.mjs";
import { withPrivateState } from "./private-state.mjs";

// Only hashes of known rejected exchanges are retained, never reply text.
export function withCaptureDecisions(directory, scope, operation) {
  return withPrivateState(join(directory, "capture", scope, "decisions"), scope, undefined, (state) => { state.revisions ||= {}; return operation(state); }, {
    initial: () => ({ version: 1, rejected: [], revisions: {} }),
    validate: (s) => s?.version === 1 && Array.isArray(s.rejected)
      && s.rejected.every((key) => typeof key === "string" && /^[a-f0-9]{64}$/.test(key))
      && (s.revisions === undefined || (s.revisions && typeof s.revisions === "object" && !Array.isArray(s.revisions)
        && Object.entries(s.revisions).every(([key, revision]) => /^[a-f0-9]{64}$/.test(key) && Number.isSafeInteger(revision) && revision >= 0))),
  });
}

export const captureTurnAnchors = (messages, protocol) => conversationAnchors(messages, {}, protocol);

/** Apply the same exclusions to recovery, final callbacks and direct queuing. */
export function selectCaptureHistory(messages, protocol, rejected = []) {
  const exchanges = conversationExchanges(messages, protocol);
  const anchors = captureTurnAnchors(messages, protocol);
  const blocked = new Set(rejected);
  const kept = [], turnAnchors = [];
  for (const { start, end, auxiliary } of exchanges) {
    if (blocked.has(anchors[start]) || auxiliary) continue;
    kept.push(...messages.slice(start, end));
    turnAnchors.push(anchors[start]);
  }
  return { messages: kept, turnAnchors };
}

export async function rejectCaptureTurn(directory, scope, anchor) {
  await withCaptureDecisions(directory, scope, async (state) => {
    if (!state.rejected.includes(anchor)) state.rejected.push(anchor);
    // Invalidates snapshots already read by a worker, even if a successful
    // retry later resolves this rejection before that worker resumes.
    state.revisions[anchor] = (state.revisions[anchor] || 0) + 1;
    const pending = join(directory, "capture", scope, "pending");
    for (const name of await readdir(pending).catch(() => [])) {
      if (!/^[a-f0-9]{64}\.json$/.test(name)) continue;
      const path = join(pending, name);
      try {
        const snapshot = JSON.parse(await readFile(path, "utf8"));
        if (snapshot.turnAnchors?.includes(anchor)) await rm(path, { force: true });
      } catch { /* Preserve unreadable evidence; still persist the rejection. */ }
    }
  });
}
