// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { createHash } from "node:crypto";
import { mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

import { createLogger } from "./debug-log.mjs";
import { sendSessionMessages } from "./batch-send.mjs";
import { createOvHttp } from "./ov-http.mjs";
import { enqueue, replayPending } from "./pending-queue.mjs";
import { buildProfileBlock } from "./profile-inject.mjs";
import { buildRecallBlock, isRecallEnabled } from "./recall-core.mjs";
import { buildPluginConfig } from "./plugin-config.mjs";
import { isRetryableFailure } from "./retryable.mjs";
import { deriveHarnessSessionId, isBypassed } from "./session-model.mjs";
import { resolveEffectivePeerId } from "./workspace-peer.mjs";

const STATE_VERSION = 1;
const STATE_DIR_MODE = 0o700;
const STATE_FILE_MODE = 0o600;

function safePart(value) {
  return String(value || "unknown").replace(/[^A-Za-z0-9._-]/g, "-");
}

export function stableHash(...values) {
  return createHash("sha256")
    .update(values.map((value) => String(value ?? "")).join("\n"))
    .digest("hex");
}

/**
 * The config for one of the thin hook harnesses.
 *
 * These four used to read the environment and nothing else, so `ov config
 * switch` moved their credentials and left their behaviour behind, and an
 * `ovcli.conf` `plugin` entry named after them was inert. They resolve through
 * the same layers as every other harness now; only the client's own name for
 * itself stays local.
 *
 * `cwd` selects the workspace layer. It defaults to this process's directory,
 * which is all a hook knows before the payload on stdin names the session's
 * own — the caller re-resolves once it has it. That is safe because a workspace
 * file may not carry connection or credential keys, so the base URL and API key
 * cannot move under a logger or fetch helper already built from the first load.
 */
export function loadAgentHookConfig(clientId, cwd = process.cwd()) {
  return {
    ...buildPluginConfig(clientId, {
      cwd,
      version: process.env.OPENVIKING_INTEGRATION_VERSION,
      logFile: `${clientId}-hooks.log`,
    }),
    clientId,
  };
}

export function createAgentLogger(clientId, hookName, cfg) {
  return createLogger(`${clientId}:${hookName}`, cfg);
}

export async function readRawHookInput() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString();
}

export async function readHookInput() {
  const raw = await readRawHookInput();
  if (!raw.trim()) return {};
  try { return JSON.parse(raw); } catch { return {}; }
}

export function resolveAgentCwd(input = {}) {
  const workspaceRoots = Array.isArray(input.workspace_roots)
    ? input.workspace_roots
    : Array.isArray(input.workspaceRoots) ? input.workspaceRoots : [];
  return String(
    input.cwd
      || workspaceRoots.find((value) => typeof value === "string" && value.trim())
      || process.env.CURSOR_PROJECT_DIR
      || process.cwd(),
  );
}

export function resolveNativeSessionId(input = {}) {
  const direct = input.conversation_id || input.session_id || input.sessionId || input.generation_id;
  if (direct) return safePart(direct);
  const transcript = input.transcript_path || input.transcriptPath;
  if (transcript) {
    const match = String(transcript).match(/([0-9a-f]{8}-[0-9a-f-]{20,})/i);
    return safePart(match?.[1] || stableHash(transcript).slice(0, 24));
  }
  const cwd = resolveAgentCwd(input);
  return `cwd-${stableHash(cwd).slice(0, 20)}`;
}

export function deriveAgentSessionId(prefix, input = {}) {
  return deriveHarnessSessionId(prefix, resolveNativeSessionId(input));
}

