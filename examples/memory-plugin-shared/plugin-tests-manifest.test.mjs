import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync, readdirSync, statSync } from "node:fs"
import { join, dirname, relative } from "node:path"
import { fileURLToPath } from "node:url"

// Guards the explicit file list of the `plugin-tests` job in .github/workflows/pr.yml.
// That job enumerates test files one by one (with a few directory globs), so a newly
// added *.test.mjs that is not registered there silently never runs in CI — this has
// happened repeatedly (missing pin tests, missing capture-utils coverage, missing
// marketplace registration). This test fails with an actionable message instead.

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const WORKFLOW = join(ROOT, ".github", "workflows", "pr.yml")

// Test files that intentionally do not run in the plugin-tests job, with reasons.
// Keep this list empty unless a file genuinely cannot run there.
const INTENTIONALLY_NOT_RUN = new Set([])

function parseWorkflowTestEntries() {
  const yml = readFileSync(WORKFLOW, "utf8")
  const stepMatch = yml.match(
    /name: Run memory plugin tests\s*\n\s*run: \|\s*\n((?:[ \t]+.*(?:\\\n)?\n)+)/,
  )
  assert.ok(stepMatch, "plugin-tests step not found in .github/workflows/pr.yml")
  const joined = stepMatch[1].replace(/\\\n/g, " ")
  const tokens = joined.split(/\s+/).filter(Boolean)
  assert.equal(tokens[0], "node", "expected the step to start with `node --test`")
  return tokens.slice(1).filter((t) => t.includes("/"))
}

function expandEntry(entry, listedAbsent) {
  if (!entry.includes("*")) {
    try {
      statSync(join(ROOT, entry))
      return [entry]
    } catch {
      listedAbsent.push(entry)
      return []
    }
  }
  const dir = dirname(entry)
  const pattern = entry.slice(entry.lastIndexOf("/") + 1)
  const prefix = pattern.slice(0, pattern.indexOf("*"))
  const suffix = pattern.slice(pattern.lastIndexOf("*") + 1)
  try {
    return readdirSync(join(ROOT, dir))
      .filter((name) => name.startsWith(prefix) && name.endsWith(suffix))
      .map((name) => relative(ROOT, join(dir, name)))
  } catch {
    return []
  }
}

function collectActualTestFiles() {
  const found = []
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name)
      const st = statSync(full)
      if (st.isDirectory()) walk(full)
      else if (name.endsWith(".test.mjs")) found.push(relative(ROOT, full))
    }
  }
  walk(join(ROOT, "examples"))
  const pluginRoot = join(ROOT, "agent-plugins")
  for (const name of readdirSync(pluginRoot)) {
    if (name.endsWith(".test.mjs")) found.push(relative(ROOT, join(pluginRoot, name)))
  }
  return found
}

test("every plugin test file is registered in the pr.yml plugin-tests job", () => {
  const listedAbsent = []
  const covered = new Set(
    parseWorkflowTestEntries().flatMap((entry) => expandEntry(entry, listedAbsent)),
  )
  assert.deepEqual(
    listedAbsent,
    [],
    `pr.yml plugin-tests lists files that do not exist: ${listedAbsent.join(", ")}`,
  )

  const actual = collectActualTestFiles()
  const missing = actual.filter((f) => !covered.has(f) && !INTENTIONALLY_NOT_RUN.has(f))
  assert.deepEqual(
    missing,
    [],
    `Test files present in the tree but never run by CI's plugin-tests job:\n${missing
      .map((f) => `  - ${f}`)
      .join(
        "\n",
      )}\nAdd them to the \`node --test\` list in .github/workflows/pr.yml (or to INTENTIONALLY_NOT_RUN with a reason).`,
  )

  const staleExclusions = [...INTENTIONALLY_NOT_RUN].filter((f) => !actual.includes(f))
  assert.deepEqual(
    staleExclusions,
    [],
    `INTENTIONALLY_NOT_RUN references files that no longer exist — prune them: ${staleExclusions.join(", ")}`,
  )
})
