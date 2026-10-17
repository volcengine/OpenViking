#!/usr/bin/env node

/**
 * Auto-Recall hook for Kimi Code (UserPromptSubmit).
 *
 * Reads the prompt from stdin → searches OpenViking → writes the recalled
 * block as RAW STDOUT TEXT, which Kimi appends to the conversation.
 *
 * Why raw text (DESIGN.md §4): Kimi's runHook.ts parses only
 * message/permissionDecision/permissionDecisionReason out of structured hook
 * JSON, so the portable `hookSpecificOutput.additionalContext` form is
 * silently dropped (kimi-code issue #4255, open on main). Plain stdout is the
 * one reliable injection channel.
 *
 * Failure policy: fail-open. Timeout, network error or an unreachable server
 * exit 0 with no output — memory must never block the user's prompt (Kimi's
 * own hook design is fail-open for exactly this reason).
 *
 * cwd: the payload's `cwd` field is the project directory. Plugin hooks run
 * with cwd = plugin root (kimi-code manager.ts sets `cwd: record.root`), so
 * process.cwd() is never the project.
 *
 * Prompt shape: Kimi sends `prompt` as a string OR a ContentPart[] array
 * (kimi-code issue #917) — both are handled.
 *
 * Structure mirrors examples/codex-memory-plugin/scripts/auto-recall.mjs.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { deriveOvSessionId, loadState, saveState } from "./session-state.mjs";
import { buildAgentProfile, recallForPrompt, runHookStage } from "./shared/agent-hook-runtime.mjs";
import { applyInputFilters, compileInputFilters } from "./shared/input-filters.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

let cfg = loadConfig();
const { log, logError } = createLogger("auto-recall");
let effectivePeer = { peerId: "", legacyPeerId: "" };
let fetchJSON = createOvHttp(cfg, { defaultTimeoutMs: cfg.timeoutMs });

let emitted = false;
let recallDeadline = null;

/** Kimi's envelope for UserPromptSubmit is the raw text itself. */
function emitText(text) {
  if (emitted) return;
  emitted = true;
  if (recallDeadline) clearTimeout(recallDeadline);
  const value = typeof text === "string" ? text.trim() : "";
  if (value) process.stdout.write(value);
}

function promptToText(prompt) {
  if (typeof prompt === "string") return prompt.trim();
  if (Array.isArray(prompt)) {
    return prompt
      .map((part) => (typeof part === "string" ? part : part?.text || ""))
      .filter(Boolean)
      .join("\n")
      .trim();
  }
  return "";
}

recallDeadline = setTimeout(() => {
  logError("recall_timeout", `timed out after ${cfg.recallTimeoutMs}ms`);
  emitText("");
}, cfg.recallTimeoutMs);
recallDeadline.unref?.();

runHookStage(
  {
    clientId: "kimicode",
    loadConfig: (cwd) => {
      cfg = loadConfig(cwd);
      return cfg;
    },
    gates: { enabled: (reloaded) => reloaded.autoRecall },
    envelope: (value) => emitText(typeof value === "string" ? value : ""),
    onSkip: (reason) => log("skip", { stage: "init", reason }),
  },
  async (stage) => {
    const { input, cwd } = stage;
    cfg = stage.cfg;
    effectivePeer = resolveEffectivePeerId({ cfg, cwd });
    fetchJSON = createOvHttp(cfg, {
      defaultTimeoutMs: cfg.timeoutMs,
      resolveActorPeerId: () => effectivePeer.peerId,
      requireJsonBody: true,
    });

    const kimiSessionId = typeof input.session_id === "string" ? input.session_id.trim() : "";
    const recallSessionId = kimiSessionId ? deriveOvSessionId(kimiSessionId) : "";

    let userPrompt = promptToText(input.prompt);
    log("start", {
      kimiSessionId: kimiSessionId || null,
      recallSessionId,
      queryLength: userPrompt.length,
      peerSource: effectivePeer.source,
    });

    // Filters run before the length gate, so a prompt whose only content was
    // a stripped prefix is a short query rather than a search for "".
    const queryFilters = compileInputFilters(cfg.recallQueryFilters);
    if (queryFilters.rules.length) {
      const verdict = applyInputFilters(userPrompt, queryFilters.rules, { role: "user" });
      if (verdict.dropped) {
        log("skip", { stage: "query_filter", reason: "query_filtered", rule: verdict.ruleIndex });
        emitText("");
        return;
      }
      if (verdict.changed) log("query_filter", { rawLength: userPrompt.length, length: verdict.text.length });
      userPrompt = verdict.text;
    }

    if (!userPrompt || userPrompt.length < cfg.minQueryLength) {
      log("skip", { stage: "query_check", reason: "query too short or empty" });
      emitText("");
      return;
    }

    const health = await fetchJSON("/health");
    if (!health.ok) {
      logError("health_check", "server unreachable or unhealthy");
      emitText("");
      return;
    }

    const recalled = await recallForPrompt(fetchJSON, cfg, userPrompt, cwd, log, {
      sessionId: recallSessionId,
    });
    log("recall_complete", { stage: recalled?.stage, chars: recalled?.block?.length || 0 });

    // Profile rides the FIRST successful prompt of a session: Kimi discards
    // SessionStart stdout (#2873), so the prompt hook is the only carrier.
    const parts = [];
    if (recalled?.block) parts.push(recalled.block);
    const state = kimiSessionId ? await loadState(kimiSessionId) : {};
    if (kimiSessionId && !state.profileInjected) {
      const profile = await buildAgentProfile(fetchJSON, cfg, cwd);
      if (profile) {
        parts.unshift(profile);
        log("profile_injected", { chars: profile.length });
      }
      state.profileInjected = true;
      await saveState(kimiSessionId, state).catch(() => {});
    }

    emitText(parts.filter(Boolean).join("\n\n"));
  },
).catch((error) => {
  logError("auto-recall", error?.message || String(error));
  emitText(""); // fail-open: never block the user's prompt
});
