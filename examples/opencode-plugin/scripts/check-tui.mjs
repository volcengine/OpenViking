// Git installs this entrypoint under node_modules without running a JSX build.
import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"

const { exports, files } = JSON.parse(await readFile(new URL("../package.json", import.meta.url)))
const entry = exports["./tui"]
assert.ok(files.includes(entry.slice(2)), "Include ./tui in the package files")
const result = spawnSync("bun", ["build", fileURLToPath(new URL(`../${entry}`, import.meta.url)),
  "--target=bun", "--external=*"], { encoding: "utf8" })
if (result.error) throw result.error
assert.equal(result.status, 0, result.stderr)
assert.doesNotMatch(result.stdout, /["'](?:react|@opentui\/react)(?:\/[^"']*)?["']/)
