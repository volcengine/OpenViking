import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { createV2Compaction } from "../lib/v2-compaction.mjs"
import { createMemorySessionManager } from "../lib/memory-session.mjs"
import { deriveHarnessSessionId } from "../lib/shared/session-model.mjs"
import { startV2Plugin } from "../lib/v2-plugin.mjs"

const sessionID = "host-session"
const mapped = deriveHarnessSessionId("oc-", sessionID)
const location = { directory: "/fixture/project" }
const config = { takeoverEnabled: true, autoCapture: true, captureAssistantTurns: true,
  takeoverOverviewPollMs: 1, takeoverOverviewPollMax: 2, takeoverOverviewBudget: 100,
  account: "fixture-account", user: "fixture-user", peerId: "fixture-peer", sendIdentityHeaders: true,
  timeoutMs: 1000, commitTokenThreshold: 1000000, mcp: { enabled: false } }
const committed = { status: "accepted", result: { status: "accepted", session_id: mapped, archived: true,
  archive_uri: `viking://user/fixture-user/sessions/${mapped}/history/archive_001` } }
const complete = { ok: true, result: { archive_id: "archive_001", status: "completed", overview: "Captured decision", messages: [{ role: "user", content: "decision" }] } }
const host = { location, session: { get: async () => ({ id: sessionID, location }) } }
const event = () => ({ sessionID, system: [{ type: "text", text: "native system" }],
  messages: [{ role: "assistant", content: [{ type: "tool-call", id: "tool-1" }] },
    { role: "tool", content: [{ type: "tool-result", id: "tool-1" }] }] })

test("only a scoped completed archive augments native compaction; native transcript and result survive", async () => {
  const target = event()
  const original = structuredClone(target.messages)
  let reads = 0
  const adapter = createV2Compaction(host, { ...config, takeoverOverviewPollMs: 50 }, {
    getMappedSessionId: () => mapped, commitCurrentSession: async () => committed,
  }, { fetcher: async (_config, path, init, options) => {
    reads++
    assert.equal(path, `/api/v1/sessions/${mapped}/archives/archive_001`)
    assert.equal(options.actorPeerId, "fixture-peer")
    assert.ok(init.signal instanceof AbortSignal)
    return reads === 1 ? { ok: true, result: { ...complete.result, status: "pending", overview: "" } } : complete
  } })
  await adapter.assist(target)
  assert.equal(reads, 2)
  assert.match(target.system.at(-1).text, /filtered captured messages only/)
  assert.match(target.system.at(-1).text, /Captured decision/)
  assert.deepEqual(target.messages, original)
  assert.equal(target.result, undefined)
  const supplied = { ...event(), result: { summary: "another plugin's summary" } }
  await adapter.assist(supplied)
  assert.equal(reads, 2)
  assert.equal(supplied.result.summary, "another plugin's summary")
})

test("acceptance, foreign archives, empty/incomplete data and unavailable endpoints never replace native compaction", async () => {
  for (const [commit, read] of [
    [committed, { ok: false, status: 404 }], [committed, { ok: false, status: 401 }],
    [committed, { ok: false, status: 0 }], [committed, { ok: false, status: 500 }],
    [{ status: "pending_capture" }, complete], [{ status: "disabled" }, complete],
    [{ status: "accepted", result: { ...committed.result, session_id: "foreign" } }, complete],
    [{ status: "accepted", result: { ...committed.result, archive_uri: "viking://foreign/history/archive_001" } }, complete],
    [{ status: "accepted", result: { ...committed.result, archive_uri: committed.result.archive_uri.replace("fixture-user", "foreign-user") } }, complete],
    [committed, { ok: true, result: { ...complete.result, archive_id: "foreign" } }],
    [committed, { ok: true, result: { ...complete.result, messages: [] } }],
    [committed, { ok: true, result: { ...complete.result, overview: "" } }],
    [committed, { ok: true, result: { ...complete.result, status: "pending" } }],
    [committed, { ok: true, result: { ...complete.result, status: "failed" } }],
  ]) {
    const target = event()
    const before = structuredClone(target)
    await createV2Compaction(host, config, { getMappedSessionId: () => mapped, commitCurrentSession: async () => commit },
      { fetcher: async () => read }).assist(target)
    assert.deepEqual(target, before)
  }
})

test("disabled, excluded child and foreign/unknown location do not archive", async () => {
  let writes = 0
  for (const [cfg, session] of [
    [{ ...config, takeoverEnabled: false }, { id: sessionID, location }],
    [{ ...config, autoCapture: false }, { id: sessionID, location }],
    [{ ...config, skipSubagentSessions: true }, { id: sessionID, location, parentID: "parent" }],
    [config, { id: sessionID, location: { directory: "/other" } }],
    [config, {}], [config, { id: "other", location }],
    [config, { id: sessionID, location, parentID: "unmapped-child" }],
  ]) {
    const target = event()
    await createV2Compaction({ ...host, session: { get: async () => session } }, cfg,
      { getMappedSessionId: () => mapped, commitCurrentSession: async () => { writes++; return committed } }).assist(target)
    assert.equal(target.system.length, 1)
  }
  assert.equal(writes, 0)
})

