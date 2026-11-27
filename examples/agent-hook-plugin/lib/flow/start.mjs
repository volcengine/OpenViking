/**
 * Session start: replay what earlier hooks could not send, then open the
 * session with the user's profile and, after a compaction or a resume, the note
 * that points at the session's full record.
 */

import {
  buildAgentProfile,
  readHookState,
  replayAgentPending,
  withAgentHookLock,
  writeHookState,
} from "../agent-hook-runtime.mjs";
import { contextEvent, deferContext, deliverPointer, reachesModel } from "./pointer.mjs";

const DUPLICATE_START_MS = 2000;

export const contextBlock = (source, body) => (
  body ? `<openviking-context source="${source}">\n${body}\n</openviking-context>` : ""
);

export async function profileBlock(ctx) {
  const profile = await buildAgentProfile(ctx.fetchJSON, ctx.cfg, ctx.cwd).catch((error) => {
    ctx.logError("profile", error);
    return null;
  });
  return contextBlock("session-start", profile);
}

export async function sessionStart(ctx) {
  const { host, log, logError } = ctx;
  const context = await withAgentHookLock(host.id, ctx.nativeSessionId, async () => {
    const state = await readHookState(host.id, ctx.nativeSessionId);
    const now = Date.now();
    const reaches = reachesModel(ctx);
    const delivered = reaches ? deliverPointer(ctx, state) : { pointer: "", state: deferContext(ctx, state) };
    let next = delivered.state;
    // A resume and the compaction it runs can start within the window. A start
    // that answers a compaction is the only profile after it, so it never skips.
    const answersCompaction = reaches && contextEvent(ctx) === "compact";
    if (now - Number(state.lastSessionStartAt || 0) < DUPLICATE_START_MS && !answersCompaction) {
      if (next !== state) await writeHookState(host.id, ctx.nativeSessionId, next);
      return delivered.pointer;
    }
    next = { ...next, lastSessionStartAt: now };
    await writeHookState(host.id, ctx.nativeSessionId, next);
    await replayAgentPending(ctx.fetchJSON, log, { limit: host.facts?.replayLimit })
      .catch((error) => logError("pending", error));
    if (!reaches) return "";
    return [await profileBlock(ctx), delivered.pointer].filter(Boolean).join("\n\n");
  });
  return context ? { context } : {};
}
