import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile, utimes } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import {
  isBlockedSkill,
  filterFindText,
  filterRecallEntries,
  loadSkillsetRegistry,
  normalizeSkillsetList,
  renderFilteredEntries,
} from "../lib/shared/skillset-filter.mjs"
import { buildRecallBlock, fetchAssembledContext } from "../lib/shared/recall-core.mjs"
import { buildMcpProxyConfig } from "../lib/shared/mcp-proxy-config.mjs"

async function withTempDir(prefix, fn) {
  const dir = await mkdtemp(join(tmpdir(), prefix))
  try {
    return await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

// Point the registry at a fixture and force distinct mtimes between writes so
// the module-level mtime cache cannot serve a stale file across tests.
async function withRegistry(skills, fn) {
  await withTempDir("ov-skillsets-", async (dir) => {
    const registryPath = join(dir, "skillsets.json")
    const write = async (payload) => {
      await writeFile(registryPath, JSON.stringify(payload))
      stamp += 1000
      const when = new Date(stamp)
      await utimes(registryPath, when, when)
    }
    await write({ skills })
    process.env.OPENVIKING_SKILLSETS_REGISTRY = registryPath
    try {
      return await fn(registryPath, write)
    } finally {
      delete process.env.OPENVIKING_SKILLSETS_REGISTRY
    }
  })
}
let stamp = Date.now()

const OWN_SKILL = "viking://user/u1/skills/deploy-runbook"
const VENDORED_SKILL = "viking://user/u1/skills/jeffreys-nmap-sweep"
const UNCLASSIFIED_SKILL = "viking://user/u1/skills/orphan-skill"
const AGENT_SKILL = "viking://agent/skills/agent-skill"
const MEMORY_URI = "viking://user/u1/memories/incident-2026"

test("normalizeSkillsetList accepts arrays and comma strings, trimming empties", () => {
  assert.deepEqual(normalizeSkillsetList(null), [])
  assert.deepEqual(normalizeSkillsetList(undefined), [])
  assert.deepEqual(normalizeSkillsetList([]), [])
  assert.deepEqual(normalizeSkillsetList([" local ", "", "audited"]), ["local", "audited"])
  assert.deepEqual(normalizeSkillsetList(" local , ,audited "), ["local", "audited"])
})

test("loadSkillsetRegistry returns null for a missing file and reloads on mtime change", async () => {
  await withTempDir("ov-skillsets-missing-", async (dir) => {
    process.env.OPENVIKING_SKILLSETS_REGISTRY = join(dir, "absent.json")
    try {
      assert.equal(loadSkillsetRegistry(), null)
    } finally {
      delete process.env.OPENVIKING_SKILLSETS_REGISTRY
    }
  })

  await withRegistry({ "deploy-runbook": ["local"] }, async (path, rewrite) => {
    assert.equal(loadSkillsetRegistry()?.skills?.["deploy-runbook"]?.[0], "local")
    await rewrite({ skills: { "deploy-runbook": ["local", "audited"] } })
    assert.deepEqual(loadSkillsetRegistry()?.skills?.["deploy-runbook"], ["local", "audited"])
  })
})

test("isBlockedSkill enforces allowlist (fail-closed), denylist, and their combination", async () => {
  await withRegistry({
    "deploy-runbook": ["local"],
    "jeffreys-nmap-sweep": ["jeffreys"],
    "agent-skill": ["local"],
  }, async () => {
    // No filtering configured: nothing is blocked.
    assert.equal(isBlockedSkill(VENDORED_SKILL), false)

    // Allowlist: only members pass; unclassified and future sets are blocked.
    assert.equal(isBlockedSkill(OWN_SKILL, ["local"], []), false)
    assert.equal(isBlockedSkill(VENDORED_SKILL, ["local"], []), true)
    assert.equal(isBlockedSkill(UNCLASSIFIED_SKILL, ["local"], []), true)
    assert.equal(isBlockedSkill(AGENT_SKILL, ["local"], []), false)

    // Denylist: members dropped, unclassified pass.
    assert.equal(isBlockedSkill(VENDORED_SKILL, [], ["jeffreys"]), true)
    assert.equal(isBlockedSkill(OWN_SKILL, [], ["jeffreys"]), false)
    assert.equal(isBlockedSkill(UNCLASSIFIED_SKILL, [], ["jeffreys"]), false)

    // Combined: allowlist minus exclusions.
    assert.equal(isBlockedSkill(AGENT_SKILL, ["local"], ["jeffreys"]), false)
    assert.equal(isBlockedSkill(VENDORED_SKILL, ["local", "jeffreys"], ["jeffreys"]), true)

    // Non-skill URIs always pass.
    assert.equal(isBlockedSkill(MEMORY_URI, ["local"], ["jeffreys"]), false)
    assert.equal(isBlockedSkill("", ["local"], []), false)
    assert.equal(isBlockedSkill(undefined, ["local"], []), false)
  })
})

test("filterRecallEntries drops blocked skills and counts them", async () => {
  await withRegistry({
    "jeffreys-nmap-sweep": ["jeffreys"],
  }, async () => {
    const entries = [
      { uri: MEMORY_URI, text: "memory" },
      { uri: VENDORED_SKILL, text: "vendored" },
      { uri: UNCLASSIFIED_SKILL, text: "orphan" },
    ]
    assert.deepEqual(filterRecallEntries(entries, [], []), { entries, dropped: 0 })
    const filtered = filterRecallEntries(entries, [], ["jeffreys"])
    assert.equal(filtered.dropped, 1)
    assert.deepEqual(filtered.entries.map((e) => e.uri), [MEMORY_URI, UNCLASSIFIED_SKILL])
    // Allowlist fail-closed against an unclassified skill.
    const closed = filterRecallEntries(entries, ["local"], [])
    assert.equal(closed.dropped, 2)
    assert.deepEqual(closed.entries.map((e) => e.uri), [MEMORY_URI])
  })
})

test("renderFilteredEntries renders uri plus a capped excerpt", () => {
  const long = "x".repeat(300)
  const rendered = renderFilteredEntries([
    { uri: MEMORY_URI, text: long },
    { uri: OWN_SKILL },
  ])
  assert.match(rendered, new RegExp(`^- ${MEMORY_URI}\\n  x{200}…$`, "m"))
  assert.match(rendered, new RegExp(`^- ${OWN_SKILL}$`, "m"))
})

test("filterFindText removes blocked skill blocks, rewrites the header count, keeps the tail hint", async () => {
  await withRegistry({
    "deploy-runbook": ["local"],
    "jeffreys-nmap-sweep": ["jeffreys"],
  }, async () => {
    const serverShape = [
      "Found 3 item(s):",
      "",
      `- [skill 92%] ${OWN_SKILL}`,
      "    Runbook for the deploy pipeline.",
      "",
      `- [skill 81%] ${VENDORED_SKILL}`,
      "    Vendored sweep skill.",
      "",
      `- [memory 70%] ${MEMORY_URI}`,
      "    Incident writeup.",
      "",
      "Use the read tool to expand a URI.",
    ].join("\n")

    // No filtering configured: byte-for-byte passthrough.
    assert.equal(filterFindText(serverShape, [], []), serverShape)

    const filtered = filterFindText(serverShape, ["local"], [])
    assert.match(filtered, /^Found 2 item\(s\):/)
    assert.match(filtered, new RegExp(`^- \\[skill 92%\\] ${OWN_SKILL}$`, "m"))
    assert.doesNotMatch(filtered, /jeffreys/)
    assert.match(filtered, new RegExp(`^- \\[memory 70%\\] ${MEMORY_URI}$`, "m"))
    assert.match(filtered, /Use the read tool to expand a URI\.$/)
    // A memory-only result with no blocks passes through untouched.
    const noBlocks = "Found 1 item(s):\n\nno hits rendered"
    assert.equal(filterFindText(noBlocks, ["local"], []), noBlocks)
  })
})

test("fetchAssembledContext discards the server render and digest when a skill is blocked", async () => {
  await withRegistry({ "jeffreys-nmap-sweep": ["jeffreys"] }, async (path) => {
    const entries = [
      { uri: MEMORY_URI, text: "incident writeup" },
      { uri: VENDORED_SKILL, text: "vendored skill body" },
    ]
    const fetchJSON = async () => ({
      ok: true,
      status: 200,
      result: {
        rendered: "<rendered> server block embedding jeffreys-nmap-sweep </rendered>",
        digest: "server digest mentioning jeffreys-nmap-sweep",
        entries,
        stats: { used_tokens: 42 },
      },
    })
    const options = { legacyCachePath: join(path, "absent-legacy-marker") }

    const untouched = await fetchAssembledContext(fetchJSON, {}, "query", options)
    assert.equal(untouched.rendered, "<rendered> server block embedding jeffreys-nmap-sweep </rendered>")
    assert.equal(untouched.digest, "server digest mentioning jeffreys-nmap-sweep")
    assert.equal(untouched.entries.length, 2)

    const filtered = await fetchAssembledContext(
      fetchJSON,
      { skillsetsExclude: "jeffreys" },
      "query",
      options,
    )
    assert.equal(filtered.entries.length, 1)
    assert.equal(filtered.entries[0].uri, MEMORY_URI)
    assert.doesNotMatch(filtered.rendered, /jeffreys/)
    assert.match(filtered.rendered, new RegExp(`^- ${MEMORY_URI}$`, "m"))
    assert.equal(filtered.digest, "")
  })
})

test("buildRecallBlock find-fallback filters skills but never memories", async () => {
  await withRegistry({ "jeffreys-nmap-sweep": ["jeffreys"] }, async (path, rewrite) => {
    const findCalls = []
    const fetchJSON = async (url, init = {}) => {
      if (url === "/api/v1/search/search" || url === "/api/v1/search/recall") {
        return { ok: false, status: 503 }
      }
      if (url === "/api/v1/search/find") {
        findCalls.push(JSON.parse(init.body))
        const targetUri = JSON.parse(init.body).target_uri
        if (targetUri.endsWith("/skills")) {
          return {
            ok: true,
            status: 200,
            result: {
              skills: [
                { uri: OWN_SKILL, score: 0.9, abstract: "runbook" },
                { uri: VENDORED_SKILL, score: 0.85, abstract: "sweep" },
              ],
            },
          }
        }
        return {
          ok: true,
          status: 200,
          result: {
            memories: [
              { uri: MEMORY_URI, score: 0.8, abstract: "incident" },
            ],
          },
        }
      }
      return { ok: false, status: 404 }
    }

    const options = { legacyCachePath: join(path, "absent-legacy-marker") }
    const block = await buildRecallBlock(
      fetchJSON,
      { skillsetsExclude: "jeffreys", scoreThreshold: 0.3, recallLimit: 5 },
      "deploy incident",
      options,
    )
    assert.ok(block)
    assert.doesNotMatch(block, /jeffreys/)
    assert.doesNotMatch(block, /sweep/)
    assert.match(block, /runbook/)
    assert.match(block, /incident/)

    // Allowlist fail-closed: the unclassified skill disappears, memory stays.
    // deploy-runbook must be classified for the allowlist to admit it.
    await rewrite({ skills: { "deploy-runbook": ["local"], "jeffreys-nmap-sweep": ["jeffreys"] } })
    const closed = await buildRecallBlock(
      fetchJSON,
      { skillsetsOnly: "local", scoreThreshold: 0.3, recallLimit: 5 },
      "deploy incident",
      options,
    )
    assert.ok(closed)
    assert.doesNotMatch(closed, /jeffreys/)
    assert.doesNotMatch(closed, /sweep/)
    assert.doesNotMatch(closed, /orphan/)
    assert.match(closed, /runbook/)
    assert.match(closed, /incident/)
  })
})

test("buildMcpProxyConfig forwards and normalizes both skillset knobs", () => {
  const cfg = buildMcpProxyConfig({
    baseUrl: "http://127.0.0.1:1933",
    skillsetsOnly: " local ,,audited ",
    skillsetsExclude: [" jeffreys ", ""],
  })
  assert.deepEqual(cfg.skillsetsOnly, ["local", "audited"])
  assert.deepEqual(cfg.skillsetsExclude, ["jeffreys"])

  const unset = buildMcpProxyConfig({ baseUrl: "http://127.0.0.1:1933" })
  assert.deepEqual(unset.skillsetsOnly, [])
  assert.deepEqual(unset.skillsetsExclude, [])
})

test("proxy config source forwards the plugin config keys", async () => {
  await withTempDir("ov-skillsets-cfg-", async (dir) => {
    const configPath = join(dir, "openviking-config.json")
    await writeFile(configPath, JSON.stringify({
      skillsetsOnly: ["local"],
      skillsetsExclude: "jeffreys,vendored",
    }))
    process.env.OPENVIKING_PLUGIN_CONFIG = configPath
    const { loadConfig } = await import("../lib/config.mjs")
    try {
      const cfg = loadConfig(join(dir, "plugin-root"))
      assert.deepEqual(cfg.skillsetsOnly, ["local"])
      // The file form passes through verbatim; every consumer normalizes
      // arrays and comma strings identically (normalizeSkillsetList).
      assert.equal(cfg.skillsetsExclude, "jeffreys,vendored")
      assert.deepEqual(normalizeSkillsetList(cfg.skillsetsExclude), ["jeffreys", "vendored"])

      process.env.OPENVIKING_SKILLSETS_ONLY = "env-a, env-b"
      process.env.OPENVIKING_SKILLSETS_EXCLUDE = "env-x"
      const envCfg = loadConfig(join(dir, "plugin-root"))
      assert.deepEqual(envCfg.skillsetsOnly, ["env-a", "env-b"])
      assert.deepEqual(envCfg.skillsetsExclude, ["env-x"])
      delete process.env.OPENVIKING_SKILLSETS_ONLY
      delete process.env.OPENVIKING_SKILLSETS_EXCLUDE
    } finally {
      delete process.env.OPENVIKING_PLUGIN_CONFIG
    }
  })
})