function statePath(clientId, nativeSessionId) {
  const root = process.env.OPENVIKING_HOOK_STATE_DIR
    || join(homedir(), ".openviking", "hook-state");
  return join(root, safePart(clientId), `${safePart(nativeSessionId)}.json`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function withAgentHookLock(clientId, nativeSessionId, callback) {
  const file = statePath(clientId, nativeSessionId);
  const lock = `${file}.lock`;
  await mkdir(dirname(file), { recursive: true, mode: STATE_DIR_MODE });
  const deadline = Date.now() + 5000;
  while (true) {
    try {
      await mkdir(lock, { mode: STATE_DIR_MODE });
      break;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      try {
        if (Date.now() - (await stat(lock)).mtimeMs > 60_000) {
          await rm(lock, { recursive: true, force: true });
          continue;
        }
      } catch {}
      if (Date.now() >= deadline) return null;
      await sleep(50);
    }
  }
  try {
    return await callback();
  } finally {
    await rm(lock, { recursive: true, force: true }).catch(() => {});
  }
}

export async function readHookState(clientId, nativeSessionId) {
  try {
    const parsed = JSON.parse(await readFile(statePath(clientId, nativeSessionId), "utf8"));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export async function writeHookState(clientId, nativeSessionId, value) {
  const file = statePath(clientId, nativeSessionId);
  await mkdir(dirname(file), { recursive: true, mode: STATE_DIR_MODE });
  const tmp = `${file}.${process.pid}.tmp`;
  await writeFile(tmp, `${JSON.stringify({ version: STATE_VERSION, ...value }, null, 2)}\n`, {
    encoding: "utf8",
    mode: STATE_FILE_MODE,
  });
  await rename(tmp, file);
}

/**
 * The `fetchJSON` a hook talks through.
 *
 * `getActorPeerId` is a getter rather than a value because Codex only knows its
 * peer after loading session state under the session lock, and because a stack
 * whose every call names its own peer answers with the empty string. The
 * workspace peer behind the default is resolved on demand so those callers do
 * not pay for a lookup they never read.
 */
export function makeAgentFetchJSON(cfg, cwd = process.cwd(), {
  defaultTimeoutMs,
  getActorPeerId,
  requireJsonBody = false,
} = {}) {
  let resolved = null;
  const workspacePeer = () => (resolved ??= resolveEffectivePeerId({ cfg, cwd }));
  const fetchJSON = createOvHttp(cfg, {
    defaultTimeoutMs: defaultTimeoutMs ?? cfg.timeoutMs,
    resolveActorPeerId: getActorPeerId || (() => workspacePeer().peerId),
    requireJsonBody,
  });
  return { fetchJSON, get effectivePeer() { return workspacePeer(); } };
}

/** Park a write for the next hook to replay. Never throws: the caller is a hook. */
export async function enqueueAgentPending(type, sessionId, payload = {}) {
  try {
    return await enqueue(type, sessionId, payload);
  } catch {
    return { ok: false };
  }
}

/**
 * What became of a write that failed: parked for replay, or reported once on
 * stderr when no retry can help. `pendingQueued` / `pendingEnqueueFailed` are
 * what the capture hooks log and what decides whether a turn is lost.
 */
async function notePendingWrite(type, sessionId, payload, result) {
  if (result.ok) return result;
  if (!isRetryableFailure(result)) {
    const detail = result.error?.message || result.error?.code || "";
    process.stderr.write(
      `[ov] ${type} failed with non-retryable status ${result.status || "unknown"};`
        + ` not enqueuing pending retry${detail ? ` (${detail})` : ""}\n`,
    );
    return result;
  }
  const pending = await enqueueAgentPending(type, sessionId, payload);
  if (pending.ok) result.pendingQueued = true;
  else result.pendingEnqueueFailed = true;
  return result;
}

export async function addAgentMessage(fetchJSON, sessionId, payload) {
  const result = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return notePendingWrite("addMessage", sessionId, payload, result);
}

export async function addAgentMessages(fetchJSON, sessionId, payloads) {
  return sendSessionMessages(fetchJSON, sessionId, payloads, { enqueueOnRetryable: true });
}

export async function commitAgentSession(fetchJSON, sessionId, log = () => {}, payload = {}) {
  const body = payload || {};
  const result = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  await notePendingWrite("commitSession", sessionId, body, result);
  log("commit", {
    sessionId,
    ok: result.ok,
    status: result.result?.status || result.status,
    trace_id: result.traceId || result.result?.trace_id,
    queued: Boolean(result.pendingQueued),
    error: result.ok ? undefined : result.error?.message || result.error?.code,
  });
  return result;
}

/** Session meta, or null when the session does not exist and `autoCreate` is off. */
export async function getAgentSession(fetchJSON, sessionId, { autoCreate = false } = {}) {
  const query = autoCreate ? "?auto_create=true" : "";
  const result = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}${query}`);
  return result.ok ? result.result : null;
}

/** Assembled session context (includes latest_archive_overview), or null. */
export async function getAgentSessionContext(fetchJSON, sessionId, tokenBudget = 128000) {
  const result = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
  );
  return result.ok ? result.result : null;
}

export async function replayAgentPending(fetchJSON, log = () => {}) {
  return replayPending(fetchJSON, log);
}

export async function recallForPrompt(fetchJSON, cfg, prompt, cwd, log = () => {}, options = {}) {
  if (!isRecallEnabled(cfg) || !String(prompt || "").trim()) return null;
  const peer = resolveEffectivePeerId({ cfg, cwd });
  return buildRecallBlock(fetchJSON, cfg, prompt, {
    actorPeerId: peer.peerId,
    legacyPeerId: peer.legacyPeerId,
    // Passing the OV session id is what turns on server-side query expansion
    // and the cross-turn dedup ledger for these thin harnesses.
    sessionId: options.sessionId || "",
    log,
  });
}

export async function buildAgentProfile(fetchJSON, cfg, cwd) {
  const peer = resolveEffectivePeerId({ cfg, cwd });
  const profile = await buildProfileBlock(fetchJSON, cfg.profileTokenBudget, peer.peerId);
  return profile?.block || null;
}

export function shouldBypassAgent(cfg, input = {}) {
  return isBypassed(cfg, { sessionId: resolveNativeSessionId(input), cwd: resolveAgentCwd(input) });
}

/**
 * The opening every hook entry shares: read the payload, re-resolve the config
 * against the session's directory, answer the two gates, then run the hook's
 * own work and emit one envelope.
 *
 * `run` receives the resolved stage and returns the envelope's payload — a
 * systemMessage, a context block, or nothing. A closed gate calls
 * `onSkip(reason, stage)` with `"bad_stdin"`, `"disabled"` or `"bypass"`, emits
 * an empty envelope and never runs the callback. `run` may emit early through
 * `stage.emit` (a detaching hook has to answer before its worker starts); the
 * envelope is written once.
 *
 * Both gates are predicates over `(cfg, stage)`: `enabled` says whether this
 * hook runs at all, `bypass` whether this session is one the plugin stays out
 * of. `sessionId` reads the session out of the payload — the default is the key
 * Claude Code and Codex send, and a host that spells it differently, or derives
 * it, hands over its own resolver rather than turning the gate off.
 *
 * The write-path preamble — the enabled gate against this process's directory,
 * then `maybeDetach` — stays in the entry. A worker has to be spawned before
 * stdin is consumed, and its response is the host's own.
 */
export async function runHookStage({
  clientId,
  loadConfig = (cwd) => loadAgentHookConfig(clientId, cwd),
  input: { read = readRawHookInput, tolerant = false } = {},
  sessionId: resolveSessionId = (payload) => payload.session_id ?? payload.sessionId,
  gates: { enabled = null, bypass = (cfg, stage) => stage.bypassed } = {},
  envelope = () => {},
  onSkip = () => {},
} = {}, run = () => undefined) {
  let emitted = false;
  const emit = (value) => {
    if (emitted) return;
    emitted = true;
    envelope(value);
  };

  let badStdin = false;
  const raw = await read();
  let payload;
  try {
    payload = JSON.parse(tolerant ? raw || "{}" : raw);
  } catch {
    badStdin = !tolerant;
    payload = {};
  }
  if (!payload || typeof payload !== "object") payload = {};

  const cwd = resolveAgentCwd(payload);
  const cfg = loadConfig(cwd);
  const sessionId = resolveSessionId(payload);
  const stage = {
    cfg,
    input: payload,
    raw,
    cwd,
    sessionId,
    bypassed: isBypassed(cfg, { sessionId, cwd }),
    emit,
  };

  if (badStdin) {
    onSkip("bad_stdin", stage);
    emit();
    return undefined;
  }
  if (enabled && !enabled(cfg, stage)) {
    onSkip("disabled", stage);
    emit();
    return undefined;
  }
  if (bypass && bypass(cfg, stage)) {
    onSkip("bypass", stage);
    emit();
    return undefined;
  }

  const result = await run(stage);
  emit(result);
  return result;
}
