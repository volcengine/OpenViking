/**
 * The one flow every hook-script host runs. A host module says what its events
 * mean and how its payloads, transcript and answers are shaped (host.d.mts);
 * what happens at each stage is decided here and nowhere else.
 */

import { readFileSync } from "node:fs";

import {
  createAgentLogger,
  loadAgentHookConfig,
  makeAgentFetchJSON,
  resolveAgentCwd,
} from "../agent-hook-runtime.mjs";
import { isCaptureEnabled } from "../capture-utils.mjs";
import { isBypassed } from "../session-model.mjs";
import { capture } from "./capture.mjs";
import { deliverPointer } from "./pointer.mjs";
import { promptSubmit } from "./prompt.mjs";
import { sessionStart } from "./start.mjs";
import { nativeSessionId, ovSessionId, toolCall } from "./tool.mjs";

export { toolCall };

/** Stages that write to OpenViking and answer nothing the model reads. */
const WRITE_STAGES = new Set(["turnEnd", "compactSoon", "end", "toolResult"]);

export const stageOf = (host, event) => host.events[event] || "";
export const isWriteStage = (stage) => WRITE_STAGES.has(stage);

/**
 * Whether a write-stage hook may answer its host first and do the work in a
 * detached process. `cfg` is the config as this process's directory resolves
 * it: the decision is taken before stdin is read, while a worker can still be
 * handed the payload.
 */
export function mayDetach(host, event, cfg) {
  if (!isWriteStage(stageOf(host, event)) || !isCaptureEnabled(cfg)) return false;
  // An event the host gives too little time detaches whatever writePathAsync says.
  if (host.detachEvents?.includes(event)) return "always";
  return Boolean(host.canDetach) && !host.syncEvents?.includes(event);
}

/** One deadline for every request an event sends, so their timeouts cannot add up past the host's. */
function withRequestBudget(fetchJSON, budgetMs, defaultTimeoutMs = 0) {
  if (!budgetMs) return fetchJSON;
  const deadline = Date.now() + budgetMs;
  return Object.assign((path, init = {}, options = {}) => {
    const remaining = deadline - Date.now();
    // ov-http clamps a request to at least one second, so one started inside
    // the last second would overrun.
    if (remaining < 1000) {
      return Promise.resolve({
        ok: false,
        status: 0,
        result: null,
        error: { name: "AbortError", aborted: true, message: "hook request budget exhausted" },
      });
    }
    const requested = Number(options.timeoutMs) || Number(defaultTimeoutMs) || remaining;
    return fetchJSON(path, init, { ...options, timeoutMs: Math.min(requested, remaining) });
  }, { origin: fetchJSON.origin });
}

function transcriptReader(host) {
  return (ref) => {
    try {
      const path = host.locate ? host.locate(ref) : ref;
      return path ? readFileSync(path, "utf8") : null;
    } catch {
      return null;
    }
  };
}

/**
 * Run one host event and return what to answer: `{ context }` for the model,
 * `{ deny }`, `{ notice }` or `{ input }` for a tool call, or `{}`. The caller
 * hands it to `host.emit`.
 */
export async function runStage(host, event, payload = {}, { detached = false } = {}) {
  const stage = stageOf(host, event);
  if (!stage) return {};
  if (stage === "toolCall") return toolCall(host, payload, event);

  const read = host.read(event, payload, { text: transcriptReader(host) }) || {};
  const cwd = read.cwd || resolveAgentCwd(payload);
  const cfg = loadAgentHookConfig(host.id, cwd);
  const { log, logError } = createAgentLogger(host.id, event, cfg);
  const nativeId = nativeSessionId(host, event, payload, read);
  const child = String(read.child || "");
  const agent = makeAgentFetchJSON(cfg, cwd);
  const ctx = {
    host,
    event,
    stage,
    read,
    payload,
    cfg,
    cwd,
    detached,
    nativeSessionId: nativeId,
    // A child session has state, a lock and an OpenViking session of its own.
    stateKey: child ? `${nativeId}__${child.replace(/[^A-Za-z0-9._-]/g, "-")}` : nativeId,
    sessionId: ovSessionId(host, nativeId, child),
    budgetMs: host.budgets?.[event] || 0,
    fetchJSON: withRequestBudget(agent.fetchJSON, host.budgets?.[event], cfg.timeoutMs),
    // The peer the actor-peer header already carries, stamped into captured
    // messages because session writes read it from the body only.
    get peerId() { return agent.effectivePeer.peerId; },
    log,
    logError,
    // What the event did, for `host.observe`.
    outcome: {},
  };
  const skip = read.skip
    || (cfg.enabled === false && "disabled")
    || (isBypassed(cfg, { sessionId: nativeId, cwd }) && "bypass")
    || (child && cfg.skipSubagentSessions && "subagent");
  let output = {};
  if (skip) {
    log("skip", { reason: skip });
    ctx.outcome.skip = skip;
  } else if (stage === "start") output = await sessionStart(ctx);
  else if (stage === "prompt") output = await promptSubmit(ctx);
  else if (stage === "compacted") {
    const { pointer } = deliverPointer(ctx, {});
    if (pointer) output = { context: pointer };
  } else output = await capture(ctx);

  if (output.context && host.present) output = { ...output, context: host.present(ctx, output.context) };
  if (host.observe) {
    if (output.context) ctx.outcome.context = output.context;
    await Promise.resolve(host.observe(ctx, ctx.outcome)).catch((error) => logError("observe", error));
  }
  return output;
}
