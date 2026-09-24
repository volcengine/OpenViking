#!/usr/bin/env node

/**
 * Auto-Recall Hook Script for Codex.
 *
 * Triggered by UserPromptSubmit hook.
 * Reads `prompt` from stdin → searches OpenViking → returns recalled memories
 * via `hookSpecificOutput.additionalContext` so Codex injects them into the turn.
 *
 * Codex output schema (codex-rs/hooks/schema/generated/user-prompt-submit.command.output.schema.json):
 *   { hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "<text>" } }
 * — `decision: "approve"` is NOT a codex thing; only `decision: "block"` is. So a no-op
 * is just `{}`.
 */

import { join } from "node:path";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { getStateDir, deriveOvSessionId } from "./session-state.mjs";
import { createCodexCompressor } from "./host-compressor.mjs";
import { buildRecallBlockDetailed } from "./shared/recall-core.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";
import { applyInputFilters, compileInputFilters } from "./shared/input-filters.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

let cfg = loadConfig();
const { log, logError } = createLogger("auto-recall");
let effectivePeer = { peerId: "" };

let emitted = false;
let activeCompressor = null;
let recallDeadline = null;
const RECALL_DIGEST_CACHE_PATH = join(getStateDir(), "recall-digest.json");

function output(obj, exitAfter = false) {
  if (emitted) return;
  emitted = true;
  if (recallDeadline) clearTimeout(recallDeadline);
  const line = JSON.stringify(obj) + "\n";
  if (exitAfter) {
    process.stdout.write(line, () => process.exit(0));
    return;
  }
  process.stdout.write(line);
}

function emit(additionalContext) {
  if (!additionalContext) {
    output({});
    return;
  }
  const wrappedContext = String(additionalContext).trim();
  if (!wrappedContext) {
    output({});
    return;
  }
  output({
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: wrappedContext,
    },
  });
}

recallDeadline = setTimeout(() => {
  logError("recall_timeout", `timed out after ${cfg.recallTimeoutMs}ms`);
  try {
    activeCompressor?.kill("SIGKILL");
  } catch { /* best effort */ }
  output({}, true);
}, cfg.recallTimeoutMs);
recallDeadline.unref?.();

// Rebuilt after the hook reloads config for the payload's directory.
function makeFetchJSON() {
  return createOvHttp(cfg, {
    defaultTimeoutMs: cfg.timeoutMs,
    resolveActorPeerId: () => effectivePeer.peerId,
    requireJsonBody: true,
  });
}

let fetchJSON = makeFetchJSON();

runHookStage({
  loadConfig,
  gates: { enabled: (reloaded) => reloaded.autoRecall },
  envelope: emit,
  onSkip: (reason) => log("skip", { stage: "init", reason }),
}, async (stage) => {
  const { input, cwd } = stage;
  cfg = stage.cfg;
  effectivePeer = resolveEffectivePeerId({ cfg, cwd });
  fetchJSON = makeFetchJSON();

  let userPrompt = (input.prompt || "").trim();
  const codexSessionId = typeof input.session_id === "string" ? input.session_id.trim() : "";
  const recallSessionId = codexSessionId ? deriveOvSessionId(codexSessionId) : "";
  log("start", {
    codexSessionId: codexSessionId || null,
    recallSessionId,
    query: userPrompt.slice(0, 200),
    queryLength: userPrompt.length,
    config: {
      recallLimit: cfg.recallLimit,
      scoreThreshold: cfg.scoreThreshold,
      peerSource: effectivePeer.source,
      recallPeerScope: cfg.recallPeerScope,
    },
  });

  // Filters run before the length gate, so a prompt whose only content was a
  // stripped prefix is a short query rather than a search for the empty string.
  const queryFilters = compileInputFilters(cfg.recallQueryFilters);
  if (queryFilters.rules.length) {
    const verdict = applyInputFilters(userPrompt, queryFilters.rules, { role: "user" });
    if (verdict.dropped) {
      log("skip", { stage: "query_filter", reason: "query_filtered", rule: verdict.ruleIndex, op: verdict.op });
      emit();
      return;
    }
    if (verdict.changed) log("query_filter", { rawLength: userPrompt.length, length: verdict.text.length });
    userPrompt = verdict.text;
  }
  if (queryFilters.errors.length) log("query_filter_errors", { errors: queryFilters.errors });

  if (!userPrompt || userPrompt.length < cfg.minQueryLength) {
    log("skip", { stage: "query_check", reason: "query too short or empty" });
    return;
  }

  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable or unhealthy");
    return;
  }

  const runCompressor = await createCodexCompressor(cfg, {
    log, logError, onActiveChild: (child) => { activeCompressor = child; },
  });
  const recalled = await buildRecallBlockDetailed(fetchJSON, cfg, userPrompt, {
    actorPeerId: effectivePeer.peerId, legacyPeerId: effectivePeer.legacyPeerId,
    sessionId: recallSessionId || "", runCompressor,
    localCompressorAvailable: Boolean(runCompressor),
    digestCachePath: RECALL_DIGEST_CACHE_PATH, log,
  });
  log("recall_complete", { stage: recalled.stage, chars: recalled.block.length });
  return recalled.block;
}).catch((err) => { logError("uncaught", err); emit(); });
