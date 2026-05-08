/**
 * Build the openviking-copilot-mcp bin into a single ESM bundle.
 *
 * Reads package.json#version and injects it as the
 * `__OV_CLI_VERSION__` define so `--version` reports the published
 * value without runtime fs reads of package.json (which gets
 * complicated once the bin is installed via npm pack/install).
 */

import { build } from "esbuild";
import { chmodSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkgPath = join(__dirname, "..", "package.json");
const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
const outfile = join(__dirname, "..", "dist", "mcp-server.js");

await build({
  entryPoints: [join(__dirname, "..", "src", "mcp-server.ts")],
  bundle: true,
  platform: "node",
  target: "node22",
  format: "esm",
  outfile,
  // No banner: esbuild preserves the shebang on the source entry
  // already; adding a banner duplicates it.
  define: {
    __OV_CLI_VERSION__: JSON.stringify(pkg.version),
  },
});
// chmod is meaningless on Windows (NTFS doesn't track POSIX mode bits)
// and Node logs an EPERM warning when the file's owner ACL doesn't
// match. Best-effort + swallow so CI on windows-latest stays green.
if (process.platform !== "win32") {
  try {
    chmodSync(outfile, 0o755);
  } catch {
    // Build artefact is still usable; just won't be directly
    // executable. Silent failure is fine for CI.
  }
}
console.log(`built ${outfile} (v${pkg.version})`);
