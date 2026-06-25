import test from "node:test"
import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"

const testDir = dirname(fileURLToPath(import.meta.url))
const memoryToolsPath = join(testDir, "../lib/memory-tools.mjs")

test("memwrite uses content/write with safe defaults and peer propagation", async () => {
  const source = await readFile(memoryToolsPath, "utf8")

  assert.match(source, /memwrite: tool\(/)
  assert.match(source, /endpoint: "\/api\/v1\/content\/write"/)
  assert.match(source, /mode: args\.mode \?\? "create"/)
  assert.match(source, /actorPeerId,/)
  assert.match(source, /mode: z\.enum\(\["create", "append", "replace"\]\)/)
})
