import {
  computeSourceState,
  ensureRuntimeInstalled,
  getRuntimePaths,
} from "./runtime-common.mjs";

async function main() {
  const paths = getRuntimePaths();
  const expectedState = await computeSourceState(paths);

  try {
    await ensureRuntimeInstalled(paths, expectedState);
  } catch (err) {
    process.stderr.write(
      `[openviking-memory] Failed to prepare MCP runtime dependencies: ${err instanceof Error ? err.message : String(err)}\n`,
    );
  }
}

main().catch(async (err) => {
  process.stderr.write(
    `[openviking-memory] Runtime bootstrap failed: ${err instanceof Error ? err.message : String(err)}\n`,
  );
  process.exit(0);
});
