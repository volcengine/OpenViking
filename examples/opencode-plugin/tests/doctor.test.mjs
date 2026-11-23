import test from "node:test"
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const DOCTOR = fileURLToPath(new URL("../scripts/ov-memory-doctor.mjs", import.meta.url))

function runDoctor(home) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("OPENVIKING_")))
  const run = spawnSync(process.execPath, [DOCTOR, "--offline", "--json"], {
    cwd: home,
    // Only node on PATH, so the `opencode --version` probe never starts a real host.
    env: { ...env, HOME: home, PATH: dirname(process.execPath) },
    encoding: "utf8",
  })
  const report = JSON.parse(run.stdout)
  const install = report.sections.find((section) => section.title === "Plugin install")
  return { report, problems: install.findings.filter((f) => f.level === "fail" || f.level === "warn") }
}

function withHome(fn) {
  const home = mkdtempSync(join(tmpdir(), "ov-opencode-doctor-"))
  try {
    return fn(home)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
}

test("on a machine without the plugin the doctor fails the install checks", () => withHome((home) => {
  const { report, problems } = runDoctor(home)
  assert.equal(report.harness, "opencode")
  assert.ok(problems.some((f) => f.level === "fail" && f.title === "OpenCode does not load this plugin"), JSON.stringify(problems))
}))

test("a file-plugin install reports no install problem", () => withHome((home) => {
  const plugins = join(home, ".config", "opencode", "plugins")
  mkdirSync(join(plugins, "openviking"), { recursive: true })
  writeFileSync(join(plugins, "openviking.js"), 'export { OpenVikingPlugin, default } from "./openviking/index.mjs"\n')
  writeFileSync(join(plugins, "openviking", "index.mjs"), "")
  // The installer's MCP fallback, in a file with a comment and a trailing comma.
  writeFileSync(join(home, ".config", "opencode", "opencode.json"),
    '{\n  // kept by the user\n  "mcp": { "openviking": { "type": "local", "command": ["node", "mcp-proxy.mjs"] }, },\n}\n')
  const { report, problems } = runDoctor(home)
  assert.equal(report.harness, "opencode")
  assert.deepEqual(problems, [])
}))

test("an npm-package install reports no install problem", () => withHome((home) => {
  mkdirSync(join(home, ".config", "opencode"), { recursive: true })
  const config = join(home, ".config", "opencode", "opencode.json")
  writeFileSync(config, JSON.stringify({ plugin: ["@openviking/opencode-plugin"] }))
  assert.deepEqual(runDoctor(home).problems, [])
  // OpenCode also accepts a [spec, options] pair.
  writeFileSync(config, JSON.stringify({ plugin: [["@openviking/opencode-plugin@latest", {}]] }))
  assert.deepEqual(runDoctor(home).problems, [])
}))
