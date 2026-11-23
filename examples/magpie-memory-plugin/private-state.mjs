import { randomUUID } from "node:crypto";
import { chmod, mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const invalidLedger = () => Object.assign(new Error("OpenViking: recall state unavailable"), { code: "unavailable" });

/**
 * Persist decisions before exposing injected text. A session lock coordinates
 * app/CLI hosts and concurrent requests; rename keeps readers off partial JSON.
 * No keys or source queries are stored, only hashed anchors and injected blocks.
 */
export async function withPrivateState(directory, scope, signal, operation, { initial, validate, maxBytes = 1024 * 1024, leaseMs = 60000 } = {}) {
  if (!/^[a-f0-9]{64}$/.test(scope)) throw invalidLedger();
  const root = directory;
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
      // Reclaim abandoned locks only after the caller's bounded operation lease.
      try {
        if (Date.now() - (await stat(lock)).mtimeMs > leaseMs) {
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
      if ((await stat(path)).size > maxBytes) throw invalidLedger();
      before = await readFile(path, "utf8");
    } catch (error) { if (error.code !== "ENOENT") throw invalidLedger(); }
    let state;
    try { state = before ? JSON.parse(before) : initial(); }
    catch { throw invalidLedger(); }
    if (!validate(state)) throw invalidLedger();
    const result = await operation(state);
    const after = JSON.stringify(state);
    if (after !== before) {
      if (Buffer.byteLength(after) > maxBytes) throw invalidLedger();
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
