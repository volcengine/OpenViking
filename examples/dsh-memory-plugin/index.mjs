import { resolveConfig } from "./config.mjs";
import { promptText } from "./decode.mjs";
import host from "./host.mjs";
import { fillHistorySession, mountOpenVikingMcp } from "./mcp.mjs";
import { runStage } from "./shared/flow/index.mjs";
import { mountOpenVikingSkills } from "./skills.mjs";
import { guardVikingUri, noticeVikingUri } from "./uri-guard.mjs";

export const name = "openviking-memory";
export const inject = ["agents", "sessions", "tools"];

const CAPTURED_EVENTS = new Set(["user/message", "assistant/message", "tool/call", "tool/result"]);

export function apply(ctx, input = {}) {
  const config = resolveConfig(input);
  const log = message => ctx.logger?.debug?.(`[openviking:dsh] ${message}`);
  // Rebuilt from the host input for the session's directory, not the config
  // merged for boot's cwd.
  const configs = new Map();
  const configFor = cwd => {
    if (!configs.has(cwd)) configs.set(cwd, resolveConfig(input, process.env, cwd));
    return configs.get(cwd);
  };
  // Called synchronously from the listeners: the flow's session lock keeps
  // calls for one session in the order they were made.
  const inFlight = new Map();
  const run = (event, session, extra = {}) => {
    const cwd = session?.header?.cwd || process.cwd();
    const payload = { session_id: session?.id, cwd, subagent: session?.header?.origin === "subagent", ...extra };
    const pending = runStage(host, event, payload, { cfg: configFor(cwd) }).catch(error => {
      log(`${event} ${error instanceof Error ? error.message : String(error)}`);
      return {};
    });
    const list = inFlight.get(session?.id) || new Set();
    inFlight.set(session?.id, list.add(pending));
    pending.finally(() => { list.delete(pending); if (!list.size) inFlight.delete(session?.id); });
    return pending;
  };
  const open = new Map();
  const end = session => (open.delete(session.id) ? run("session/dispose", session) : undefined);
  ctx.effect(() => () => Promise.all([...open.values()].map(end)), "openvikingMemory.endSessions()");

  // DSH 0.1.5 and earlier emit `agent/created` without `source`.
  let unsupported = false;
  const started = new WeakSet();
  // The listener must resolve to undefined: any other value bails the serial
  // dispatch and skips the listeners registered after this one. AgentLoop holds
  // queued input until it settles.
  ctx.on("agent/created", async ({ agent, source, signal }) => {
    if (source === undefined) {
      if (!unsupported) ctx.logger?.warn?.("[openviking:dsh] this DeepSeek Harness version is not supported (needs 0.1.7-rc.2 or later); OpenViking memory is off");
      unsupported = true;
      return undefined;
    }
    if (unsupported || started.has(agent)) return undefined;
    started.add(agent);
    open.set(agent.session.id, agent.session);
    agent.ctx.effect(() => () => end(agent.session), "openvikingMemory.endSession()");
    await untilAborted((async () => {
      const output = await run("agent/created", agent.session, { resumed: source, idle: agent.status === "idle" });
      const message = host.emit("agent/created", output);
      if (message && !signal?.aborted && agent.status === "idle") agent.inject(message);
    })(), signal);
    return undefined;
  });

  // prepend: downstream waterfall listeners run first, so this plugin sees
  // the final claimed batch and appends after every other contributor.
  ctx.on("agent/pre-step", async ({ agent, signal }, next) => {
    const decision = await next();
    if (unsupported || decision.kind !== "enter" || signal.aborted) return decision;
    const prompt = promptText(decision.messages);
    if (!prompt) return decision;
    const output = await run("agent/pre-step", agent.session, { prompt, agent, signal });
    const message = host.emit("agent/pre-step", output);
    // Keep the rest of the decision: goal-round-driver marks its entries with
    // `startsRequestSeries`, and rebuilding the object would drop that flag.
    return message && !signal.aborted ? { ...decision, messages: [...decision.messages, message] } : decision;
  }, { prepend: true });

  ctx.on("session/event", (session, event) => {
    if (unsupported) return;
    const kind = event?.type === "compaction/start" ? "compaction/start"
      : CAPTURED_EVENTS.has(event?.type) ? "session/event" : "";
    if (kind) run(kind, session, { event });
  });

  ctx.on("session/flush", async session => {
    await Promise.all(inFlight.get(session.id) || []);
  });

  const skipMemory = session => config.skipSubagentSessions && session?.header?.origin === "subagent";
  ctx.on("tools/pre-execute", guardVikingUri);
  ctx.on("tools/post-execute", noticeVikingUri);
  ctx.on("tools/execute", (exec, next) => (
    unsupported || skipMemory(exec.agent?.session) ? next() : fillHistorySession(ctx.tools, exec, next, ctx.sessions)
  ));

  // Mounted last, and deliberately not awaited: the bridge's apply blocks on
  // its first tools/list, so a server that accepts the connection but never
  // answers would otherwise hold up every registration above it.
  // The skills tell the model to call the MCP tools, so `mcpEnabled: false`
  // leaves both out, as on OpenCode and pi.
  if (config.mcpEnabled === false) return;
  mountOpenVikingMcp(ctx, config);
  mountOpenVikingSkills(ctx);
}

// Resolve when `promise` settles or `signal` aborts, whichever comes first. An
// abandoned startup keeps running and drops its injection.
function untilAborted(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.resolve();
  return new Promise(resolve => {
    const onAbort = () => resolve();
    signal.addEventListener("abort", onAbort, { once: true });
    promise.finally(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    });
  });
}
