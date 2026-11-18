import test from "node:test"
import assert from "node:assert/strict"
import { createSkillExperience } from "../lib/skill-experience.mjs"
import { estimateTokens } from "../lib/shared/recall-core.mjs"
import { contextMessageEvents } from "../lib/v2-events.mjs"
import { shapeCapturePayload } from "../lib/shared/capture-utils.mjs"

const uri = "viking://user/alice/memories/experiences/deploy.md"
const config = { enabled: true, skillExperience: true, skillExperienceLimit: 3, timeoutMs: 1000 }
const event = (name = "deploy", sessionID = "s") => ({ tool: "skill", status: "completed", sessionID, input: { id: "arbitrary-id" }, result: { output: { directory: "/project/.opencode/skills/deploy", content: "instructions" }, content: [{ type: "text", text: "instructions" }, { type: "file", uri: "file:///image.png", mime: "image/png" }], metadata: { name, original: true } } })
function fixture(overrides = {}, handler) {
  const calls = []
  const hook = createSkillExperience({ config: { ...config, ...overrides }, directory: "/project", request: async (path, init, options) => {
    calls.push({ path, init, options })
    if (handler) return handler(path, init)
    return { ok: true, result: path.endsWith("status") ? { user: "alice" } : path.endsWith("find") ? { memories: [{ uri, score: 0.84 }] } : "Verify rollout & <untrusted>" }
  } })
  return { ...hook, calls }
}

test("native successful skill loads use current-user generic find/read and preserve the tool result", async () => {
  const hook = fixture({ effectivePeer: { peerId: "project" } })
  const e = event()
  const before = structuredClone(e.result)
  await hook.after(e)
  assert.deepEqual(e.result.content.slice(0, 2), before.content)
  assert.deepEqual(e.result.output, before.output)
  assert.deepEqual(e.result.metadata, before.metadata)
  const block = e.result.content.at(-1).text
  assert.match(block, /<openviking-context source="skill-experience" format="experience-digest">/)
  assert.match(block, /\[experience 84%\].*Verify rollout &amp; &lt;untrusted&gt;.*deploy.md/)
  assert.match(block, /Exposure does not establish successful application/)
  assert.equal(estimateTokens(block) <= 800, true)
  const search = JSON.parse(hook.calls[1].init.body)
  assert.equal(search.target_uri, "viking://~/memories/experiences")
  assert.equal(search.query, "skill deploy usage experience")
  assert.equal(hook.calls[2].path, `/api/v1/content/read?uri=${encodeURIComponent(uri)}`)
  assert.ok(hook.calls.every((c) => c.options.actorPeerId === "project"))
  // The genuine skill result survives the existing capture adapter. Internal
  // HTTP retrieval creates no fake generic-tool records.
  const captured = contextMessageEvents("s", { id: "a", type: "assistant", content: [{ type: "tool", id: "call", name: "skill", state: { status: "completed", input: e.input, content: e.result.content } }] })
  assert.match(captured[1].properties.part.output, /skill-experience/)
  assert.equal(captured[1].properties.part.tool, "skill")
  const payload = shapeCapturePayload({ role: "assistant", content: [captured[1].properties.part] }, "assistant", { captureMode: "semantic" })
  assert.equal(payload.parts.length, 1)
  assert.equal(payload.parts[0].type, "tool")
  assert.match(payload.parts[0].tool_output, /skill-experience/)
  assert.equal(payload.parts[0].tool_name, "skill")
})

test("explicit native SKILL.md reads deduplicate canonical names with skill loads, independently per session", async () => {
  const hook = fixture()
  const e = { ...event(), tool: "read", input: { path: ".opencode/skills/folder/SKILL.md" }, result: { content: "1: ---\n2: name: deploy\n3: ---\n4: # Steps", output: { type: "text-page", offset: 1, content: "---\nname: 'deploy'\n---\n# Steps" } } }
  await hook.after(e)
  assert.match(e.result.content, /skill-experience/)
  await Promise.all([hook.after(event()), hook.after(event())])
  assert.equal(hook.calls.length, 3)
  await hook.after(event("deploy", "other"))
  assert.equal(hook.calls.length, 6)
  hook.forget("s")
  await hook.after(event())
  assert.equal(hook.calls.length, 9)
})

test("off, disabled, bypassed, failed, unrelated and generic Experience calls never retrieve", async () => {
  for (const cfg of [{ skillExperience: false }, { enabled: false }, { bypassSession: true }, { bypassSessionPatterns: ["/project"] }, { bypassSessionPatterns: ["s"] }]) {
    const hook = fixture(cfg)
    await hook.after(event())
    assert.equal(hook.calls.length, 0)
  }
  const hook = fixture()
  for (const e of [{ ...event(), status: "error" }, { ...event(), tool: "openviking_read", input: { uri } }, { ...event(), tool: "openviking_find" }, { ...event(), tool: "read", input: { path: "README.md" } }, { ...event(), tool: "read", input: { path: "SKILL.md" } }, { ...event(), result: {} }]) await hook.after(e)
  assert.equal(hook.calls.length, 0)
})

