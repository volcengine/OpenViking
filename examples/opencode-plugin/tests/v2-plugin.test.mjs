import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { injectV2Context, startV2Plugin } from "../lib/v2-plugin.mjs"
import { readRequestBody, scrubOpenVikingEnv, withMockOpenViking, writeJson } from "../../memory-plugin-shared/testing/support.mjs"

const pluginRoot = fileURLToPath(new URL("..", import.meta.url))
const BUNDLED_SKILLS = ["openviking-memory", "openviking-rule-search", "openviking-skills", "ov-experience-memory", "ov-memory-doctor"]

// Every path the plugin writes or reads points into a fresh directory, and the
// server is a loopback mock.
async function withPluginEnv(url, fn, plugin = {}) {
  const restore = scrubOpenVikingEnv()
  const dir = await mkdtemp(join(tmpdir(), "ov-oc-v2-"))
  try {
    const ovcli = join(dir, "ovcli.conf")
    await writeFile(ovcli, JSON.stringify({ url, plugin: { dataDir: join(dir, "data"), repoContext: false, ...plugin } }))
    Object.assign(process.env, {
      OPENVIKING_CLI_CONFIG_FILE: ovcli,
      OPENVIKING_CONFIG_FILE: join(dir, "ov.conf"),
      OPENVIKING_HOME: join(dir, "home"),
      OPENVIKING_HOOK_STATE_DIR: join(dir, "hook-state"),
      OPENVIKING_PENDING_DIR: join(dir, "pending"),
    })
    return await fn(dir)
  } finally {
    restore()
    await rm(dir, { recursive: true, force: true })
  }
}

function contextFixture({ messages = [], events = [] } = {}) {
  const hooks = {}
  const registered = []
  const skillSources = []
  return {
    hooks,
    registered,
    skillSources,
    ctx: {
      skill: {
        transform: async (callback) => callback({
          list: () => skillSources,
          get: (id) => skillSources.find((skill) => skill.id === id),
          add: (skill) => skillSources.push(skill),
        }),
      },
      location: { directory: "/repo/a" },
      mcp: {
        transform: async (callback) => callback({
          get: () => undefined,
          set: (name, config) => registered.push([name, config]),
        }),
      },
      tool: { hook: async (name, fn) => { hooks[name] = fn } },
      session: {
        hook: async (name, fn) => { hooks[name] = fn },
        context: async () => messages,
        get: async ({ sessionID }) => ({ data: { id: sessionID, location: { directory: "/repo/a" } } }),
      },
      event: { subscribe: () => (async function* replay() { yield* events })() },
    },
  }
}

const unreachable = "http://127.0.0.1:9"

