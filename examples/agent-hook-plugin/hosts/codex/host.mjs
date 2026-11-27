import { readFileSync } from "node:fs";
import { join } from "node:path";

import { decode } from "./decode.mjs";
import { createCodexCompressor } from "./host-compressor.mjs";
import { getStateDir } from "./state-dir.mjs";
import { answerContext } from "./usage/display.mjs";
import { parseRecall } from "./usage/sources.mjs";

const INJECTS = { SessionStart: "SessionStart", UserPromptSubmit: "UserPromptSubmit" };

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "codex",
  sessionPrefix: "cx-",
  events: {
    SessionStart: "start",
    UserPromptSubmit: "prompt",
    Stop: "turnEnd",
    PreCompact: "compactSoon",
    // Fired since Codex 0.145; an older Codex never commits from the plugin.
    SessionEnd: "end",
  },
  budgets: {
    SessionStart: 65_000,
    // The local compressor child is bounded by recallCompressTimeoutMs.
    UserPromptSubmit: 125_000,
    Stop: 27_000,
    PreCompact: 57_000,
    SessionEnd: 30_000,
  },
  canDetach: true,
  syncEvents: ["PreCompact"],
  // Codex gives SessionEnd 1 s by default and 3 s at most.
  detachEvents: ["SessionEnd"],
  read(event, payload) {
    const { source } = payload;
    return {
      sessionId: typeof payload.session_id === "string" ? payload.session_id.trim() : undefined,
      prompt: event === "UserPromptSubmit" && typeof payload.prompt === "string" ? payload.prompt.trim() : "",
      transcript: typeof payload.transcript_path === "string" ? payload.transcript_path.trim() : "",
      resumed: event === "SessionStart" && (source === "compact" || source === "resume") ? source : "",
    };
  },
  decode,
  // Codex accepts no `decision: "approve"`; a no-op is `{}`.
  emit(event, output) {
    const hookEventName = INJECTS[event];
    return hookEventName && output.context
      ? { hookSpecificOutput: { hookEventName, additionalContext: output.context } }
      : {};
  },
  mcpSession: (meta) => (typeof meta.sessionId === "string" ? meta.sessionId.trim() : ""),
  legacyCursor(nativeSessionId, read) {
    const id = String(read?.sessionId || nativeSessionId).replace(/[^a-zA-Z0-9_-]/g, "_");
    try {
      const state = JSON.parse(readFileSync(join(getStateDir(), `${id}.json`), "utf8"));
      return Number(state?.capturedTurnCount) > 0 ? state : undefined;
    } catch {
      return undefined;
    }
  },
  compress(cfg, log) {
    return createCodexCompressor(cfg, { log, logError: (stage, error) => log(stage, { error: String(error?.message || error) }) });
  },
  // In desktop usage mode the answer is asked to carry a footer of the sources
  // this turn's recall made available.
  present(ctx, context) {
    const turnId = ctx.payload.turn_id;
    if (ctx.event !== "UserPromptSubmit" || !turnId) return context;
    const usage = answerContext({ recalled: parseRecall(context), lookups: [] }, turnId, ctx.cfg);
    return usage ? `${context}\n${usage}` : context;
  },
};
