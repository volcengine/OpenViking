/**
 * VS Code adapter (Local agent hooks, the `chat.useHooks` feature).
 *
 * The Local hooks reference (code.visualstudio.com/docs/agents/reference/hooks-reference)
 * spells the events PascalCase and reads a flat
 * `{hooks: {Event: [{type: "command", command, timeout}]}}` file from
 * `~/.copilot/hooks/*.json` (user scope) or `.github/hooks/*.json` (workspace).
 *
 * Two documented limits shape the adapter:
 * - UserPromptSubmit uses the common output format only, so recall has no
 *   channel to land in and the dispatcher skips the fetch entirely
 *   (`skipsPromptRecall`); the profile rides SessionStart, which VS Code fires
 *   on the first prompt of a session and which does take `additionalContext`.
 * - Stop carries no assistant response and the transcript format is declared
 *   unstable, so capture holds the user turn only (see vscode-turns.mjs).
 * VS Code also declares no session-end hook, which is why every capture
 * commits right away: a Stop may be the last event this session ever sends.
 */

import {
  addAgentMessages,
  commitAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { filterCaptureTurns } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { preToolUseOutput } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { buildVscodeTurns, cleanVscodeText } from "./vscode-turns.mjs";

export const vscode = {
  prefix: "vscode-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  skipsPromptRecall: true,
  requestBudgets: { SessionStart: 25_000, Stop: 25_000, PreCompact: 25_000 },
  stages: { SessionStart: "start", UserPromptSubmit: "prompt", Stop: "capture", PreCompact: "capture" },
  envelope(event, block) {
    // UserPromptSubmit and the capture events accept the common output only;
    // writing nothing is the documented pass-through ("continue" defaults on).
    if (event !== "SessionStart" || !block) return null;
    return {
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: block,
      },
    };
  },
  // The template names no matcher, so every tool call reaches the guard; it
  // answers in the documented PreToolUse permissionDecision shape and lets
  // anything without a path straight through.
  guard: (input) => preToolUseOutput(input),
  prompt: (input) => cleanVscodeText(input.prompt),
  async capture(ctx, state) {
    const hashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
    const turnKey = state.pendingPrompt?.at || state.lastTurnKey || state.promptHash || "unknown-turn";
    const toSend = [];
    for (const turn of buildVscodeTurns(ctx.input, state)) {
      // Hash the raw turn, not the filtered text, so raising
      // captureMaxLength never resends one the server already holds
      // in truncated form.
      const hash = stableHash(turnKey, turn.role, turn.content);
      if (hashes.has(hash)) continue;
      const { kept, dropped } = filterCaptureTurns([turn], ctx.cfg);
      if (!kept.length) {
        ctx.log("capture_skip", dropped[0]);
        hashes.add(hash);
        continue;
      }
      toSend.push({ hash, turn: kept[0] });
    }
    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, toSend.map((item) => item.turn), ctx.peerId);
    const captured = result.sent + result.queued;
    for (const item of toSend.slice(0, captured)) hashes.add(item.hash);
    let nextCount = Number(state.capturedSinceCommit || 0) + captured;
    if (captured > 0) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok) nextCount = 0;
    }
    return {
      ...state,
      capturedHashes: [...hashes].slice(-1000),
      capturedSinceCommit: nextCount,
      pendingPrompt: null,
      lastTurnKey: turnKey,
    };
  },
};
