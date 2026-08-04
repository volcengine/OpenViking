#!/usr/bin/env node

/**
 * ZCode hook dispatcher.
 *
 * Mirrors the TRAE adapter pattern: a single entry point branched on
 * OPENVIKING_HOOK_EVENT. Four thin shim scripts set the event env var and
 * import this module.
 *
 * Output contract: ZCode parses hook stdout as strict JSON — any unrecognized
 * key causes the entire output to be silently discarded. Therefore we NEVER
 * emit Claude-Code-isms like { "decision": "approve" }. Instead:
 *   - Context injection: { hookSpecificOutput: { hookEventName, additionalContext } }
 *   - Pass-through: empty output (stdout = "") + exit 0
 *
 * The deny path (PreToolUse) is handled separately by uri-guard.mjs, which
 * emits { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", ... } }.
 */

import {
  addAgentMessages,
  buildAgentProfile,
  commitAgentSession,
  createAgentLogger,
  deriveAgentSessionId,
  loadAgentHookConfig,
  makeAgentFetchJSON,
  readHookInput,
  readHookState,
  recallForPrompt,
  replayAgentPending,
  resolveAgentCwd,
  resolveNativeSessionId,
  shouldBypassAgent,
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "./shared/agent-hook-runtime.mjs";
import { buildZcodeTurns, cleanZcodeText } from "./zcode-turns.mjs";

const eventName = process.env.OPENVIKING_HOOK_EVENT || process.argv[2] || "";
const cfg = loadAgentHookConfig("zcode");
const { log, logError } = createAgentLogger("zcode", eventName, cfg);

/**
 * Emit context injection output for SessionStart / UserPromptSubmit.
 * Uses ONLY ZCode-recognized keys: hookSpecificOutput with hookEventName + additionalContext.
 * No `decision` field — that is a Claude-Code-ism that ZCode's strict schema rejects.
 */
function outputContext(additionalContext, hookEventName) {
  if (!additionalContext) {
    // Pass-through: empty output + implicit exit 0
    return;
  }
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName,
        additionalContext,
      },
    }) + "\n",
  );
}

const input = await readHookInput();

// ZCode may pass sessionId in camelCase or snake_case. Ensure both are present
// so resolveNativeSessionId() finds it via the direct lookup path — avoids the
// cwd fallback that would collide for two windows in the same directory.
if (!input.session_id && input.sessionId) input.session_id = input.sessionId;

const nativeSessionId = resolveNativeSessionId(input);
const sessionId = deriveAgentSessionId("zc-", input);
const cwd = resolveAgentCwd(input);
const { fetchJSON } = makeAgentFetchJSON(cfg, cwd);

async function main() {
  if (!cfg.enabled || shouldBypassAgent(cfg, input)) {
    return;
  }
  let state = await readHookState("zcode", nativeSessionId);

  // --- SessionStart: inject user profile + replay pending ---
  if (eventName === "session-start") {
    const profile = await withAgentHookLock("zcode", nativeSessionId, async () => {
      state = await readHookState("zcode", nativeSessionId);
      const now = Date.now();
      if (now - Number(state.lastSessionStartAt || 0) < 2000) return null;
      state = { ...state, lastSessionStartAt: now };
      await writeHookState("zcode", nativeSessionId, state);
      await replayAgentPending(fetchJSON, log).catch((error) => logError("pending", error));
      return buildAgentProfile(fetchJSON, cfg, cwd).catch((error) => {
        logError("profile", error);
        return null;
      });
    });
    outputContext(
      profile ? `<openviking-context source="session-start">\n${profile}\n</openviking-context>` : "",
      "SessionStart",
    );
    return;
  }

  // --- UserPromptSubmit: recall relevant memories ---
  if (eventName === "user-prompt-submit") {
    const prompt = cleanZcodeText(
      input.prompt || input.user_prompt || input.userMessage || input.user_message || input.message || "",
    );
    if (!prompt) return;
    const recallBlock = await withAgentHookLock("zcode", nativeSessionId, async () => {
      state = await readHookState("zcode", nativeSessionId);
      const promptHash = stableHash(prompt);
      const now = Date.now();
      const promptEventId =
        input.generation_id || input.request_id || input.message_id || input.prompt_id || "";
      const duplicateEvent = promptEventId
        ? state.promptEventId === promptEventId
        : state.promptHash === promptHash && now - Number(state.promptAt || 0) < 500;
      if (duplicateEvent) return null;
      const block =
        state.promptHash === promptHash && state.recallBlock
          ? state.recallBlock
          : await recallForPrompt(fetchJSON, cfg, prompt, cwd, log).catch((error) => {
              logError("recall", error);
              return null;
            });
      await writeHookState("zcode", nativeSessionId, {
        ...state,
        promptHash,
        promptEventId,
        promptAt: now,
        recallBlock: block,
        pendingPrompt: { prompt, hash: promptHash, at: now },
      });
      return block;
    });
    outputContext(recallBlock || "", "UserPromptSubmit");
    return;
  }

  // --- Stop: capture incremental turns + commit ---
  if (eventName === "stop") {
    if (!cfg.autoCapture) return;
    await withAgentHookLock("zcode", nativeSessionId, async () => {
      state = await readHookState("zcode", nativeSessionId);
      const capturedTurnIds = new Set(Array.isArray(state.capturedTurnIds) ? state.capturedTurnIds : []);
      const toSend = [];
      let newLastTurnId = state.lastTurnId || null;

      for (const turn of buildZcodeTurns(input, state)) {
        // Dedup key: turnId + role ensures user and assistant from the same
        // rollout entry (which share a turnId) are treated as distinct turns.
        // For stdin-only turns without turnId, fall back to stableHash.
        const dedupKey = turn.turnId
          ? `${turn.turnId}:${turn.role}`
          : stableHash(turn.role, turn.content);
        if (capturedTurnIds.has(dedupKey)) continue;
        toSend.push({ dedupKey, turn });
        if (turn.turnId) {
          newLastTurnId = turn.turnId;
        }
      }

      // Fail-closed: if no turns and no dedup keys, skip silently (not an error —
      // could be a Stop with no new content, or a race with UserPromptSubmit).
      if (toSend.length === 0) return;

      const result = await addAgentMessages(
        fetchJSON,
        sessionId,
        toSend.map((item) => item.turn),
      );
      const captured = result.sent + result.queued;
      // Only record dedup keys for successfully sent turns
      for (const item of toSend.slice(0, captured)) {
        if (!item.turn.turnId) {
          capturedTurnIds.add(item.dedupKey);
        }
      }
      let nextCount = Number(state.capturedSinceCommit || 0) + captured;
      if (captured > 0) {
        const committed = await commitAgentSession(fetchJSON, sessionId);
        if (committed.ok) nextCount = 0;
      }
      await writeHookState("zcode", nativeSessionId, {
        ...state,
        capturedTurnIds: [...capturedTurnIds].slice(-1000),
        capturedSinceCommit: nextCount,
        pendingPrompt: null,
        lastTurnId: newLastTurnId,
      });
    });
    // Stop: no output needed (pass-through)
    return;
  }

  // Unknown event: pass-through silently
}

main().catch((error) => {
  logError("uncaught", error);
  // Pass-through on error — never block the session
});
