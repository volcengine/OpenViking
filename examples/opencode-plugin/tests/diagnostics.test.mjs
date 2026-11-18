import test from "node:test"
import assert from "node:assert/strict"
import { diagnoseOpenCode } from "../lib/diagnostics.mjs"

const config = { enabled: true, autoCapture: true, mcp: { enabled: true }, baseUrl: "http://localhost:1933", apiKey: "secret-fixture-key", runtime: { dataDir: "/nonexistent/ov-diagnostics" } }
const ctx = {
  app: { version: "2.3.0" },
  plugin: { list: async () => ({ data: [{ id: "openviking", source: { type: "package", target: "@openviking/opencode-plugin", version: "0.5.23" }, state: { status: "active" } }] }) },
  mcp: { list: async () => ({ data: [{ name: "openviking", status: { status: "connected" } }] }) },
  skill: { list: async () => ({ data: [] }) },
}

test("connected MCP with missing native skills is a failure, without capture or repair", async () => {
  const result = await diagnoseOpenCode({ ctx, config, pluginRoot: "/nonexistent", offline: true })
  assert.equal(result.mcp.state, "connected")
  assert.equal(result.skills.state, "error")
  assert.deepEqual(result.skills.missing, ["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search"])
  assert.equal(result.exitCode, 1)
  assert.doesNotMatch(JSON.stringify(result), /secret-fixture-key/)
})

test("Doctor matches plugin identity without counting unrelated parent paths", async () => {
  const local = { id: "openviking", source: { type: "local", path: "/plugins/openviking/index.mjs" }, state: { status: "active" } }
  const companion = { id: "mee-toolkit", source: { type: "local", path: "/state/openviking-parity/toolkit/index.mjs" }, state: { status: "active" } }
  const lookalike = { id: "lookalike", source: { type: "package", target: "@openviking/opencode-plugin-helper" }, state: { status: "active" } }
  const duplicate = { id: "openviking-package", source: { type: "package", target: "@openviking/opencode-plugin@0.5.29" }, state: { status: "active" } }
  const plugins = [local, companion, lookalike]
  const inventory = { ...ctx, plugin: { list: async () => ({ data: plugins }) } }
  const diagnose = () => diagnoseOpenCode({ ctx: inventory, config, offline: true })
  const initial = (await diagnose()).installation
  assert.equal(initial.state, "ok")
  assert.deepEqual(initial.instances.map(({ id }) => id), ["openviking"])
  plugins.push(duplicate)
  const withDuplicate = (await diagnose()).installation
  assert.equal(withDuplicate.state, "error")
  assert.deepEqual(withDuplicate.instances.map(({ id }) => id), ["openviking", "openviking-package"])
})

test("disabled, unsupported and failed native inventories remain distinct", async () => {
  const disabled = await diagnoseOpenCode({ ctx, config: { ...config, mcp: { enabled: false }, autoCapture: false }, offline: true })
  assert.equal(disabled.mcp.state, "disabled")
  assert.equal(disabled.skills.state, "disabled")
  assert.equal(disabled.capture.state, "disabled")
  const unsupported = await diagnoseOpenCode({ config, offline: true })
  assert.equal(unsupported.skills.state, "unsupported")
  assert.deepEqual(unsupported, JSON.parse(JSON.stringify(unsupported)), "native RPC output must contain only JSON values")
  const failed = await diagnoseOpenCode({ ctx: { ...ctx, skill: { list: async () => { throw new Error("permission denied secret-fixture-key") } } }, config, offline: true })
  assert.equal(failed.skills.state, "error")
  assert.match(failed.skills.error, /permission denied/)
  assert.doesNotMatch(JSON.stringify(failed), /secret-fixture-key/)
})

import { registerV2Controls, OPENVIKING_CONTROLS } from "../lib/v2-controls.mjs"

