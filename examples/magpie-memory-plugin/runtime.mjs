import { loadConfig } from "./config.mjs";
import { buildContextSearchBody, isRecallEnabled, selectRecallContent } from "./shared/recall-core.mjs";
import { connectionHeaders, PluginError, request } from "./http.mjs";

import { homedir } from "node:os";
import { join } from "node:path";
import { canonicalRecallValue, recallDigest, withRecallLedger } from "./recall-ledger.mjs";
import { applyInputFilters, compileInputFilters } from "./shared/input-filters.mjs";
import { describeConversation, memoryBlock, replayContext } from "./conversation.mjs";

const text = (value) => typeof value === "string" ? value.trim() : "";

export { loadConfig, PluginError, request };

// Pure protocol adapter; production requests use the persisted ledger below.
export function injectContext(body, protocol, memory) {
  const conversation = describeConversation(body, protocol);
  if (!conversation) return;
  return replayContext(body, protocol, conversation, {
    [conversation.anchors[conversation.latest]]: { block: memoryBlock(memory) },
  });
}

const outcome = (error) => error.code === "unconfigured" ? "unbound"
  : ["unauthorized", "invalid"].includes(error.code) ? error.code : "unavailable";

export async function recallRequest(body, ctx, env = process.env) {
  if (!["local", "magpie-key"].includes(ctx.origin) || !text(ctx.session)) {
    ctx.report?.("unbound", 0); return;
  }
  const conversation = describeConversation(body, ctx.protocol);
  const kind = ctx.kind || body?.metadata?.request_kind;
  if (!conversation || ["auxiliary", "compaction", "title", "subagent"].includes(kind)) {
    ctx.report?.("skipped", 0); return;
  }
  try {
    const cfg = await loadConfig(env);
    if (!cfg.enabled) { ctx.report?.("skipped", 0); return; }
    if (cfg.recallPeerScope === "actor" && !cfg.peerId) { ctx.report?.("unbound", 0); return; }
    // No plaintext credentials/queries enter filenames or logs. Changing the
    // connection identity, agent, workspace or model starts a different scope.
    const headers = Object.fromEntries(Object.entries(connectionHeaders(cfg))
      .filter(([name]) => name.toLowerCase() !== "user-agent"));
    const scope = recallDigest(JSON.stringify(canonicalRecallValue({
      url: cfg.baseUrl, headers,
      peerScope: cfg.recallPeerScope, configPath: cfg.path,
      agent: ctx.agent || "", session: ctx.session, cwd: ctx.cwd || "",
      protocol: ctx.protocol, model: body.model || ctx.model || "",
    })));
    const result = await withRecallLedger(cfg.dataDir || join(homedir(), ".openviking", "magpie"), scope, ctx.signal, async (decisions) => {
      const { users, latest, fresh, anchors } = conversation;
      // Missing earlier decisions means a restart with lost state, a rewritten
      // history or an unknown compaction. Never attach old memory by text alone.
      const required = fresh ? users.slice(0, -1) : users;
      if (required.some((index) => !Object.hasOwn(decisions, anchors[index]))) return { outcome: "unbound" };
      const anchor = anchors[latest];
      if (fresh && !Object.hasOwn(decisions, anchor)) {
        let block = "", code = "skipped";
        const query = applyInputFilters(text(ctx.query), compileInputFilters(cfg.recallQueryFilters).rules, { role: "user" }).text;
        if (isRecallEnabled(cfg) && query.length >= cfg.minQueryLength) {
          try {
            const recallBody = buildContextSearchBody(cfg);
            recallBody.query = query.slice(0, 16000);
            const response = await request(cfg, "/api/v1/search/search", {
              body: recallBody, signal: ctx.signal,
              timeoutMs: Math.min(cfg.recallContextTimeoutMs || cfg.timeoutMs, cfg.timeoutMs, 9000),
            });
            const memory = selectRecallContent(response);
            code = memory ? "ready" : "empty";
            if (memory) block = memoryBlock(memory.slice(0, cfg.recallMaxTokens * 4));
          } catch (error) {
            ctx.signal?.throwIfAborted();
            code = outcome(error);
          }
        }
        // Empty and failed recalls are decisions too. Retrying the same turn
        // must not mutate a history the model may already have used.
        decisions[anchor] = { block, outcome: code };
      }
      const output = replayContext(body, ctx.protocol, conversation, decisions);
      return { body: output, outcome: output ? "ready" : decisions[anchor]?.outcome || "skipped" };
    });
    ctx.report?.(result.outcome, result.body ? Buffer.byteLength(JSON.stringify(result.body)) - Buffer.byteLength(JSON.stringify(body)) : 0);
    return result.body;
  } catch (error) {
    ctx.report?.(outcome(error), 0);
    return;
  }
}

export function requiredText(args, key, max = 16000) {
  const value = text(args?.[key]);
  if (!value || value.length > max) throw new PluginError("invalid");
  return value;
}

export async function toolConfig(env) {
  const cfg = await loadConfig(env);
  if (!cfg.enabled || !cfg.mcpEnabled || !cfg.explicitMcpOptIn) throw new PluginError("disabled");
  return cfg;
}
