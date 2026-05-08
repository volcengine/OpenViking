#!/usr/bin/env node
/**
 * `openviking-copilot-mcp` bin entry. Stays a thin shim — the real
 * argv parsing + dispatch lives in `cli.ts` so it's unit-testable
 * under Vitest without spawning subprocesses.
 *
 * Issue #20 wires the bin + scaffolding flags. Issue #21 swaps the
 * default no-flag path from a stub message into the actual stdio
 * MCP server bootstrap.
 */

import { runMain } from "./cli.js";

const argv = process.argv.slice(2);
runMain(argv).then(
  (code) => process.exit(code),
  (err: unknown) => {
    process.stderr.write(
      `openviking-copilot-mcp: fatal: ${err instanceof Error ? err.stack ?? err.message : String(err)}\n`,
    );
    process.exit(1);
  },
);
