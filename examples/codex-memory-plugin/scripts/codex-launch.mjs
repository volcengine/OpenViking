import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { win32 } from "node:path";

function stripPathQuotes(value) {
  const entry = String(value || "").trim();
  if (entry.length >= 2 && entry.startsWith('"') && entry.endsWith('"')) {
    return entry.slice(1, -1);
  }
  return entry;
}

/**
 * Resolve a launch target for the Codex CLI.
 *
 * npm installs on Windows put a POSIX `codex` shim, `codex.cmd`, and the
 * package's JavaScript entry point beside each other. `spawn("codex")` may
 * select the POSIX shim and fail with EPERM, so prefer an executable or launch
 * the JavaScript entry point directly through the current Node runtime.
 */
export function resolveCodexLaunch({
  platform = process.platform,
  pathValue = process.env.PATH || "",
  execPath = process.execPath,
  pathExists = existsSync,
} = {}) {
  if (platform !== "win32") {
    return { command: "codex", argsPrefix: [] };
  }

  const entries = pathValue
    .split(";")
    .map(stripPathQuotes)
    .filter(Boolean);

  for (const entry of entries) {
    const executable = win32.join(entry, "codex.exe");
    if (pathExists(executable)) {
      return { command: executable, argsPrefix: [] };
    }

    const npmEntryPoint = win32.join(
      entry,
      "node_modules",
      "@openai",
      "codex",
      "bin",
      "codex.js",
    );
    if (pathExists(npmEntryPoint)) {
      return { command: execPath, argsPrefix: [npmEntryPoint] };
    }
  }

  // Keep the normal lookup as a last resort. The caller treats either a
  // synchronous throw or the child's asynchronous error as a compressor
  // runtime failure and preserves the deterministic recall fallback.
  return { command: "codex", argsPrefix: [] };
}

export function trySpawnCodex(args, options, {
  spawnImpl = spawn,
  resolveLaunch = resolveCodexLaunch,
} = {}) {
  const launch = resolveLaunch();
  try {
    return {
      ...launch,
      child: spawnImpl(launch.command, [...launch.argsPrefix, ...args], options),
      error: null,
    };
  } catch (error) {
    return { ...launch, child: null, error };
  }
}
