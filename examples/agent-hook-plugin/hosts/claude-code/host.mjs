import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { loadAgentHookConfig } from "../../lib/agent-hook-runtime.mjs";
import { applyInputFilters, compileInputFilters } from "../../lib/input-filters.mjs";
import { denyHookSpecificOutput, noticeHookSpecificOutput, readToolEvent } from "../../lib/uri-guard.mjs";
import { bindCurrentSession, currentSessionId } from "./binding.mjs";
import { createHostCompressor } from "./compressor.mjs";
import { decode } from "./decode.mjs";
import { OV_HOME, readJsonState, writeJsonState } from "./state.mjs";

// The MCP proxy imports this module once, when Claude Code starts it.
const STARTED_AT = Date.now();

const INJECTS = new Set(["SessionStart", "UserPromptSubmit"]);
const APPROVES = new Set(["Stop", "PreCompact", "SessionEnd", "SubagentStop"]);
const CAPTURES = new Set(["turnEnd", "compactSoon", "end"]);
// What the recall core reports when it has no block.
const EMPTY_RECALL = new Set(["disabled", "no_results", "filtered_out", "request_rejected", "error"]);
const URI_RE = /viking:\/\/[^\s<>"')\]]+/g;
const OWN_BLOCKS = /<openviking-context source="(?:session-start|session-history)">[\s\S]*?<\/openviking-context>/g;

const safeName = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, "_");

function capturedTurnCount(file) {
  try {
    const count = JSON.parse(readFileSync(file, "utf8")).capturedTurnCount;
    return Number.isInteger(count) && count >= 0 ? { capturedTurnCount: count } : undefined;
  } catch {
    return undefined;
  }
}

function recallReason(ctx, outcome) {
  if (outcome.skip) return outcome.skip;
  const recall = outcome.recall;
  if (!recall) return ctx.read.prompt ? "" : "short_query";
  if (recall.stage === "no_query") {
    const { rules } = compileInputFilters(ctx.cfg.recallQueryFilters);
    const dropped = rules.length > 0 && applyInputFilters(ctx.read.prompt, rules, { role: "user" }).dropped;
    return dropped ? "query_filtered" : "short_query";
  }
  return EMPTY_RECALL.has(recall.stage) ? recall.stage : "ok";
}

function observePrompt(ctx, outcome) {
  if (outcome.skip === "disabled") return;
  const reason = recallReason(ctx, outcome);
  if (!reason) return;
  const recall = outcome.recall || {};
  const snapshot = {
    server_url: ctx.cfg.baseUrl,
    latency_ms: recall.latencyMs ?? 0,
    count: 0,
    reason,
    cc_session_id: ctx.payload.session_id,
  };
  if (reason === "ok") {
    // A server-assembled block is one rendered unit whatever it holds, so the
    // count comes from the URIs it cites.
    snapshot.count = recall.stage === "server_assembled"
      ? new Set(String(outcome.context).replace(OWN_BLOCKS, "").match(URI_RE) || []).size
      : (recall.contentCount || 0) + (recall.hintCount || 0);
    snapshot.content_items = recall.contentCount || 0;
    snapshot.hint_items = recall.hintCount || 0;
    snapshot.tokens_used = recall.budgetUsed || 0;
    snapshot.tokens_budget = ctx.cfg.recallTokenBudget;
  }
  writeJsonState("last-recall.json", snapshot);
}

function observeStart(ctx, outcome) {
  if (outcome.skip === "disabled") return;
  bindCurrentSession(ctx.payload.session_id);
  if (outcome.skip) return;
  const source = ctx.read.resumed;
  const context = outcome.context || "";
  if (source) {
    writeJsonState("last-session-event.json", {
      source,
      cc_session_id: ctx.payload.session_id,
      ov_session_id: ctx.cfg.resumeArchiveInject ? ctx.sessionId : null,
      had_context: context.includes("<session-archive>"),
    });
  }
  if (context.includes('<openviking-context source="session-start">')) {
    try {
      const path = join(OV_HOME, "last_inject.md");
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, context, "utf-8");
    } catch { /* audit copy only */ }
  }
}

