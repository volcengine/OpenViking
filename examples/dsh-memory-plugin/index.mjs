import { OpenVikingClient } from "./client.mjs";
import { resolveConfig } from "./config.mjs";
import { injectStartupProfile } from "./lifecycle.mjs";
import { OpenVikingRuntime } from "./runtime.mjs";
import { registerOpenVikingTools } from "./tools.mjs";
import { guardVikingUri } from "./uri-guard.mjs";

export const name = "openviking-memory";
export const inject = ["agents", "sessions", "tools"];

export function apply(ctx, input = {}) {
  const config = resolveConfig(input);
  const client = new OpenVikingClient(config);
  const runtime = new OpenVikingRuntime(client, config, ctx.logger);
  ctx.provide("openvikingMemory", runtime);
  ctx.effect(
    () => () => runtime.disposeAll(),
    "openvikingMemory.disposeAll()",
  );

  registerOpenVikingTools(ctx, client, runtime);

  ctx.on("agent/session-start", ({ agent }) => {
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
    runtime.capture(session, event);
    runtime.maybeCommit(session, event);
  });

  ctx.on("session/flush", async session => {
    await runtime.flush(session);
  });

  ctx.on("tools/pre-execute", guardVikingUri);
}
