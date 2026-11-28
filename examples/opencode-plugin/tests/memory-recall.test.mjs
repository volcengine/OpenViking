import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createMemoryRecall, extractCurrentUserText } from "../lib/memory-recall.mjs"

test("extractCurrentUserText skips synthetic session context before user text", () => {
  const query = extractCurrentUserText([
    {
      type: "text",
      text: "<openviking-context source=\"session-start\">Profile</openviking-context>",
      synthetic: true,
    },
    {
      type: "text",
      text: "Find the deployment notes for the recall test",
    },
  ])

  assert.equal(query, "Find the deployment notes for the recall test")
})

test("extractCurrentUserText still rejects non-synthetic OpenViking context text", () => {
  const query = extractCurrentUserText([
    {
      type: "text",
      text: "<openviking-context>Injected context</openviking-context>",
    },
    {
      type: "text",
      text: "Find the deployment notes for the recall test",
    },
  ])

  assert.equal(query, null)
})

async function startRecallServer() {
  const requests = []
  const server = createServer((req, res) => {
    let raw = ""
    req.on("data", (chunk) => {
      raw += chunk
    })
    req.on("end", () => {
      requests.push({ path: req.url, body: raw ? JSON.parse(raw) : null })
      res.writeHead(200, { "content-type": "application/json" })
      res.end(JSON.stringify({
        result: {
          rendered: '<memory uri="viking://user/default/memories/a.md" type="events">body</memory>',
          entries: [{ uri: "viking://user/default/memories/a.md" }],
          stats: { used_tokens: 42, rewrite: "off" },
        },
      }))
    })
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  return { server, requests, endpoint: `http://127.0.0.1:${server.address().port}` }
}

async function runRecall(config) {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-opencode-recall-"))
  const previousStateDir = process.env.OPENVIKING_STATE_DIR
  process.env.OPENVIKING_STATE_DIR = stateDir
  const { server, requests, endpoint } = await startRecallServer()
  try {
    const recall = createMemoryRecall({
      config: { enabled: true, endpoint, ...config },
      sessionManager: { getMappedSessionId: () => "ov-session-1" },
    })
    const block = await recall.buildRelevantMemories({}, [
      { type: "text", text: "Find the deployment notes for the exclude test" },
    ])
    return { block, requests }
  } finally {
    if (previousStateDir === undefined) delete process.env.OPENVIKING_STATE_DIR
    else process.env.OPENVIKING_STATE_DIR = previousStateDir
    await rm(stateDir, { recursive: true, force: true })
    server.close()
  }
}

test("recall forwards configured recallExcludeUris as exclude_uris", async () => {
  const { block, requests } = await runRecall({
    recallExcludeUris: ["viking://agent/skills", "viking://user/default/resources"],
  })

  assert.ok(block, "recall block expected")
  const search = requests.find((call) => call.path === "/api/v1/search/search")
  assert.ok(search, "context search request expected")
  assert.equal(search.body.mode, "context")
  assert.deepEqual(search.body.exclude_uris, [
    "viking://agent/skills",
    "viking://user/default/resources",
  ])
})

test("recall omits exclude_uris when no recallExcludeUris are configured", async () => {
  const { requests } = await runRecall({})

  const search = requests.find((call) => call.path === "/api/v1/search/search")
  assert.ok(search, "context search request expected")
  assert.equal(search.body.exclude_uris, undefined)
})