test("startV2Plugin registers the MCP server, the bundled skills and the v2 hooks", async () => {
  await withPluginEnv(unreachable, async () => {
    const { ctx, hooks, registered, skillSources } = contextFixture()
    const cleanup = await startV2Plugin(ctx, { pluginRoot })
    assert.deepEqual(skillSources.map((skill) => skill.id).sort(), BUNDLED_SKILLS)
    for (const skill of skillSources) {
      assert.ok(skill.description.length > 20 && !skill.description.includes("\n"))
      assert.match(skill.content, /^\s*# /)
    }
    assert.equal(registered[0][0], "openviking")
    assert.equal(registered[0][1].codemode, false)
    assert.deepEqual(Object.keys(hooks).sort(), ["compaction", "context", "execute.after", "execute.before", "prompt"])
    await cleanup()
  })
})

for (const disabled of [false, true]) {
  test(`bundled skills follow MCP registration when transforms run after setup, disabled=${disabled}`, async () => {
    await withPluginEnv(unreachable, async () => {
      const { ctx } = contextFixture()
      const transforms = []
      ctx.mcp.transform = async (callback) => { transforms.push(callback) }
      ctx.skill.transform = async (callback) => { transforms.push(callback) }
      const cleanup = await startV2Plugin(ctx, { pluginRoot })
      const [mcp, skill] = transforms
      const skills = new Map()
      mcp({ get: () => (disabled ? { disabled: true } : undefined), set: () => {} })
      skill({ list: () => [...skills.values()], get: (id) => skills.get(id), add: (value) => skills.set(value.id, value) })
      assert.deepEqual([...skills.keys()].sort(), disabled ? [] : BUNDLED_SKILLS)
      await cleanup()
    })
  })
}

test("existing skills override bundled skills by ID or name", async () => {
  await withPluginEnv(unreachable, async () => {
    const { ctx, skillSources } = contextFixture()
    skillSources.push(
      { id: "project-memory", name: "openviking-memory", path: "/p/SKILL.md", content: "" },
      { id: "openviking-skills", name: "Custom skills", path: "/q/SKILL.md", content: "" },
    )
    const cleanup = await startV2Plugin(ctx, { pluginRoot })
    assert.deepEqual(skillSources.map((skill) => skill.id).sort(), [
      "openviking-rule-search", "openviking-skills", "ov-experience-memory", "ov-memory-doctor", "project-memory",
    ])
    await cleanup()
  })
})

test("tool hooks apply the flow's deny, session fill and notice", async () => {
  await withPluginEnv(unreachable, async () => {
    const { ctx, hooks } = contextFixture()
    const cleanup = await startV2Plugin(ctx, { pluginRoot })
    await assert.rejects(
      () => hooks["execute.before"]({ sessionID: "ses_1", tool: "read", input: { filePath: "viking://resources/a" } }),
      /openviking_read/,
    )
    const input = { query: "x" }
    await hooks["execute.before"]({ sessionID: "ses_1", tool: "openviking_session_history", input })
    assert.equal(input.session, "oc-ses_1")

    const original = [{ type: "text", text: "out" }, { type: "image", data: "x" }]
    const result = { content: structuredClone(original) }
    await hooks["execute.after"]({ status: "completed", sessionID: "ses_1", tool: "shell", input: { command: "cat viking://resources/a" }, result })
    assert.deepEqual(result.content.slice(0, 2), original)
    assert.match(result.content[2].text, /openviking_read/)
    await cleanup()
  })
})

test("prompt context reaches the user message once, with only the newest start block", () => {
  const start = '<openviking-context source="session-start">\nprofile\n</openviking-context>'
  const older = { role: "user", content: [{ type: "text", text: start, metadata: { openviking: true } }] }
  const message = { role: "user", content: [{ type: "text", text: "q" }], metadata: { openviking: { context: [start] } } }
  const event = { system: [], messages: [older, message] }
  const repoContext = { getRepoSystemPrompt: () => "repo prompt" }
  injectV2Context(event, repoContext)
  injectV2Context(event, repoContext)
  assert.equal(message.content.length, 2)
  assert.equal(message.content[0].text, start)
  assert.deepEqual(older.content, [])
  assert.deepEqual(event.system, [{ type: "text", text: "repo prompt" }])
})

test("execution ends capture each message once and session deletion commits", async () => {
  const bodies = []
  await withMockOpenViking(async (req, res) => {
    const body = await readRequestBody(req)
    if (req.method !== "GET") bodies.push([req.method, new URL(req.url, "http://x").pathname, body])
    writeJson(res, { status: "ok", result: {} })
  }, async (url) => {
    await withPluginEnv(url, async () => {
      const done = { id: "ses_a", type: "session.execution.succeeded", data: { sessionID: "ses_a" } }
      const { ctx } = contextFixture({
        messages: [
          { id: "msg_user", type: "user", text: "question" },
          { id: "msg_assistant", type: "assistant", content: [{ type: "text", text: "answer" }] },
        ],
        events: [
          { type: "session.created", data: { sessionID: "ses_a", location: { directory: "/repo/a" } } },
          { type: "session.created", data: { sessionID: "ses_b", location: { directory: "/repo/b" } } },
          done,
          { type: "session.execution.succeeded", data: { sessionID: "ses_b" } },
          done,
          { type: "session.deleted", data: { sessionID: "ses_a" } },
        ],
      })
      const cleanup = await startV2Plugin(ctx, { pluginRoot })
      await cleanup()
    })
  })
  const writes = bodies.filter(([, path]) => path.includes("/messages"))
  assert.equal(writes.length, 1)
  assert.match(writes[0][1], /\/sessions\/oc-ses_a\/messages/)
  assert.deepEqual(writes[0][2].messages.map((message) => message.role), ["user", "assistant"])
  assert.ok(bodies.some(([method, path]) => method === "POST" && path === "/api/v1/sessions/oc-ses_a/commit"))
  assert.ok(!bodies.some(([, path]) => path.includes("ses_b")))
})
