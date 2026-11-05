import { loadConfig } from "./config.mjs";
import { buildContextSearchBody, isRecallEnabled, selectRecallContent } from "./shared/recall-core.mjs";
import { estimateTokens } from "./shared/profile-inject.mjs";
import { PluginError, request } from "./http.mjs";
import { withRecallLedger } from "./recall-ledger.mjs";
import { bypassedSession, connectionScope, dataDirectory } from "./scope.mjs";
import { nativeOwner } from "./capture.mjs";
import { applyInputFilters, compileInputFilters } from "./shared/input-filters.mjs";
import { describeConversation, isAuxiliaryRequest, memoryBlock, replayContext } from "./conversation.mjs";

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

// Use the shared CJK-aware estimator for both profile and combined context.
function withinTokens(value, budget) {
  if (estimateTokens(value) <= budget) return value;
  const chars = Array.from(value);
  let low = 0, high = chars.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (estimateTokens(chars.slice(0, middle).join("")) <= budget) low = middle;
    else high = middle - 1;
  }
  return chars.slice(0, low).join("");
}

const outcome = (error) => error.code === "unconfigured" ? "unbound"
  : ["unauthorized", "invalid"].includes(error.code) ? error.code : "unavailable";

export async function recallRequest(body, ctx, env = process.env, resolvedConfig, startedAt = performance.now()) {
  if (!["local", "magpie-key"].includes(ctx.origin)) {
    ctx.report?.("unbound", 0); return;
  }
  const conversation = describeConversation(body, ctx.protocol);
  if (!conversation || isAuxiliaryRequest(body, ctx)) {
    ctx.report?.("skipped", 0); return;
  }
  try {
    const cfg = resolvedConfig || await loadConfig(env, ctx.options || {});
    if (!cfg.enabled || bypassedSession(cfg, ctx)) { ctx.report?.("skipped", 0); return; }
    if (await nativeOwner(cfg, ctx, body)) { ctx.report?.("skipped", 0); return; }
    if (cfg.recallPeerScope === "actor" && !cfg.peerId) { ctx.report?.("unbound", 0); return; }
    // No plaintext credentials/queries enter filenames or logs. Changing the
    // connection identity, agent, workspace or model starts a different scope.
    const bound = Boolean(text(ctx.session));
    const decide = async (decisions) => {
      const { users, latest, fresh, anchors } = conversation;
      // Missing earlier decisions means a restart with lost state, a rewritten
      // history or an unknown compaction. Never attach old memory by text alone.
      const required = fresh ? users.slice(0, -1) : users;
      if (bound && required.some((index) => !Object.hasOwn(decisions, anchors[index]))) return { outcome: "unbound" };
      const anchor = anchors[latest];
      if (fresh && !Object.hasOwn(decisions, anchor)) {
        let block = "", code = "skipped";
        const query = applyInputFilters(text(ctx.query), compileInputFilters(cfg.recallQueryFilters).rules, { role: "user" }).text;
        if (isRecallEnabled(cfg) && (users.length === 1 || query.length >= cfg.minQueryLength)) {
          try {
            const shouldSearch = query.length >= cfg.minQueryLength;
            const recallBody = buildContextSearchBody(cfg, { excludeUris: cfg.recallExcludeUris });
            recallBody.query = query.slice(0, 16000);
            // Match Magpie's per-hook option validation; the host exposes no remaining deadline.
            const configuredBudget = ctx.options?.gatewayTimeoutMs;
            const hostBudgetMs = Number.isInteger(configuredBudget) && configuredBudget >= 100 && configuredBudget <= 10000
              ? configuredBudget : 1000;
            // Reserve 200 ms for result assembly, ledger I/O and host serialization.
            // This is a tested safety margin, not a guarantee against earlier host cancellation.
            const reserveMs = 200;
            const remainingMs = hostBudgetMs - (performance.now() - startedAt) - reserveMs;
            const timeoutMs = Math.floor(Math.min(cfg.recallContextTimeoutMs || cfg.timeoutMs, cfg.timeoutMs, 9000, remainingMs));
            // HTTP enforces a 100 ms minimum: do not start work that cannot fit.
            if (timeoutMs < 100) throw new PluginError("timeout");
            // End both HTTP waits together without treating our deadline as host cancellation.
            const deadline = AbortSignal.timeout(timeoutMs);
            const requestSignal = ctx.signal ? AbortSignal.any([ctx.signal, deadline]) : deadline;
            // A profile holds stable facts that semantic search may not rank.
            // Read it only on the first user turn; later turns replay that
            // exact snapshot. Both reads share the host's wall-clock budget.
            const [search, profile] = await Promise.allSettled([
              shouldSearch
                ? request(cfg, "/api/v1/search/search", { body: recallBody, signal: requestSignal, timeoutMs })
                : Promise.resolve(""),
              users.length === 1
                ? request(cfg, `/api/v1/content/read?${new URLSearchParams({ uri: "viking://~/memories/profile.md" })}`, { signal: requestSignal, timeoutMs })
                : Promise.resolve(""),
            ]);
            ctx.signal?.throwIfAborted();
            const profileText = profile.status === "fulfilled" && typeof profile.value === "string" ? profile.value.trim() : "";
            const profileOpen = '<user-profile uri="viking://~/memories/profile.md">\n';
            const profileClose = "\n</user-profile>";
            const profileBudget = Math.min(cfg.profileTokenBudget, Math.floor(cfg.recallMaxTokens / 2));
            const profileContent = withinTokens(profileText, Math.max(0, profileBudget - estimateTokens(profileOpen + profileClose)));
            const profileBlock = profileContent ? profileOpen + profileContent + profileClose : "";
            if (search.status === "rejected" && !profileBlock) throw search.reason;
            if (!shouldSearch && profile.status === "rejected") throw profile.reason;
            const recalled = search.status === "fulfilled" ? selectRecallContent(search.value) : "";
            const recallContent = withinTokens(recalled, Math.max(0,
              cfg.recallMaxTokens - estimateTokens(profileBlock + (profileBlock ? "\n\n" : ""))));
            const memory = [profileBlock, recallContent].filter(Boolean).join("\n\n");
            code = memory ? "ready" : "empty";
            if (memory) block = memoryBlock(memory);
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
    };
    // Stateless requests recall only for this request. They never read/write
    // a ledger or reuse another conversation's profile/ownership decisions.
    const result = bound
      ? await withRecallLedger(dataDirectory(cfg), connectionScope(cfg, ctx, body.model || ctx.model || ""), ctx.signal, decide)
      : await decide({});
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

export async function toolConfig(env, options) {
  const cfg = await loadConfig(env, options);
  if (!cfg.enabled || !cfg.mcpEnabled || !cfg.explicitMcpOptIn) throw new PluginError("disabled");
  return cfg;
}