test("ownership uses the public session location and project, not the private workspace ID", async () => {
  const location = { ...host.location, workspaceID: "private-workspace", project: { id: "project-1" } }
  for (const projectID of ["project-1", "foreign-project"]) {
    const target = event()
    await createV2Compaction({ location, session: { get: async () => ({ id: sessionID, projectID, location: host.location }) } }, config,
      { getMappedSessionId: () => mapped, commitCurrentSession: async () => committed },
      { fetcher: async () => complete }).assist(target)
    assert.equal(target.system.length, projectID === "project-1" ? 2 : 1)
  }
})

test("timeout, interruption, shutdown and late completion leave native compaction unchanged", async () => {
  for (const kind of ["timeout", "interrupt", "shutdown"]) {
    const lifetime = new AbortController()
    let release
    let started
    const ready = new Promise((resolve) => { started = resolve })
    const commit = new Promise((resolve) => { release = resolve })
    const adapter = createV2Compaction(host, config, { getMappedSessionId: () => mapped,
      commitCurrentSession: async () => { started(); return commit } }, { signal: lifetime.signal, fetcher: async () => complete })
    const target = event()
    const pending = adapter.assist(target)
    await ready
    if (kind === "interrupt") adapter.cancel(sessionID)
    if (kind === "shutdown") lifetime.abort()
    await pending
    release(committed)
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(target.system.length, 1)
    assert.equal(target.result, undefined)
  }
})

test("production hook captures below-threshold tool history before commit, then repeats and resumes safely", async () => {
  const directory = await mkdtemp(join(tmpdir(), "ov-v2-compact-"))
  const requests = []
  let batchCount = 0
  let commitCount = 0
  let archiveCount = 0
  let pendingCapture = false
  const server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : undefined
    requests.push({ path: req.url, body, headers: req.headers })
    let result = { pending_tokens: 0 }
    if (req.url.endsWith("/messages/batch")) { batchCount++; pendingCapture = true; result = { accepted: true } }
    if (req.url.endsWith("/commit")) {
      commitCount++
      if (pendingCapture) {
        archiveCount++
        result = { ...committed.result, archive_uri: committed.result.archive_uri.replace("001", String(archiveCount).padStart(3, "0")) }
        pendingCapture = false
      } else result = { status: "skipped", archived: false, session_id: mapped }
    }
    if (req.url.includes("/archives/")) result = { ...complete.result, archive_id: req.url.split("/").at(-1) }
    res.setHeader("Content-Type", "application/json")
    res.end(JSON.stringify({ status: "ok", result }))
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  const cfg = { ...config, takeoverOverviewPollMs: 100, takeoverOverviewPollMax: 10,
    endpoint: `http://127.0.0.1:${server.address().port}` }
  const storage = new Map()
  const transcript = [{ id: "user-1", type: "user", text: "Please preserve the deployment decision and successful tool output." },
    { id: "assistant-1", type: "assistant", content: [{ type: "text", text: "The deployment uses a local fixture." },
      { type: "tool", id: "call-1", name: "read", state: { status: "completed", content: [{ type: "text", text: "fixture tool output" }] } }] }]
  const original = structuredClone(transcript)
  try {
    for (const restart of [false, true]) {
      if (restart) transcript.push({ id: "user-2", type: "user", text: "Preserve this new request after restarting the host." })
      const hooks = {}
      const manager = createMemorySessionManager({ config: cfg, pluginRoot: directory })
      await manager.init({ deferNetwork: true })
      const ctx = { ...host, storage: { get: async (key) => storage.get(key), set: async (key, value) => storage.set(key, value) },
        session: { ...host.session, context: async () => transcript, hook: async (name, fn) => { hooks[name] = fn } } }
      const cleanup = await startV2Plugin(ctx, { config: cfg, sessionManager: manager,
        ready: Promise.resolve(), repoContext: { getRepoSystemPrompt: () => null },
        recall: {}, sessionInject: {} }, { pluginRoot: fileURLToPath(new URL("..", import.meta.url)) })
      const target = event()
      await hooks.compaction(target)
      assert.match(target.system.at(-1).text, /Captured decision/)
      assert.deepEqual(target.messages, event().messages)
      assert.equal(target.result, undefined)
      if (!restart) {
        assert.ok(requests.findIndex((r) => r.path.endsWith("/messages/batch")) < requests.findIndex((r) => r.path.endsWith("/commit")))
        const batch = requests.find((r) => r.path.endsWith("/messages/batch"))
        assert.match(JSON.stringify(batch.body), /fixture tool output/)
        assert.equal(batch.headers["x-openviking-account"], "fixture-account")
        assert.match(JSON.stringify(batch.body), /fixture-peer/)
        const repeat = event()
        await hooks.compaction(repeat)
        assert.equal(repeat.system.length, 1, "no new captured content means native-only compaction")
      }
      await cleanup()
      await manager.waitForBackground()
    }
    assert.equal(batchCount, 2, "restart uploads only the newly appended host message")
    const batches = requests.filter((r) => r.path.endsWith("/messages/batch"))
    assert.doesNotMatch(JSON.stringify(batches[1].body), /fixture tool output/)
    assert.ok(commitCount >= 3, "an explicit boundary may attempt a commit with no new capture")
    assert.equal(archiveCount, 2)
    assert.deepEqual(transcript.slice(0, original.length), original, "host source transcript is retained")
  } finally {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
    await rm(directory, { recursive: true, force: true })
  }
})
