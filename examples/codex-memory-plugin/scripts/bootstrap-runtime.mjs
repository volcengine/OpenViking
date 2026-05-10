import {
  computeSourceState,
  ensureRuntimeInstalled,
  getRuntimePaths,
} from "./runtime-common.mjs";

async function main() {
  // Codex hook stdin: JSON object — we ignore it (SessionStart payload).
  // Read & discard to keep the pipe clean across platforms.
  process.stdin.resume();
  for await (const _ of process.stdin) { /* drain */ }

  let paths;
  try {
    paths = getRuntimePaths();
  } catch (err) {
    process.stderr.write(
      `[openviking-memory] CODEX_PLUGIN_ROOT not set; skipping runtime bootstrap. ${err instanceof Error ? err.message : String(err)}\n`,
    );
    return;
  }

  const expectedState = await computeSourceState(paths);

  try {
    await ensureRuntimeInstalled(paths, expectedState);
  } catch (err) {
    process.stderr.write(
      `[openviking-memory] Failed to prepare MCP runtime dependencies: ${err instanceof Error ? err.message : String(err)}\n`,
    );
  }
}

main().catch((err) => {
  process.stderr.write(
    `[openviking-memory] Runtime bootstrap failed: ${err instanceof Error ? err.message : String(err)}\n`,
  );
  process.exit(0);
});
