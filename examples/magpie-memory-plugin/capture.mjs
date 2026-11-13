import { access, chmod, mkdir, open, readdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { bypassedSession, connectionScope, dataDirectory } from "./scope.mjs";
import { describeConversation, hasNativeContext, isAuxiliaryRequest } from "./conversation.mjs";
import { captureMessages, completedReply, rejectedReply } from "./capture-messages.mjs";
import { withCaptureDecisions, rejectCaptureTurn, captureTurnAnchors, selectCaptureHistory } from "./capture-history.mjs";
import { recallDigest } from "./recall-ledger.mjs";

export const captureDirectory = (cfg, ctx) => join(dataDirectory(cfg), "capture", connectionScope(cfg, ctx));
const workers = new Map();
let warnedMissingSession = false;
export async function nativeOwner(cfg, ctx, body) {
  // Without a session, ownership is request-local: never share a marker
  // between unrelated conversations from the same client.
  if (!ctx.session?.trim()) return cfg.nativeAgents.includes(ctx.agent) || hasNativeContext(body, ctx.protocol);
  const directory = captureDirectory(cfg, ctx);
  const marker = join(directory, "native-owner");
  if (cfg.nativeAgents.includes(ctx.agent) || hasNativeContext(body, ctx.protocol)) {
    if (ctx.derivedSession && !cfg.captureWithoutSession) {
      // Take over an existing scope without creating state while capture is off.
      try { await access(directory); }
      catch (error) { if (error.code === "ENOENT") return true; throw error; }
    } else {
      await mkdir(directory, { recursive: true, mode: 0o700 });
    }
    await chmod(directory, 0o700);
    const file = await open(marker, "a", 0o600);
    await file.close();
    await rm(join(directory, "pending"), { recursive: true, force: true });
    return true;
  }
  try { await access(marker); await rm(join(directory, "pending"), { recursive: true, force: true }); return true; }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  return false;
}

export function launchCaptureWorker(cfg) {
  const directory = dataDirectory(cfg);
  const active = workers.get(directory);
  if (active) { active.again = true; return; }
  const child = spawn(process.execPath, [fileURLToPath(new URL("./capture-worker.mjs", import.meta.url)), dataDirectory(cfg)], {
    detached: true, stdio: "ignore", env: { ...process.env },
  });
  const entry = { child, again: false };
  workers.set(directory, entry);
  child.once("error", () => { if (workers.get(directory) === entry) workers.delete(directory); });
  child.once("exit", () => {
    if (workers.get(directory) !== entry) return;
    workers.delete(directory);
    if (entry.again) launchCaptureWorker(cfg);
  });
  child.unref();
}

export async function queueCapture(cfg, ctx, messages, { launch = true } = {}) {
  if (bypassedSession(cfg, ctx)) return false;
  if (await nativeOwner(cfg, ctx, {})) return false;
  const scope = connectionScope(cfg, ctx);
  const selected = await withCaptureDecisions(dataDirectory(cfg), scope,
    (state) => {
      const selected = selectCaptureHistory(messages, ctx.protocol, state.rejected);
      return { ...selected, turnRevisions: Object.fromEntries(selected.turnAnchors.map((key) => [key, state.revisions[key] || 0])) };
    });
  const payloads = captureMessages(selected.messages, ctx.protocol, cfg);
  if (!payloads?.length || payloads.length > 2000 || payloads.at(-1).role !== "assistant" || payloads.at(-1).message_kind !== "assistant_step") return false;
  const directory = captureDirectory(cfg, ctx);
  const pending = join(directory, "pending");
  await mkdir(pending, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700);
  await chmod(pending, 0o700);
  if ((await readdir(pending)).filter((name) => name.endsWith(".json")).length >= 32) return false;
  const user = payloads.findLast((m) => m.message_kind === "user_query");
  if (!user) return false;
  const turnKey = user.source_message_ids[0].slice(7);
  const options = {};
  if (Object.hasOwn(ctx.options || {}, "autoCapture")) options.autoCapture = cfg.autoCapture;
  if (typeof ctx.options?.captureWithoutSession === "boolean") options.captureWithoutSession = cfg.captureWithoutSession;
  const snapshot = { version: 1, scope, configPath: cfg.path, context: { agent: ctx.agent, session: ctx.session, cwd: ctx.cwd, protocol: ctx.protocol,
    ...(ctx.derivedSession ? { derivedSession: true } : {}) },
    options, turnKey, messages: payloads, turnAnchors: selected.turnAnchors, turnRevisions: selected.turnRevisions };
  const encoded = JSON.stringify(snapshot);
  if (Buffer.byteLength(encoded) > 3 * 1024 * 1024) return false;
  const path = join(pending, `${turnKey}.json`);
  // Write a private staging file first; a hard link publishes it atomically
  // without replacing a retry's already queued snapshot.
  const staging = `${path}.${process.pid}.${recallDigest(String(Math.random())).slice(0, 8)}.tmp`;
  try {
    const file = await open(staging, "wx", 0o600);
    try { await file.writeFile(encoded); } finally { await file.close(); }
    const { link } = await import("node:fs/promises");
    const published = await withCaptureDecisions(dataDirectory(cfg), scope, async (state) => {
      if (selected.turnAnchors.some((key) => state.rejected.includes(key)
        || selected.turnRevisions[key] !== (state.revisions[key] || 0))) return false;
      if (await nativeOwner(cfg, ctx, {})) return false;
      try { await link(staging, path); } catch (error) { if (error.code !== "EEXIST") throw error; }
      return true;
    });
    if (!published) return false;
  } finally { await rm(staging, { force: true }); }
  if (await nativeOwner(cfg, ctx, {})) return false;
  if (launch) launchCaptureWorker(cfg);
  return true;
}

export async function prepareCapture(body, ctx, cfg, options) {
  if (!cfg.enabled || bypassedSession(cfg, ctx) || !["local", "magpie-key"].includes(ctx.origin)) return;
  if (isAuxiliaryRequest(body, ctx)) return;
  const conversation = describeConversation(body, ctx.protocol, { allowNativeContext: true });
  const first = conversation && captureMessages([conversation.messages[conversation.users[0]]], ctx.protocol, cfg)?.[0]?.source_message_ids[0];
  if (!ctx.session?.trim() && first && (cfg.captureWithoutSession
    || cfg.nativeAgents.includes(ctx.agent) || hasNativeContext(body, ctx.protocol))) {
    // Keep the host context unchanged: fallback identity belongs only to capture.
    ctx = { ...ctx, derivedSession: true, session: `derived-${recallDigest(JSON.stringify([ctx.agent || "", ctx.cwd || "", ctx.protocol, first]))}` };
  }
  // Resolve the capture scope before yielding so native takeover also retires
  // derived queues and remains effective when later requests lose the marker.
  if (ctx.derivedSession && bypassedSession(cfg, ctx)) return;
  if (await nativeOwner(cfg, ctx, body)) { ctx.state && (ctx.state.nativeOwner = true); return; }
  if (!cfg.autoCapture || !conversation || !first) return;
  if (!ctx.session?.trim()) {
    if (conversation.fresh && !warnedMissingSession) {
      warnedMissingSession = true;
      // Magpie's report() accepts recall codes only; log separately so a
      // successful recall cannot overwrite the capture diagnostic.
      console.warn("OpenViking capture: no-session — automatic capture unavailable: the host did not provide a session ID. Configure the client to send a stable session header (for example x-session-id), or opt into captureWithoutSession: true (identical opening messages may merge conversations). Stateless recall remains available.");
    }
    return;
  }
  // Never bind across an earlier assistant/tool exchange, even if only one
  // human user remains after client-side trimming or an identity switch.
  const before = captureMessages(conversation.messages.slice(0, conversation.users[0]), ctx.protocol, cfg);
  if (!before || before.length) return;
  const directory = captureDirectory(cfg, ctx);
  const binding = join(directory, "start");
  try {
    const { readFile } = await import("node:fs/promises");
    if (await readFile(binding, "utf8") !== first) return;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    // Joining unknown history could copy another connection's earlier text.
    if (!conversation.fresh || conversation.users.length !== 1) return;
    await mkdir(directory, { recursive: true, mode: 0o700 });
    await chmod(directory, 0o700);
    let file;
    try { file = await open(binding, "wx", 0o600); await file.writeFile(first); }
    catch (error) { if (error.code !== "EEXIST") throw error; return; }
    finally { await file?.close(); }
  }
  if (ctx.state) ctx.state.capture = { body: structuredClone(body), options: structuredClone(options || {}), scope: connectionScope(cfg, ctx),
    ...(ctx.derivedSession ? { session: ctx.session } : {}) };
  // A new human turn confirms the previous exchange even if its completion
  // callback was lost when the host stopped. Never persist the current query
  // alone as an idle-complete exchange.
  if (conversation.fresh && conversation.users.length > 1) {
    await queueCapture(cfg, ctx, conversation.messages.slice(0, conversation.latest));
  }
  launchCaptureWorker(cfg);
}

export async function finishCapture(reply, ctx, cfg) {
  if (!ctx.state?.capture || !cfg.enabled || !cfg.autoCapture || bypassedSession(cfg, ctx) || ctx.state.nativeOwner) return;
  const body = ctx.state.capture.body;
  if (ctx.state.capture.session) {
    if (!cfg.captureWithoutSession) return;
    ctx = { ...ctx, session: ctx.state.capture.session, derivedSession: true };
    if (bypassedSession(cfg, ctx)) return;
  }
  if (ctx.state.capture.scope !== connectionScope(cfg, ctx)) return;
  if (await nativeOwner(cfg, ctx, body)) return;
  const conversation = describeConversation(body, ctx.protocol);
  if (!conversation) return;
  const anchor = captureTurnAnchors(conversation.messages, ctx.protocol)[conversation.latest];
  const tail = completedReply(reply, ctx.protocol);
  if (rejectedReply(reply, ctx)) {
    await rejectCaptureTurn(dataDirectory(cfg), connectionScope(cfg, ctx), anchor);
    return;
  }
  if (!ctx.success || !tail) return;
  // A successful retry/continued tool exchange can explicitly resolve a prior
  // rejection. Merely seeing that reply in later history cannot do so.
  await withCaptureDecisions(dataDirectory(cfg), connectionScope(cfg, ctx), (state) => {
    state.rejected = state.rejected.filter((key) => key !== anchor);
  });
  await queueCapture(cfg, ctx, [...conversation.messages, ...tail]);
}
