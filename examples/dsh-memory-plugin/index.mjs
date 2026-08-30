import { OpenVikingClient } from "./client.mjs";
import { resolveConfig } from "./config.mjs";
import { injectStartupProfile } from "./lifecycle.mjs";
import { mountOpenVikingMcp } from "./mcp.mjs";
import { OpenVikingRuntime } from "./runtime.mjs";
import { mountOpenVikingSkills } from "./skills.mjs";
import { guardVikingUri } from "./uri-guard.mjs";

export const name = "openviking-memory";
export const inject = ["agents", "sessions", "tools"];

export function apply(ctx, input = {}) {
  const config = resolveConfig(input);
  const client = new OpenVikingClient(config);
  const runtime = new OpenVikingRuntime(client, config, ctx.logger);
  const skipMemory = session => (
    config.skipSubagentSessions && session?.header?.origin === "subagent"
  );
  ctx.provide("openvikingMemory", runtime);
  ctx.effect(
    () => () => runtime.disposeAll(),
    "openvikingMemory.disposeAll()",
  );

  ctx.on("agent/session-start", ({ agent }) => {
    if (skipMemory(agent.session)) return false;
    agent.ctx.effect(
      () => () => runtime.dispose(agent.session),
      "openvikingMemory.disposeSession()",
    );
    return injectStartupProfile(agent, runtime);
  });

  // prepend: downstream waterfall listeners run first, so this plugin sees
  // the final claimed batch and appends after every other contributor.
  ctx.on("agent/pre-step", async ({ agent, messages, signal }, next) => {
    const decision = await next();
    if (skipMemory(agent.session)) return decision;
    if (decision.kind !== "enter" || signal.aborted) return decision;
    const profile = await runtime.profileMessage(agent);
    if (signal.aborted) return decision;
    const recall = await runtime.recallMessage(agent, decision.messages);
    if (signal.aborted) return decision;
    const additions = [profile, recall].filter(Boolean);
    return additions.length > 0
      ? { kind: "enter", messages: [...decision.messages, ...additions] }
      : decision;
  }, { prepend: true });

  ctx.on("session/event", (session, event) => {
    if (skipMemory(session)) return;
    runtime.capture(session, event);
    runtime.maybeCommit(session, event);
  });

  ctx.on("session/flush", async session => {
    if (skipMemory(session)) return;
    await runtime.flush(session);
  });

  ctx.on("tools/pre-execute", guardVikingUri);

  // Mounted last, and deliberately not awaited: the bridge's apply blocks on
  // its first tools/list, so a server that accepts the connection but never
  // answers would otherwise hold up every registration above it.
  mountOpenVikingMcp(ctx, config);
  mountOpenVikingSkills(ctx);
}
