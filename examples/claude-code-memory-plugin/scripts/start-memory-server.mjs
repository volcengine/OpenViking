import { spawn } from "node:child_process";
import {
  computeSourceState,
  ensureRuntimeInstalled,
  getRuntimePaths,
  loadInstallState,
} from "./runtime-common.mjs";

async function main() {
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

  const child = spawn(process.execPath, [paths.runtimeServerPath], {
    cwd: paths.runtimeRoot,
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

main().catch((err) => {
  process.stderr.write(
    `[openviking-memory] MCP launcher failed: ${err instanceof Error ? err.message : String(err)}\n`,
  );
  process.exit(1);
});
