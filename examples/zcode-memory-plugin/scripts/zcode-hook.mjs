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
import { maybeDetach, readHookStdin } from "./shared/async-writer.mjs";
import { applyZcodeCaptureResult, buildZcodeCapturePlan } from "./zcode-capture.mjs";
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

let input = {};
let nativeSessionId = "";
let sessionId = "";
let cwd = "";
let fetchJSON;

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
          : await recallForPrompt(fetchJSON, cfg, prompt, cwd, log, { sessionId }).catch((error) => {
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
      const plan = buildZcodeCapturePlan(buildZcodeTurns(input, state), state);

      // Fail-closed: if no turns and no dedup keys, skip silently (not an error —
      // could be a Stop with no new content, or a race with UserPromptSubmit).
      if (plan.toSend.length === 0) return;

      const result = await addAgentMessages(fetchJSON, sessionId, plan.payloads);
      const { captured, ...nextState } = applyZcodeCaptureResult(state, plan, result);
      let nextCount = Number(state.capturedSinceCommit || 0) + captured;
      if (captured > 0) {
        const committed = await commitAgentSession(fetchJSON, sessionId, log);
        if (committed.ok) nextCount = 0;
      }
      await writeHookState("zcode", nativeSessionId, {
        ...nextState,
        capturedSinceCommit: nextCount,
      });
    });
    // Stop: no output needed (pass-through)
    return;
  }

  // Unknown event: pass-through silently
}

async function run() {
  if (eventName === "stop" && cfg.enabled && cfg.autoCapture) {
    const detached = await maybeDetach(cfg, { approve: () => {} });
    if (detached) return;
  }

  try {
    input = JSON.parse(await readHookStdin());
  } catch {
    input = {};
  }

  // ZCode may pass sessionId in camelCase or snake_case. Ensure both are present
  // so resolveNativeSessionId() finds it via the direct lookup path — avoids the
  // cwd fallback that would collide for two windows in the same directory.
  if (!input.session_id && input.sessionId) input.session_id = input.sessionId;

  nativeSessionId = resolveNativeSessionId(input);
  sessionId = deriveAgentSessionId("zc-", input);
  cwd = resolveAgentCwd(input);
  ({ fetchJSON } = makeAgentFetchJSON(cfg, cwd));
  await main();
}

run().catch((error) => {
  logError("uncaught", error);
  // Pass-through on error — never block the session
});
