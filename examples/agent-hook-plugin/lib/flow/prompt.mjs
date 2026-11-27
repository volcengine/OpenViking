/**
 * A user prompt: recall for it, and deliver what an earlier event that could
 * not reach the model left waiting (the profile, the session-history note).
 */

import {
  readHookState,
  recallForPrompt,
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "../agent-hook-runtime.mjs";
import { isCaptureEnabled } from "../capture-utils.mjs";
import { appendNote } from "./notes.mjs";
import { deliverPointer } from "./pointer.mjs";
import { profileBlock } from "./start.mjs";

const DUPLICATE_PROMPT_MS = 500;

export async function promptSubmit(ctx) {
  const { host, read, logError } = ctx;
  const prompt = read.prompt || "";
  if (!prompt) return {};
  const context = await withAgentHookLock(host.id, ctx.nativeSessionId, async (stateFile) => {
    const state = await readHookState(host.id, ctx.nativeSessionId);
    const promptHash = stableHash(prompt);
    const now = Date.now();
    const duplicate = read.eventId
      ? state.promptEventId === read.eventId
      : state.promptHash === promptHash && now - Number(state.promptAt || 0) < DUPLICATE_PROMPT_MS;
    if (duplicate) return "";

    const parts = [];
    const firstPrompt = host.facts?.profileAt === "firstPrompt";
    let profileInjected = Boolean(state.profileInjected);
    // A host without a compaction event names its latest compaction here.
    const { compaction } = read;
    let profileStale = Boolean(state.profileStale)
      || (compaction !== undefined && compaction !== state.profileCompaction);
    if (profileStale || (firstPrompt && !profileInjected)) {
      const profile = await profileBlock(ctx);
      if (profile) {
        parts.push(profile);
        profileInjected = true;
        profileStale = false;
      }
    }

    const recallBlock = state.promptHash === promptHash && state.recallBlock
      ? state.recallBlock
      : await recallForPrompt(ctx.fetchJSON, ctx.cfg, prompt, ctx.cwd, ctx.log, { sessionId: ctx.sessionId })
        .catch((error) => {
          logError("recall", error);
          return null;
        });
    if (recallBlock) parts.push(recallBlock);

    const delivered = deliverPointer(ctx, state);
    if (delivered.pointer) parts.push(delivered.pointer);
    if (read.note && isCaptureEnabled(ctx.cfg)) await appendNote(stateFile, read.note, now);

    await writeHookState(host.id, ctx.nativeSessionId, {
      ...delivered.state,
      promptHash,
      promptEventId: read.eventId || "",
      promptAt: now,
      recallBlock,
      profileStale,
      ...(compaction !== undefined ? { profileCompaction: compaction } : {}),
      ...(firstPrompt ? { profileInjected } : {}),
    });
    return parts.join("\n\n");
  });
  return context ? { context } : {};
}
