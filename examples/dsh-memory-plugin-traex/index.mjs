import { OpenVikingClient } from "./client.mjs";
import { resolveConfig } from "./config.mjs";
import { injectStartupProfile } from "./lifecycle.mjs";
import { OpenVikingRuntime } from "./runtime.mjs";
import { registerOpenVikingTools } from "./tools.mjs";
import { guardVikingUri } from "./uri-guard.mjs";

export const name = "openviking-memory-traex";
export const inject = ["agents", "sessions", "tools"];

export function apply(ctx, input = {}) {
  const config = resolveConfig(input);
  const client = new OpenVikingClient(config);
  const runtime = new OpenVikingRuntime(client, config, ctx.logger);
  ctx.provide("openvikingMemory", runtime);

  registerOpenVikingTools(ctx, client, runtime);

  ctx.on("agent/session-start", async ({ agent }) => {
    await injectStartupProfile(agent, runtime);
  });

  ctx.on("agent/pre-step", async ({ agent, messages }, next) => {
    const decision = await next();
    if (decision.kind !== "enter") return decision;
    const profile = await runtime.profileMessage(agent);
    const recall = await runtime.recallMessage(agent, messages);
    const additions = [profile, recall].filter(Boolean);
    return additions.length > 0
      ? { kind: "enter", messages: [...decision.messages, ...additions] }
      : decision;
  });

  ctx.on("session/event", (session, event) => {
    runtime.capture(session, event);
    runtime.maybeCommit(session, event);
  });

  ctx.on("session/flush", async () => {
    await runtime.flush();
  });

  ctx.on("session/disposed", session => {
    runtime.dispose(session);
  });

  ctx.on("tools/pre-execute", guardVikingUri);
}
