import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import {
  computeSourceState,
  ensureRuntimeInstalled,
  getRuntimePaths,
  loadInstallState,
} from "./runtime-common.mjs";

const FALLBACK_PLUGIN_DATA_ROOT = join(homedir(), ".openviking", "claude-code-memory-plugin");

// When Claude Code spawns the MCP server it substitutes `${CLAUDE_PLUGIN_ROOT}` in
// `args` but not inside `env` values, and as of Claude Code 2.1.x does not auto-
// propagate CLAUDE_PLUGIN_ROOT into the MCP child env. If the runtime is already
// installed, we do not actually need the source tree to launch it — we only need
// the runtime directory and the compiled server file. Try that path first so the
// MCP keeps working even when CLAUDE_PLUGIN_ROOT is missing.
async function tryLaunchReadyRuntime() {
  const pluginDataRoot = process.env.CLAUDE_PLUGIN_DATA || FALLBACK_PLUGIN_DATA_ROOT;
  const runtimeRoot = join(pluginDataRoot, "runtime");
  const statePath = join(runtimeRoot, "install-state.json");
  const serverPath = join(runtimeRoot, "servers", "memory-server.js");

  try {
    const state = JSON.parse(await readFile(statePath, "utf-8"));
    if (state?.status !== "ready") return null;
    await access(serverPath);
    return { runtimeRoot, serverPath };
  } catch {
    return null;
  }
}

function runServer(runtimeRoot, serverPath) {
  const child = spawn(process.execPath, [serverPath], {
    cwd: runtimeRoot,
    env: process.env,
    stdio: "inherit",
  });

  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.on(signal, () => {
      if (!child.killed) child.kill(signal);
    });
  }

  child.on("error", (err) => {
    process.stderr.write(
      `[openviking-memory] Failed to start MCP server: ${err instanceof Error ? err.message : String(err)}\n`,
    );
    process.exit(1);
  });

  child.on("exit", (code) => {
    process.exit(code ?? 1);
  });
}

async function main() {
  if (!process.env.CLAUDE_PLUGIN_ROOT) {
    const ready = await tryLaunchReadyRuntime();
    if (ready) {
      runServer(ready.runtimeRoot, ready.serverPath);
      return;
    }
    process.stderr.write(
      "[openviking-memory] CLAUDE_PLUGIN_ROOT is not set and no ready runtime was found. Start a new Claude Code session so the SessionStart hook can install the runtime.\n",
    );
    process.exit(1);
    return;
  }

  const paths = getRuntimePaths();
  const expectedState = await computeSourceState(paths);

  try {
    await ensureRuntimeInstalled(paths, expectedState);
  } catch (err) {
    const state = await loadInstallState(paths);
    const detail = state?.error ? ` Last install error: ${state.error}` : "";
    process.stderr.write(
      `[openviking-memory] MCP runtime is not ready in ${paths.runtimeRoot}.${detail}\n`,
    );
    process.exit(1);
    return;
  }

  runServer(paths.runtimeRoot, paths.runtimeServerPath);
}

main().catch((err) => {
  process.stderr.write(
    `[openviking-memory] MCP launcher failed: ${err instanceof Error ? err.message : String(err)}\n`,
  );
  process.exit(1);
});
