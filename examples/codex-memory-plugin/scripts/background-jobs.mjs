import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

export function startDetachedScript(scriptName, args = []) {
  const scriptPath = join(SCRIPT_DIR, scriptName);
  const child = spawn(process.execPath, [scriptPath, ...args], {
    detached: true,
    env: process.env,
    stdio: "ignore",
  });
  child.unref();
  return child.pid || null;
}