async function observeCapture(ctx, outcome) {
  const capture = outcome.capture;
  if (!capture && !outcome.committed) return;
  const snapshot = {
    turns_captured: capture?.sent || 0,
    turns_queued: capture?.queued || 0,
    turns_failed: Math.max(capture?.failed || 0, capture?.held || 0),
    pending_tokens: 0,
    commit_threshold: ctx.cfg.commitTokenThreshold,
    committed: Boolean(outcome.committed),
    commit_count: 0,
    total_message_count: 0,
    ov_session_id: ctx.sessionId,
    cc_session_id: ctx.payload.session_id,
  };
  if (capture?.sent > 0) {
    const res = await ctx.fetchJSON(`/api/v1/sessions/${encodeURIComponent(ctx.sessionId)}`);
    const meta = res?.ok ? res.result || {} : {};
    snapshot.pending_tokens = Number(meta.pending_tokens || 0);
    snapshot.commit_count = Number(meta.commit_count || 0);
    snapshot.total_message_count = Number(meta.total_message_count || 0);
  }
  writeJsonState("last-capture.json", snapshot);
  if (outcome.committed) {
    const today = new Date().toISOString().slice(0, 10);
    const prior = readJsonState("daily-stats.json") || {};
    writeJsonState("daily-stats.json", {
      date: today,
      archives: prior.date === today ? Number(prior.archives || 0) + 1 : 1,
    });
  }
}

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "claude-code",
  sessionPrefix: "cc-",
  config: { logFile: "cc-hooks.log", rootKeyFallback: true },
  events: {
    SessionStart: "start",
    UserPromptSubmit: "prompt",
    PreToolUse: "toolCall",
    Stop: "turnEnd",
    PreCompact: "compactSoon",
    SessionEnd: "end",
    // Hooks inside a subagent do not fire; its stop is the only event that
    // sees it, and it hands over the subagent's whole transcript.
    SubagentStop: "end",
  },
  budgets: {
    SessionStart: 25_000,
    UserPromptSubmit: 20_000,
    Stop: 25_000,
    PreCompact: 25_000,
    SessionEnd: 25_000,
    SubagentStop: 25_000,
  },
  canDetach: true,
  // The turn in progress has had no Stop yet: it has to be on the server before
  // the model, right after an auto-compaction, looks it up.
  syncEvents: ["PreCompact"],
  read(event, payload) {
    if (event === "SubagentStop") {
      const agent = String(payload.agent_id || "").trim();
      return agent
        ? { child: `subagent:${agent}`, transcript: String(payload.agent_transcript_path || "").trim() }
        : { skip: "no_agent_id" };
    }
    const { source } = payload;
    return {
      prompt: typeof payload.prompt === "string" ? payload.prompt.trim() : "",
      transcript: String(payload.transcript_path || "").trim(),
      resumed: event === "SessionStart" && (source === "compact" || source === "resume") ? source : "",
    };
  },
  decode,
  tool(payload) {
    const { toolName, toolInput } = readToolEvent(payload);
    return { name: toolName, input: toolInput };
  },
  emit(event, output) {
    if (output.deny) return denyHookSpecificOutput(output.deny);
    if (output.notice) return noticeHookSpecificOutput(output.notice);
    if (INJECTS.has(event)) {
      return output.context
        ? { decision: "approve", hookSpecificOutput: { hookEventName: event, additionalContext: output.context } }
        : { decision: "approve" };
    }
    return APPROVES.has(event) ? { decision: "approve" } : null;
  },
  legacyCursor(nativeSessionId, read) {
    const dir = tmpdir();
    if (read.child) {
      const agent = String(read.child).replace(/^subagent:/, "");
      return capturedTurnCount(join(dir, "openviking-cc-subagent-state", `${safeName(agent)}.json`));
    }
    return capturedTurnCount(join(dir, "openviking-cc-capture-state", `${safeName(nativeSessionId)}.json`));
  },
  compress: createHostCompressor,
  // Claude Code's MCP tool calls carry no session; the proxy's parent is the
  // Claude Code process, whose current session SessionStart recorded.
  mcpSession() {
    if (loadAgentHookConfig("claude-code").enabled === false) return "";
    return currentSessionId({ pid: process.ppid, startedAt: STARTED_AT });
  },
  async observe(ctx, outcome) {
    if (ctx.stage === "start") observeStart(ctx, outcome);
    else if (ctx.stage === "prompt") observePrompt(ctx, outcome);
    else if (CAPTURES.has(ctx.stage) && !ctx.read.child && !outcome.skip) await observeCapture(ctx, outcome);
  },
};