test("RPC read controls never create messages or commit; intentional commit scopes and awaits capture", async () => {
  let handlers
  const writes = []
  const runtime = { config, ready: Promise.resolve(), sessionManager: { commitCurrentSession: async (id) => { writes.push(`commit:${id}`); return { status: "accepted", result: { completed: true }, traceId: "trace-1" } } } }
  const native = { ...ctx, location: { directory: "/project", project: { id: "project", directory: "/project" } }, rpc: { register: async (definition, callbacks) => { assert.equal(definition, OPENVIKING_CONTROLS); handlers = callbacks } }, session: { get: async () => ({ location: { directory: "/project" }, projectID: "project" }) } }
  await registerV2Controls(native, runtime, { serializeSession: (op) => op(), captureCurrent: async (id) => { await Promise.resolve(); writes.push(`capture:${id}`) } })
  await handlers.status({ offline: true })
  await handlers.doctor({ offline: true })
  assert.deepEqual(writes, [])
  assert.deepEqual(await handlers.commit({ sessionID: "current" }), { status: "accepted", traceId: "trace-1", extraction: "not_verified" })
  assert.deepEqual(writes, ["capture:current", "commit:current"])
  runtime.config = { ...config, autoCapture: false }
  assert.equal((await handlers.commit({ sessionID: "current" })).status, "disabled")
  assert.equal(writes.length, 2)
  runtime.config = config
  native.session.get = async () => ({ location: { directory: "/other" } })
  await assert.rejects(handlers.commit({ sessionID: "foreign" }), /another OpenCode location/)
  native.session.get = async () => ({ location: { directory: "/project" }, projectID: "other" })
  await assert.rejects(handlers.commit({ sessionID: "foreign-project" }), /another OpenCode location/)
  native.session.get = async () => ({ location: { directory: "/project" }, projectID: "project" })
  runtime.sessionManager.commitCurrentSession = async () => { throw new Error("401 denied") }
  await assert.rejects(handlers.commit({ sessionID: "current" }), /401 denied/)
})

import { createServer } from "node:http"

test("doctor probes authenticated MCP read operations and redacts effective config", async () => {
  const requests = []
  const server = createServer((req, res) => {
    requests.push({ method: req.method, url: req.url, auth: req.headers.authorization, gateway: req.headers["x-gateway"] })
    res.setHeader("Content-Type", "application/json")
    if (!req.headers["x-gateway"]) {
      res.statusCode = 401
      return res.end(JSON.stringify({ status: "error", error: { message: "gateway required" } }))
    }
    if (req.url === "/mcp") return res.end(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { tools: [] } }))
    if (req.url === "/health") return res.end(JSON.stringify({ status: "ok", version: "fixture", auth_mode: "api_key", role: "user", user_id: "fixture" }))
    res.end(JSON.stringify({ status: "ok", result: { entries: [] } }))
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  try {
    const result = await diagnoseOpenCode({ ctx, config: { ...config, baseUrl: `http://127.0.0.1:${server.address().port}`, extraHeaders: { "X-Gateway": 'secret-"gateway"' } } })
    assert.equal(result.connection.mcpHttpStatus, 200)
    assert.equal(result.connection.authOk, true)
    assert.ok(requests.some((r) => r.url === "/mcp" && r.auth === "Bearer secret-fixture-key" && r.gateway === 'secret-"gateway"' ))
    assert.ok(requests.every((r) => r.method === "GET" || r.url === "/mcp"))
    assert.doesNotMatch(JSON.stringify(result), /secret-fixture-key/)
    assert.doesNotMatch(JSON.stringify(result), /secret-[\\"]*gateway/)
  } finally { await new Promise((resolve) => server.close(resolve)) }
})

import { mkdtemp, mkdir, readdir, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { spawnSync } from "node:child_process"

test("standalone offline doctor never writes a cold workspace identity cache", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-doctor-readonly-"))
  try {
    const home = join(dir, "home")
    const workspace = join(dir, "workspace")
    await mkdir(home)
    await mkdir(join(workspace, ".openviking"), { recursive: true })
    await writeFile(join(workspace, ".openviking", "config.json"), JSON.stringify({ version: 1, peer: { id: "doctor-fixture" } }))
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("OPENVIKING_")))
    Object.assign(env, { HOME: home, XDG_CONFIG_HOME: join(home, "config"), XDG_DATA_HOME: join(home, "data"), XDG_CACHE_HOME: join(home, "cache") })
    const run = spawnSync(process.execPath, [resolve(import.meta.dirname, "../scripts/ov-memory-doctor.mjs"), "--json", "--offline"], { env, cwd: workspace, encoding: "utf8" })
    assert.equal(run.status, 0, run.stderr)
    assert.equal(JSON.parse(run.stdout).skills.state, "unsupported")
    assert.deepEqual(await readdir(home), [])
    assert.deepEqual(await readdir(join(workspace, ".openviking")), ["config.json"])
  } finally { await rm(dir, { recursive: true, force: true }) }
})
