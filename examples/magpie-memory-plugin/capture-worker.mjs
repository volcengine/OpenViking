import { access, readFile, readdir, rm, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { realpathSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { loadConfig } from "./config.mjs";
import { request } from "./http.mjs";
import { sendSessionMessages } from "./shared/batch-send.mjs";
import { bypassedSession, connectionScope } from "./scope.mjs";
import { recallDigest } from "./recall-ledger.mjs";
import { withCaptureDecisions } from "./capture-history.mjs";
import { withPrivateState } from "./private-state.mjs";

const hash = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const stateOptions = {
  initial: () => ({ version: 1, hashes: [], completedTurns: [], intent: null, needsCommit: false, commitIntent: null, blocked: "" }),
  validate: (s) => s?.version === 1 && Array.isArray(s.hashes) && s.hashes.every(hash)
    && (s.intent === null || (Array.isArray(s.intent.ids) && s.intent.ids.every((id) => typeof id === "string")
      && Array.isArray(s.intent.hashes) && s.intent.hashes.every(hash))) && typeof s.blocked === "string"
    && (s.completedTurns === undefined || (Array.isArray(s.completedTurns) && s.completedTurns.every(hash))),
};
async function exists(path) {
  try { await access(path); return true; } catch (e) { if (e.code !== "ENOENT") throw e; return false; }
}

/** Reconcile lost acknowledgements without resending an uncertain append.
 * Source IDs are provenance, not server-side idempotency keys. An append whose
 * result cannot be proven stays pending rather than risking duplicate memory.
 */
async function receipts(cfg, uri, call, signal) {
  const ids = new Set();
  const read = async (path) => {
    let content;
    try { content = await call(cfg, `/api/v1/content/read?${new URLSearchParams({ uri: path, raw: "true", limit: "-1" })}`, { signal }); }
    catch (error) { if (error.status === 404) return; throw error; }
    if (typeof content !== "string" || Buffer.byteLength(content) > 4 * 1024 * 1024) throw new Error("capture_receipt_invalid");
    for (const line of content.split("\n").filter((line) => line.trim())) {
      const message = JSON.parse(line);
      for (const id of message.source_message_ids || []) ids.add(id);
    }
  };
  await read(`${uri}/messages.jsonl`);
  let entries;
  try { entries = await call(cfg, `/api/v1/fs/ls?${new URLSearchParams({ uri: `${uri}/history`, output: "original", node_limit: "33" })}`, { signal }); }
  catch (error) { if (error.status !== 404) throw error; entries = []; }
  if (!Array.isArray(entries) || entries.length > 32) throw new Error("capture_receipt_limit");
  for (const entry of entries) {
    if (entry.isDir && /^archive_\d+$/.test(entry.name)) await read(`${uri}/history/${entry.name}/messages.jsonl`);
  }
  return ids;
}

async function commitCaptured(cfg, sessionId, update, call, signal, stopped) {
  const stop = await stopped();
  if (stop) return stop;
  const state = await update((s) => structuredClone(s));
  if (!state.needsCommit && !state.commitIntent) return "delivered";
  const path = `/api/v1/sessions/${sessionId}`;
  const meta = await call(cfg, path, { signal });
  if (meta.message_count === 0 || (state.commitIntent && meta.commit_count > state.commitIntent.beforeCount)) {
    await update((s) => { s.needsCommit = false; s.commitIntent = null; });
    return "delivered";
  }
  if (state.commitIntent) return "uncertain";
  await update((s) => { s.commitIntent = { beforeCount: Number(meta.commit_count || 0) }; });
  const stopBeforeCommit = await stopped();
  if (stopBeforeCommit) {
    // This intent is known not to have reached the server.
    await update((s) => { s.commitIntent = null; });
    return stopBeforeCommit;
  }
  const result = await call(cfg, `${path}/commit`, { body: { keep_recent_count: 0 }, signal });
  await update((s) => {
    s.needsCommit = false; s.commitIntent = null;
    s.archiveUri = typeof result?.archive_uri === "string" ? result.archive_uri : "";
    s.extractionTaskId = typeof result?.task_id === "string" ? result.task_id : "";
  });
  return "delivered";
}

export async function deliverSnapshot(directory, snapshot, { call = request, signal = AbortSignal.timeout(20000) } = {}) {
  const { scope, context } = snapshot;
  if (!hash(scope) || !hash(snapshot.turnKey) || !Array.isArray(snapshot.messages) || snapshot.messages.length > 2000) throw new Error("capture_snapshot_invalid");
  const cfg = await loadConfig({ OPENVIKING_CLI_CONFIG_FILE: snapshot.configPath }, snapshot.options);
  if (connectionScope(cfg, context) !== scope) return "paused";
  const root = join(directory, "capture", scope);
  const stopped = async () => {
    // A worker may outlive file edits. Recheck identity and opt-in immediately
    // before each non-idempotent operation, without changing its pinned identity.
    const current = await loadConfig({ OPENVIKING_CLI_CONFIG_FILE: snapshot.configPath }, snapshot.options);
    if (connectionScope(current, context) !== scope || !current.enabled || !current.autoCapture || bypassedSession(current, context)) return "paused";
    if (current.nativeAgents.includes(context.agent) || await exists(join(root, "native-owner"))) return "native";
    return withCaptureDecisions(directory, scope, (state) => {
      if (!Array.isArray(snapshot.turnAnchors) && state.rejected.length) return "rejected";
      const invalidated = snapshot.turnAnchors?.some((key) => state.rejected.includes(key)
        || (snapshot.turnRevisions?.[key] || 0) !== (state.revisions[key] || 0));
      return invalidated ? "rejected" : "";
    });
  };
  const stop = await stopped();
  if (stop) return stop;
  if (!cfg.enabled || !cfg.autoCapture) return "paused";
  const stateDir = join(root, "state");
  const update = (fn) => withPrivateState(stateDir, scope, signal, fn, stateOptions);
  let state = await update((s) => structuredClone(s));
  const sessionId = `magpie-${scope}`;
  if (state.completedTurns?.includes(snapshot.turnKey)) return commitCaptured(cfg, sessionId, update, call, signal, stopped);
  if (state.commitIntent) {
    const status = await commitCaptured(cfg, sessionId, update, call, signal, stopped);
    if (status !== "delivered") return status;
    state = await update((s) => structuredClone(s));
  }
  if (state.blocked) return "paused";
  let meta;
  try { meta = await call(cfg, `/api/v1/sessions/${sessionId}`, { signal }); }
  catch (error) {
    if (error.status !== 404) throw error;
    const stop = await stopped();
    if (stop) return stop;
    try {
      await call(cfg, "/api/v1/sessions", { signal, body: { session_id: sessionId,
        auto_commit_policy: null,
        memory_policy: { working_memory: { enabled: false } },
      } });
    } catch (error) { if (error.status !== 409) throw error; }
    meta = await call(cfg, `/api/v1/sessions/${sessionId}`, { signal });
  }
  if (typeof meta.uri !== "string" || !meta.uri.startsWith("viking://")) throw new Error("capture_session_invalid");
  const incoming = snapshot.messages.map((m) => m.source_message_ids?.[0]?.replace(/^magpie:/, ""));
  if (!incoming.every(hash)) throw new Error("capture_snapshot_invalid");
  if (state.intent) {
    const received = await receipts(cfg, meta.uri, call, signal);
    if (!state.intent.ids.every((id) => received.has(id))) return "uncertain";
    await update((s) => {
      s.hashes = s.intent.hashes;
      s.completedTurns = [...new Set([...(s.completedTurns || []), ...(s.intent.completedTurns || [])])];
      s.needsCommit ||= Boolean(s.intent.completedTurns?.length);
      s.intent = null;
    });
    state = await update((s) => structuredClone(s));
  }
  if (!state.hashes.length && Number(meta.total_message_count || meta.message_count) > 0) {
    const received = await receipts(cfg, meta.uri, call, signal);
    let count = 0;
    while (count < incoming.length && received.has(`magpie:${incoming[count]}`)) count++;
    if (!count) return "uncertain";
    const prefix = new Set(incoming.slice(0, count).map((id) => `magpie:${id}`));
    if ([...received].some((id) => id.startsWith("magpie:") && !prefix.has(id))) return "uncertain";
    await update((s) => { s.hashes = incoming.slice(0, count); s.needsCommit ||= meta.message_count > 0; });
    state.hashes = incoming.slice(0, count);
  }
  if (incoming.length <= state.hashes.length && incoming.every((id, i) => id === state.hashes[i])) return commitCaptured(cfg, sessionId, update, call, signal, stopped);
  if (!state.hashes.every((id, i) => id === incoming[i])) {
    await update((s) => { s.blocked = "history_changed"; });
    return "paused";
  }
  let offset = state.hashes.length, stopReason = "";
  try {
    const sent = await sendSessionMessages(async (path, init) => {
      stopReason = await stopped();
      if (stopReason) throw new Error("capture_stopped");
      const body = JSON.parse(init.body);
      const messages = Array.isArray(body.messages) ? body.messages : [body];
      const through = incoming.slice(0, offset + messages.length);
      const completedTurns = through.length === incoming.length
        ? snapshot.messages.filter((m) => m.message_kind === "user_query").map((m) => m.source_message_ids[0].slice(7)) : [];
      // Persist one intent per actual HTTP operation, including serial fallback.
      await update((s) => { s.intent = { ids: messages.map((m) => m.source_message_ids[0]), hashes: through, completedTurns }; });
      stopReason = await stopped();
      if (stopReason) {
        await update((s) => { s.intent = null; });
        throw new Error("capture_stopped");
      }
      try {
        await call(cfg, path, { body, signal });
      } catch (error) {
        // Explicit client errors did not accept this append. In particular,
        // unsupported batches can safely fall back; uncertain transport/server
        // outcomes keep their intent for provenance reconciliation.
        if (error.status >= 400 && error.status < 500 && error.status !== 408) {
          await update((s) => { s.intent = null; });
        }
        return { ok: false, status: error.status, error };
      }
      await update((s) => {
        s.hashes = through;
        s.completedTurns = [...new Set([...(s.completedTurns || []), ...completedTurns])];
        s.needsCommit ||= completedTurns.length > 0;
        s.intent = null;
      });
      offset += messages.length;
      return { ok: true };
    }, sessionId, snapshot.messages.slice(offset));
    if (sent.failed) return "uncertain";
  } catch (error) {
    if (stopReason) return stopReason;
    throw error;
  }
  return commitCaptured(cfg, sessionId, update, call, signal, stopped);
}

export async function drainCapture(directory, { call = request, signal = AbortSignal.timeout(25000) } = {}) {
  const root = join(directory, "capture");
  const scopes = (await readdir(root).catch(() => [])).filter(hash);
  let pending = false;
  for (const scope of scopes) {
    signal.throwIfAborted();
    const location = join(root, scope, "pending");
    const files = (await readdir(location).catch(() => [])).filter((f) => /^[a-f0-9]{64}\.json$/.test(f));
    const snapshots = [];
    for (const file of files) {
      const path = join(location, file);
      try {
        if ((await stat(path)).size > 3 * 1024 * 1024) continue;
        const encoded = await readFile(path, "utf8");
        const value = JSON.parse(encoded);
        if (value.version === 1 && Array.isArray(value.messages)) snapshots.push({ path, value, encoded });
      } catch { /* Keep corrupt evidence for diagnosis. */ }
    }
    snapshots.sort((a, b) => a.value.messages.length - b.value.messages.length);
    for (const snapshot of snapshots) {
      signal.throwIfAborted();
      try {
        if (snapshot.value.scope !== scope) throw new Error("capture_scope_invalid");
        const status = await deliverSnapshot(directory, snapshot.value, { call, signal });
        if (["delivered", "native", "rejected"].includes(status)) {
          // Publication and rejection cleanup use the same lock. Never unlink
          // a successful retry that replaced the file this worker originally read.
          const replaced = await withCaptureDecisions(directory, scope, async () => {
            let current;
            try { current = await readFile(snapshot.path, "utf8"); }
            catch (error) { if (error.code === "ENOENT") return false; throw error; }
            if (current !== snapshot.encoded) return true;
            await rm(snapshot.path, { force: true });
            return false;
          });
          pending ||= replaced;
        } else pending = true;
      } catch { pending = true; }
    }
  }
  return pending;
}

export async function runCaptureWorker(directory, { call = request, signal = AbortSignal.timeout(25000), attempts = 4 } = {}) {
  // Only one detached worker drains this private directory at a time.
  await withPrivateState(join(directory, "capture-workers"), recallDigest(directory), signal, async () => {
    for (let attempt = 0; attempt < attempts; attempt++) {
      if (!(await drainCapture(directory, { call, signal }))) break;
      if (attempt + 1 < attempts) await delay(1000 * (attempt + 1), undefined, { signal });
    }
  }, { initial: () => ({}), validate: (s) => s && typeof s === "object", leaseMs: 60000 });
}

function isDirectRun() {
  try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return false; }
}

if (isDirectRun()) {
  runCaptureWorker(process.argv[2]).catch(() => { process.exitCode = 1; });
}
