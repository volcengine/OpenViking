import { access, chmod, mkdir, open, readdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { connectionScope, dataDirectory } from "./scope.mjs";
import { describeConversation, hasNativeContext, isAuxiliaryRequest } from "./conversation.mjs";
import { captureMessages, completedReply } from "./capture-messages.mjs";
import { recallDigest } from "./recall-ledger.mjs";

export const captureDirectory = (cfg, ctx) => join(dataDirectory(cfg), "capture", connectionScope(cfg, ctx));
const workers = new Map();
export async function nativeOwner(cfg, ctx, body) {
  const directory = captureDirectory(cfg, ctx);
  const marker = join(directory, "native-owner");
  if (cfg.nativeAgents.includes(ctx.agent) || hasNativeContext(body, ctx.protocol)) {
    await mkdir(directory, { recursive: true, mode: 0o700 });
    await chmod(directory, 0o700);
    const file = await open(marker, "a", 0o600);
    await file.close();
    return true;
  }
  try { await access(marker); return true; }
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
  const payloads = captureMessages(messages, ctx.protocol, cfg);
  if (!payloads?.length || payloads.length > 2000 || payloads.at(-1).role !== "assistant" || payloads.at(-1).message_kind !== "assistant_step") return false;
  const scope = connectionScope(cfg, ctx);
  const directory = captureDirectory(cfg, ctx);
  const pending = join(directory, "pending");
  await mkdir(pending, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700);
  await chmod(pending, 0o700);
  if ((await readdir(pending)).filter((name) => name.endsWith(".json")).length >= 32) return false;
  const user = payloads.findLast((m) => m.message_kind === "user_query");
  if (!user) return false;
  const turnKey = user.source_message_ids[0].slice(7);
  const snapshot = { version: 1, scope, configPath: cfg.path, context: { agent: ctx.agent, session: ctx.session, cwd: ctx.cwd, protocol: ctx.protocol },
    options: Object.hasOwn(ctx.options || {}, "autoCapture") ? { autoCapture: cfg.autoCapture } : {}, turnKey, messages: payloads };
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
    try { await link(staging, path); } catch (error) { if (error.code !== "EEXIST") throw error; }
  } finally { await rm(staging, { force: true }); }
  if (launch) launchCaptureWorker(cfg);
  return true;
}

export async function prepareCapture(body, ctx, cfg, options) {
  if (!cfg.enabled || !["local", "magpie-key"].includes(ctx.origin) || !ctx.session?.trim()) return;
  if (isAuxiliaryRequest(body, ctx)) return;
  if (await nativeOwner(cfg, ctx, body)) { ctx.state && (ctx.state.nativeOwner = true); return; }
  if (!cfg.autoCapture) return;
  const conversation = describeConversation(body, ctx.protocol);
  if (!conversation) return;
  const directory = captureDirectory(cfg, ctx);
  const binding = join(directory, "start");
  const first = captureMessages([conversation.messages[conversation.users[0]]], ctx.protocol, cfg)?.[0]?.source_message_ids[0];
  if (!first) return;
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
  if (ctx.state) ctx.state.capture = { body: structuredClone(body), options: structuredClone(options || {}), scope: connectionScope(cfg, ctx) };
  // A new human turn confirms the previous exchange even if its completion
  // callback was lost when the host stopped. Never persist the current query
  // alone as an idle-complete exchange.
  if (conversation.fresh && conversation.users.length > 1) {
    await queueCapture(cfg, ctx, conversation.messages.slice(0, conversation.latest));
  }
  launchCaptureWorker(cfg);
}

export async function finishCapture(reply, ctx, cfg) {
  if (!ctx.success || !ctx.state?.capture || !cfg.enabled || !cfg.autoCapture || ctx.state.nativeOwner) return;
  const body = ctx.state.capture.body;
  if (ctx.state.capture.scope !== connectionScope(cfg, ctx)) return;
  if (await nativeOwner(cfg, ctx, body)) return;
  const conversation = describeConversation(body, ctx.protocol);
  const tail = completedReply(reply, ctx.protocol);
  if (!conversation || !tail) return;
  await queueCapture(cfg, ctx, [...conversation.messages, ...tail]);
}
