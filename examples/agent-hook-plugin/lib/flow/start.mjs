/**
 * Session start: replay what earlier hooks could not send, then open the
 * session with the user's profile and, after a compaction or a resume, the note
 * that points at the session's full record.
 */

import {
  buildAgentProfile,
  getAgentSessionContext,
  readHookState,
  replayAgentPending,
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "../agent-hook-runtime.mjs";
import { truncateToBytes } from "../profile-inject.mjs";
import { contextEvent, deferContext, deliverPointer, reachesModel } from "./pointer.mjs";

const DUPLICATE_START_MS = 2000;

export const contextBlock = (source, body) => (
  body ? `<openviking-context source="${source}">\n${body}\n</openviking-context>` : ""
);

async function profile(ctx, cfg = ctx.cfg) {
  return buildAgentProfile(ctx.fetchJSON, cfg, ctx.cwd).catch((error) => {
    ctx.logError("profile", error);
    return null;
  });
}

export async function profileBlock(ctx) {
  return contextBlock("session-start", await profile(ctx));
}

/** With `resumeArchiveInject`, the session's latest archive overview after a compaction or a resume. */
async function archiveBlock(ctx, maxBytes) {
  if (!ctx.cfg.resumeArchiveInject || !contextEvent(ctx)) return "";
  const context = await getAgentSessionContext(ctx.fetchJSON, ctx.sessionId, ctx.cfg.resumeContextBudget);
  let overview = String(context?.latest_archive_overview || "").trim();
  if (!overview) return "";
  const cut = truncateToBytes(overview, maxBytes);
  if (cut !== overview) {
    overview = `${cut}\nMore detail: read viking://~/sessions/${ctx.sessionId}/history/ with the OpenViking MCP read tool.`;
  }
  return `<session-archive>\n  <archive-overview>${overview}</archive-overview>\n</session-archive>`;
}

const bytes = (text) => Buffer.byteLength(text || "", "utf8");

/**
 * What opens the session: the profile and, where asked for, the archive
 * overview. Under `sessionStartMaxBytes` the archive takes up to half and the
 * profile what is left beside the note. A resumed session still holds the
 * profile it was given, so an unchanged one is not sent again.
 */
async function opening(ctx, pointer, sentHash) {
  const max = Number(ctx.cfg.sessionStartMaxBytes) || 0;
  const archive = await archiveBlock(ctx, Math.floor(max / 2));
  const room = max > 0 ? Math.max(1, max - bytes(archive) - bytes(pointer) - 100) : 0;
  let text = await profile(ctx, { ...ctx.cfg, sessionStartMaxBytes: room });
  const profileHash = text ? stableHash(text) : sentHash;
  if (text && ctx.read.resumed === "resume" && profileHash === sentHash) text = null;
  return { block: contextBlock("session-start", [text, archive].filter(Boolean).join("\n")), profileHash };
}

export async function sessionStart(ctx) {
  const { host, log, logError } = ctx;
  const context = await withAgentHookLock(host.id, ctx.stateKey, async () => {
    const state = await readHookState(host.id, ctx.stateKey);
    const now = Date.now();
    const reaches = reachesModel(ctx);
    const delivered = reaches ? deliverPointer(ctx, state) : { pointer: "", state: deferContext(ctx, state) };
    let next = delivered.state;
    // A resume and the compaction it runs can start within the window. A start
    // that answers a compaction is the only profile after it, so it never skips.
    const answersCompaction = reaches && contextEvent(ctx) === "compact";
    if (now - Number(state.lastSessionStartAt || 0) < DUPLICATE_START_MS && !answersCompaction) {
      if (next !== state) await writeHookState(host.id, ctx.stateKey, next);
      return delivered.pointer;
    }
    next = { ...next, lastSessionStartAt: now };
    await writeHookState(host.id, ctx.stateKey, next);
    await replayAgentPending(ctx.fetchJSON, log, { limit: host.facts?.replayLimit })
      .catch((error) => logError("pending", error));
    if (!reaches) return "";
    const opened = await opening(ctx, delivered.pointer, next.profileHash);
    if (opened.profileHash !== next.profileHash) {
      await writeHookState(host.id, ctx.stateKey, { ...next, profileHash: opened.profileHash });
    }
    return [opened.block, delivered.pointer].filter(Boolean).join("\n\n");
  });
  return context ? { context } : {};
}
