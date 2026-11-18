// OpenCode supplies Solid/OpenTUI at runtime; syntax-check without bundling them.
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"

const output = await mkdtemp(join(tmpdir(), "ov-tui-build-"))
try {
  const result = spawnSync("bun", ["build", fileURLToPath(new URL("../tui.tsx", import.meta.url)),
    "--target=bun", "--external=*", `--outfile=${join(output, "tui.js")}`], { stdio: "inherit" })
  if (result.error) throw result.error
  process.exitCode = result.status ?? 1
} finally { await rm(output, { recursive: true, force: true }) }