test("foreign, traversal, directory, sidecar and malformed hits cannot be read or injected", async () => {
  const bad = [uri.replace("alice", "bob"), uri + "?x", uri + "#x", uri.replace("deploy.md", "../deploy.md"), uri.replace("deploy.md", ".abstract.md"), uri.replace("deploy.md", "%2e%2e/deploy.md"), uri.replace("deploy.md", ""), uri.replace("deploy.md", "a b.md")]
  const hook = fixture({}, async (path) => ({ ok: true, result: path.endsWith("status") ? { user: "alice" } : { memories: bad.map((uri) => ({ uri, score: 1 })) } }))
  const e = event()
  const original = structuredClone(e)
  await hook.after(e)
  assert.deepEqual(e, original)
  assert.equal(hook.calls.length, 2)
})

test("errors, empty bodies, unavailable identity and timeouts fail open with no late mutation", async () => {
  for (const handler of [async () => { throw new Error("offline") }, async () => ({ ok: false }), async () => ({ ok: true, result: {} }), async (path) => ({ ok: true, result: path.endsWith("status") ? { user: "alice" } : path.endsWith("find") ? { memories: [{ uri }] } : "" })]) {
    const hook = fixture({}, handler)
    const e = event(); const original = structuredClone(e)
    await hook.after(e)
    assert.deepEqual(e, original)
  }
  let release
  const hook = fixture({ timeoutMs: 15 }, () => new Promise((resolve) => { release = resolve }))
  const e = event(); const original = structuredClone(e)
  await hook.after(e)
  release({ ok: true, result: { user: "alice" } })
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(e, original)
  assert.equal(hook.calls.length, 1)
})

test("session token/attempt budgets, disposal and deletion bound concurrent retrieval", async () => {
  const hook = fixture({ recallTokenBudget: 200 })
  const events = Array.from({ length: 30 }, (_, i) => event(`skill-${i}`))
  await Promise.all(events.map(hook.after))
  assert.equal(hook.calls.length, 3)
  assert.ok(events.reduce((n, e) => n + estimateTokens(e.result.content[2]?.text), 0) <= 200)
  hook.dispose()
  await hook.after(event("new", "new"))
  assert.equal(hook.calls.length, 3)
  for (const cancel of [(hook) => hook.forget("s"), (hook) => hook.dispose()]) {
    let release
    const pending = fixture({}, () => new Promise((resolve) => { release = resolve }))
    const e = event(); const original = structuredClone(e)
    const done = pending.after(e)
    cancel(pending)
    release({ ok: true, result: { user: "alice" } })
    await done
    assert.deepEqual(e, original)
    assert.equal(pending.calls.length, 1, "cancellation must prevent subsequent lookup/read requests")
  }
})

test("captured hints reach the session batch and explicit commit as genuine tool output", async () => {
  const { createServer } = await import("node:http")
  const { mkdtemp, rm } = await import("node:fs/promises")
  const { tmpdir } = await import("node:os")
  const { join } = await import("node:path")
  const { createMemorySessionManager } = await import("../lib/memory-session.mjs")
  const requests = []
  const server = createServer(async (req, res) => {
    let body = ""
    for await (const chunk of req) body += chunk
    requests.push({ path: req.url, body })
    res.setHeader("Content-Type", "application/json")
    res.end(JSON.stringify({ status: "ok", result: { accepted: true, pending_tokens: 0 } }))
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  const dir = await mkdtemp(join(tmpdir(), "ov-skill-capture-"))
  try {
    const manager = createMemorySessionManager({ pluginRoot: dir, config: {
      endpoint: `http://127.0.0.1:${server.address().port}`, timeoutMs: 1000,
      autoCapture: true, captureAssistantTurns: true, captureMode: "semantic",
      captureMaxLength: 24000, commitTokenThreshold: 20000, commitKeepRecentCount: 10,
    } })
    await manager.init()
    const e = event()
    await fixture().after(e)
    for (const normalized of contextMessageEvents("s", { id: "a", type: "assistant", content: [{ type: "tool", id: "call", name: "skill", state: { status: "completed", input: e.input, content: e.result.content } }] })) await manager.handleEvent(normalized)
    await manager.flushAll({ commit: true })
    await manager.waitForBackground()
    // The commit updates local session state and schedules another save.
    // Drain it before removing the isolated manager directory.
    await manager.flushAll({ commit: false })
    const batch = requests.find((r) => r.path.endsWith("/messages/batch"))
    assert.ok(batch)
    assert.match(batch.body, /skill-experience/)
    assert.match(batch.body, /"tool_name":"skill"/)
    assert.doesNotMatch(batch.body, /"tool_name":"(?:find|read)"/)
    assert.ok(requests.some((r) => r.path.endsWith("/commit")))
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await rm(dir, { recursive: true, force: true })
  }
})
