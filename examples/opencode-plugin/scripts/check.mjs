import { spawnSync } from "node:child_process"
import { readdirSync } from "node:fs"
import { resolve } from "node:path"
import { fileURLToPath } from "node:url"

const root = fileURLToPath(new URL("..", import.meta.url))

function matchingFiles(directory, suffix) {
  const files = readdirSync(resolve(root, directory))
    .filter((name) => !name.startsWith(".") && name.endsWith(suffix))
    .sort()
    .map((name) => resolve(root, directory, name))
  if (!files.length) throw new Error(`No ${suffix} files found in ${directory}`)
  return files
}

const files = [
  "index.mjs",
  ...["config", "mcp-config", "runtime", "plugin-runtime", "v2-events", "v2-plugin",
    "repo-context", "memory-session", "memory-recall", "session-inject", "viking-uri-guard", "utils"]
    .map((name) => `lib/${name}.mjs`),
  ...matchingFiles("lib/shared", ".mjs"),
  "servers/mcp-proxy.mjs",
  "scripts/setup.mjs",
  "scripts/check.mjs",
  ...matchingFiles("tests", ".test.mjs"),
]

for (const file of files) {
  const result = spawnSync(process.execPath, ["--check", resolve(root, file)], { stdio: "inherit" })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
