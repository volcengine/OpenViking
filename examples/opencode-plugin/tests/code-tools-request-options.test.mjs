import test from "node:test"
import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"

const testDir = dirname(fileURLToPath(import.meta.url))
const codeToolsPath = join(testDir, "../lib/code-tools.mjs")

test("code tools propagate actorPeerId to every code request", async () => {
  const source = await readFile(codeToolsPath, "utf8")

  assert.match(source, /import \{ effectivePeerId,/)
  assert.match(source, /const actorPeerId = effectivePeerId\(config\)/)
  assert.equal((source.match(/actorPeerId,\n\s+abortSignal: context\.abort/g) ?? []).length, 4)
})

test("code tool descriptions restrict use to confirmed viking code repositories", async () => {
  const source = await readFile(codeToolsPath, "utf8")

  assert.match(source, /confirmed viking:\/\/ code repository or source subtree/)
  assert.match(source, /evidence that the uri contains supported source files/)
  assert.match(source, /Rank likely edit files\/symbols/)
  assert.match(source, /Search code by ranked path, symbol, and content matches/)
  assert.match(source, /Do not use for general memory search/)
  assert.match(source, /documentation-only resources/)
  assert.match(source, /chat\/session history/)
  assert.match(source, /local filesystem paths/)
})

test("codelocate exposes structured terms and hints", async () => {
  const source = await readFile(codeToolsPath, "utf8")

  assert.match(source, /body\.terms = terms \?\? \[\]/)
  assert.match(source, /body\.hints = hints \?\? \{\}/)
  assert.match(source, /terms: z\s*\n\s*\.array\(z\.string\(\)\)/)
  assert.match(source, /hints: z\s*\n\s*\.object\(\{/)
  assert.match(source, /paths: z/)
  assert.match(source, /path_terms: z/)
  assert.match(source, /symbols: z/)
  assert.match(source, /imports: z/)
  assert.match(source, /errors: z/)
})
