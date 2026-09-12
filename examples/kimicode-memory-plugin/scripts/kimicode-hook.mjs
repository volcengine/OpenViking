#!/usr/bin/env node

/**
 * Kimi Code CLI hook dispatcher.
 *
 * Thin shims pass the event as argv[2] (or OPENVIKING_HOOK_EVENT). Output
 * contracts differ from ZCode and must not be mixed:
 *
 *   UserPromptSubmit — stdout text is appended to context (plain text, not JSON).
 *   SessionStart / SessionEnd / PreCompact / Interrupt — observation-only;
 *     return values are ignored. Use them for replay / capture only.
 *   Stop — blockable; we always pass through (empty stdout, exit 0).
 *   PreToolUse deny is handled by uri-guard.mjs.
 *
 * Fail-open: any exception exits 0 with empty stdout so Kimi Code never stalls.
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
import {
  applyKimicodeCaptureResult,
  buildKimicodeCapturePlan,
} from "./kimicode-capture.mjs";
import { buildKimicodeTurns, cleanKimicodeText } from "./kimicode-turns.mjs";

const HARNESS = "kimicode";
// maybeDetach respawns argv[1] only. Copy the event into env so the
// worker still knows this is stop/session-end/pre-compact.
if (!process.env.OPENVIKING_HOOK_EVENT && process.argv[2]) {
  process.env.OPENVIKING_HOOK_EVENT = process.argv[2];
}
const eventName = process.env.OPENVIKING_HOOK_EVENT || "";
const cfg = loadAgentHookConfig(HARNESS);
const { log, logError } = createAgentLogger(HARNESS, eventName, cfg);

const CAPTURE_EVENTS = new Set(["stop", "pre-compact", "session-end", "interrupt"]);
const DETACH_EVENTS = new Set(["stop", "pre-compact", "session-end"]);

function outputPlainContext(text) {
  if (!text) return;
  process.stdout.write(`${text}\n`);
}

let input = {};
let nativeSessionId = "";
let sessionId = "";
let cwd = "";
let fetchJSON;

async function captureAndCommit() {
  if (!cfg.autoCapture) return;
  await withAgentHookLock(HARNESS, nativeSessionId, async () => {
    let state = await readHookState(HARNESS, nativeSessionId);
    const plan = buildKimicodeCapturePlan(buildKimicodeTurns(input, state), state, cfg);
    if (plan.toSend.length === 0) return;

    const result = await addAgentMessages(fetchJSON, sessionId, plan.payloads);
    const { captured, ...nextState } = applyKimicodeCaptureResult(state, plan, result);
    let nextCount = Number(state.capturedSinceCommit || 0) + captured;
    if (captured > 0) {
      const committed = await commitAgentSession(fetchJSON, sessionId, log);
      if (committed.ok) nextCount = 0;
    }
    await writeHookState(HARNESS, nativeSessionId, {
      ...nextState,
      capturedSinceCommit: nextCount,
    });
  });
}

async function main() {
  if (!cfg.enabled || shouldBypassAgent(cfg, input)) {
    return;
  }
  let state = await readHookState(HARNESS, nativeSessionId);

  if (eventName === "session-start") {
    await withAgentHookLock(HARNESS, nativeSessionId, async () => {
      state = await readHookState(HARNESS, nativeSessionId);
      const now = Date.now();
      if (now - Number(state.lastSessionStartAt || 0) < 2000) return;
      await writeHookState(HARNESS, nativeSessionId, {
        ...state,
        lastSessionStartAt: now,
        profileInjected: false,
      });
      await replayAgentPending(fetchJSON, log).catch((error) => logError("pending", error));
    });
    return;
  }

  if (eventName === "user-prompt-submit") {
    const prompt = cleanKimicodeText(
      input.prompt ||
        input.user_prompt ||
        input.user_message ||
        input.message ||
        textFromPromptInput(input) ||
        "",
    );
    if (!prompt) return;
    const block = await withAgentHookLock(HARNESS, nativeSessionId, async () => {
      state = await readHookState(HARNESS, nativeSessionId);
      const promptHash = stableHash(prompt);
      const now = Date.now();
      const promptEventId = input.prompt_id || input.request_id || input.message_id || "";
      const duplicateEvent = promptEventId
        ? state.promptEventId === promptEventId
        : state.promptHash === promptHash && now - Number(state.promptAt || 0) < 500;
      if (duplicateEvent) return null;

      const recallBlock =
        state.promptHash === promptHash && state.recallBlock
          ? state.recallBlock
          : await recallForPrompt(fetchJSON, cfg, prompt, cwd, log, { sessionId }).catch(
              (error) => {
                logError("recall", error);
                return null;
              },
            );

      let profileBlock = "";
      if (!state.profileInjected) {
        profileBlock = await buildAgentProfile(fetchJSON, cfg, cwd).catch((error) => {
          logError("profile", error);
          return null;
        });
      }

      const parts = [];
      if (profileBlock) {
        parts.push(
          `<openviking-context source="session-start">\n${profileBlock}\n</openviking-context>`,
        );
      }
      if (recallBlock) parts.push(recallBlock);

      await writeHookState(HARNESS, nativeSessionId, {
        ...state,
        promptHash,
        promptEventId,
        promptAt: now,
        recallBlock,
        profileInjected: Boolean(state.profileInjected || profileBlock),
        pendingPrompt: { prompt, hash: promptHash, at: now },
      });
      return parts.join("\n\n");
    });
    outputPlainContext(block || "");
    return;
  }

  if (CAPTURE_EVENTS.has(eventName)) {
    await captureAndCommit();
  }
}

function textFromPromptInput(payload) {
  const inputField = payload.input;
  if (typeof inputField === "string") return inputField;
  if (Array.isArray(inputField)) {
    return inputField
      .map((part) => (typeof part === "string" ? part : part?.text || ""))
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

async function run() {
  if (DETACH_EVENTS.has(eventName) && cfg.enabled && cfg.autoCapture) {
    const detached = await maybeDetach(cfg, { approve: () => {} });
    if (detached) return;
  }

  try {
    input = JSON.parse(await readHookStdin());
  } catch {
    input = {};
  }

  if (!input.session_id && input.sessionId) input.session_id = input.sessionId;

  nativeSessionId = resolveNativeSessionId(input);
  sessionId = deriveAgentSessionId("kc-", input);
  cwd = resolveAgentCwd(input);
  ({ fetchJSON } = makeAgentFetchJSON(cfg, cwd));
  await main();
}

run().catch((error) => {
  logError("uncaught", error);
});
