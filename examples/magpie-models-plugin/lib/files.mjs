import fs from "node:fs/promises";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";

export class AdapterError extends Error {
  constructor(code) { super(`OpenViking models: ${code}`); this.code = code; }
}
export async function readJSON(file) {
  try { return JSON.parse((await fs.readFile(file, "utf8")).replace(/^\uFEFF/, "")); }
  catch (error) { if (error.code === "ENOENT") return null; throw new AdapterError("invalid_json_or_file"); }
}
export async function privateDirectory(directory) {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  const stat = await fs.lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new AdapterError("unsafe_state_directory");
  await fs.chmod(directory, 0o700);
}
export async function writeJSON(file, value) {
  const temporary = join(dirname(file), `.${randomUUID()}.tmp`);
  try {
    const handle = await fs.open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(JSON.stringify(value, null, 2) + "\n");
      await handle.sync();
    } finally { await handle.close(); }
    await fs.rename(temporary, file);
  } finally { await fs.rm(temporary, { force: true }); }
}

// PID lock covers the host loop and standalone recovery CLI. A dead process's
// lock may be reclaimed; a live one is never stolen on an elapsed timeout.
export async function withLock(directory, run) {
  await privateDirectory(directory);
  const file = join(directory, "adapter.lock");
  let handle;
  for (let attempt = 0; attempt < 2; attempt++) {
    try { handle = await fs.open(file, "wx", 0o600); break; }
    catch (error) {
      if (error.code !== "EEXIST") throw error;
      // Serialize stale-lock inspections. Re-read under this guard: another
      // contender may already have replaced the dead owner's lock with its own.
      // Never reclaim this short-lived guard automatically; ambiguous recovery
      // fails closed instead of deleting a live contender's lock.
      const recoveryFile = `${file}.reclaim`;
      let recovery;
      try { recovery = await fs.open(recoveryFile, "wx", 0o600); }
      catch (cause) { if (cause.code === "EEXIST") throw new AdapterError("adapter_locked"); throw cause; }
      try {
        await recovery.writeFile(JSON.stringify({ pid: process.pid }));
        const lock = await readJSON(file);
        if (lock) {
          if (!Number.isSafeInteger(lock.pid) || lock.pid < 1) throw new AdapterError("adapter_locked");
          try { process.kill(lock.pid, 0); throw new AdapterError("adapter_locked"); }
          catch (cause) { if (cause.code !== "ESRCH") throw new AdapterError("adapter_locked"); }
          await fs.rm(file, { force: true });
        }
      } finally { await recovery.close(); await fs.rm(recoveryFile, { force: true }); }
    }
  }
  if (!handle) throw new AdapterError("adapter_locked");
  try {
    await handle.writeFile(JSON.stringify({ pid: process.pid }));
    return await run();
  } finally { await handle.close(); await fs.rm(file, { force: true }); }
}
