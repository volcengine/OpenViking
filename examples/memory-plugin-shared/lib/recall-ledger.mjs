import { createHash, randomUUID } from "node:crypto";
import { chmod, mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

export const recallDigest = (value) => createHash("sha256").update(value).digest("hex");

/** Object key order does not identify a different message; array order does. */
export function canonicalRecallValue(value) {
  if (Array.isArray(value)) return value.map(canonicalRecallValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalRecallValue(value[key])]));
  }
  return value;
}

/** Prefix anchors distinguish repeated text, branches and replaced histories. */
export function recallAnchors(messages, prefix = {}) {
  const hash = createHash("sha256").update(JSON.stringify(canonicalRecallValue(prefix)));
  return messages.map((message) => {
    hash.update("\0").update(JSON.stringify(canonicalRecallValue(message)));
    return hash.copy().digest("hex");
  });
}

const MAX_BYTES = 1024 * 1024;
const invalidLedger = () => Object.assign(new Error("OpenViking: recall state unavailable"), { code: "unavailable" });

/**
 * Persist decisions before exposing injected text. A session lock coordinates
 * app/CLI hosts and concurrent requests; rename keeps readers off partial JSON.
 * No keys or source queries are stored, only hashed anchors and injected blocks.
 */
export async function withRecallLedger(directory, scope, signal, operation) {
  if (!/^[a-f0-9]{64}$/.test(scope)) throw invalidLedger();
  const root = join(directory, "recall");
  const path = join(root, `${scope}.json`);
  const lock = `${path}.lock`;
  const token = randomUUID();
  const started = Date.now();
  await mkdir(root, { recursive: true, mode: 0o700 });
  await chmod(root, 0o700);
  while (true) {
    signal?.throwIfAborted();
    let created = false;
    try {
      await mkdir(lock, { mode: 0o700 });
      created = true;
      await writeFile(join(lock, "owner"), token, { mode: 0o600 });
      break;
    } catch (error) {
      if (created) await rm(lock, { recursive: true, force: true });
      if (error.code !== "EEXIST") throw invalidLedger();
      // Host operations are bounded to at most ten seconds. A retired host's
      // lock may be reclaimed after a minute; the old owner cannot commit.
      try {
        if (Date.now() - (await stat(lock)).mtimeMs > 60000) {
          const retired = `${lock}.${randomUUID()}`;
          await rename(lock, retired);
          await rm(retired, { recursive: true, force: true });
          continue;
        }
      } catch (error) { if (error.code !== "ENOENT") throw invalidLedger(); }
      if (Date.now() - started > 900) throw invalidLedger();
      await delay(10, undefined, { signal });
    }
  }
  const staging = `${path}.${token}.tmp`;
  try {
    let before = "";
    try {
      if ((await stat(path)).size > MAX_BYTES) throw invalidLedger();
      before = await readFile(path, "utf8");
    } catch (error) { if (error.code !== "ENOENT") throw invalidLedger(); }
    let state;
    try { state = before ? JSON.parse(before) : { version: 1, decisions: {} }; }
    catch { throw invalidLedger(); }
    if (state?.version !== 1 || !state.decisions || typeof state.decisions !== "object" || Array.isArray(state.decisions)) throw invalidLedger();
    if (Object.entries(state.decisions).some(([anchor, decision]) => !/^[a-f0-9]{64}$/.test(anchor)
      || !decision || typeof decision.block !== "string"
      || !["ready", "empty", "skipped", "unauthorized", "invalid", "unavailable"].includes(decision.outcome))) throw invalidLedger();
    const result = await operation(state.decisions);
    const after = JSON.stringify(state);
    if (after !== before) {
      if (Buffer.byteLength(after) > MAX_BYTES) throw invalidLedger();
      signal?.throwIfAborted();
      await writeFile(staging, after, { mode: 0o600, flag: "wx" });
      if (await readFile(join(lock, "owner"), "utf8") !== token) throw invalidLedger();
      await rename(staging, path);
    }
    return result;
  } finally {
    await rm(staging, { force: true });
    try {
      if (await readFile(join(lock, "owner"), "utf8") === token) await rm(lock, { recursive: true, force: true });
    } catch { /* Another host reclaimed a retired lease. */ }
  }
}
