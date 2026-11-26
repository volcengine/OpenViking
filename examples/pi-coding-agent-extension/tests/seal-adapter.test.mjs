import test from "node:test"
import assert from "node:assert/strict"
import {
  AGENT_TOOLS,
  SEAL_NOTE_TYPE,
  SEAL_SESSION_TEXT,
  createSealHandlers,
  createSealPost,
} from "../lib/seal-adapter.mjs"
import { guardVikingUriToolCall, noticeVikingUriToolResult } from "../lib/uri-guard-adapter.mjs"

const SAVED = "ghp_savedTokenValue0123456789"
const SAVED_REF = "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/token}}"
const TYPED = "sk-typedSecretValue987654"
const TYPED_REF = "{{ov_privacy:alice:viking://~/privacy/auto/openai-k3j2#/values/token}}"

/** A seal and resolve endpoint over a two-entry vault, as the server answers. */
function fakeServer({ fail = null } = {}) {
  const calls = []
  const post = async (path, body) => {
    calls.push({ path, body })
    if (fail) return fail(path, body)
    if (path.endsWith("/seal")) {
      const texts = body.texts.map(({ text, enroll }) => {
        const vault = [[SAVED, SAVED_REF, false]]
        if (enroll) vault.push([TYPED, TYPED_REF, true])
        const points = Array.from(text)
        const replaced = []
        let out = ""
        for (let i = 0; i < points.length;) {
          const rest = points.slice(i).join("")
          const hit = vault.find(([value]) => rest.startsWith(value))
          if (hit) {
            const len = Array.from(hit[0]).length
            replaced.push({ start: i, end: i + len, reference: hit[1], created: false, temporary: hit[2] })
            out += hit[1]
            i += len
          } else {
            out += points[i]
            i += 1
          }
        }
        return replaced.length ? { text: out, replaced } : null
      })
      return { status: 200, json: { status: "ok", result: { revision: "r1", texts } } }
    }
    if (body.selector === SAVED_REF) return { status: 200, json: { status: "ok", result: { value: SAVED } } }
    return { status: 404, json: { status: "error", error: { message: "not found" } } }
  }
  return { post, calls }
}

function handlers(server, options = {}) {
  const told = []
  const h = createSealHandlers({
    post: server.post,
    ownToolNames: () => ["openviking_read"],
    tell: (_ctx, message) => told.push({ message }),
    ...options,
  })
  return { ...h, told }
}

test("input is sealed with enroll and the note comes with the run it starts", async () => {
  const server = fakeServer()
  const h = handlers(server)
  const text = `use ${TYPED} and ${SAVED}`
  const result = await h.onInput({ text, source: "interactive" }, {})
  assert.equal(result.action, "transform")
  assert.equal(result.text, `use ${TYPED_REF} and ${SAVED_REF}`)
  assert.equal(server.calls[0].body.texts[0].enroll, true)

  const start = await h.onBeforeAgentStart({ systemPrompt: "BASE", prompt: result.text, messageType: "user" })
  assert.equal(start.systemPrompt, `BASE\n\n${SEAL_SESSION_TEXT}`)
  assert.equal(start.message.customType, SEAL_NOTE_TYPE)
  assert.equal(start.message.display, false)
  assert.match(start.message.content, /^OpenViking stored a secret from the user's message/)
  assert.ok(start.message.content.includes(`values={"token": "${TYPED_REF}"}`))
  assert.ok(!start.message.content.includes(SAVED_REF), "only temporary references are listed")

  // The note belongs to that one run.
  const next = await h.onBeforeAgentStart({ systemPrompt: "BASE", prompt: "next", messageType: "user" })
  assert.equal(next.message, undefined)
  assert.equal(next.systemPrompt, `BASE\n\n${SEAL_SESSION_TEXT}`)
})

test("the note survives a /skill: expansion of the sealed input", async () => {
  const h = handlers(fakeServer())
  const result = await h.onInput({ text: `/skill:deploy with ${TYPED}`, source: "interactive" }, {})
  const expanded = `<skill name="deploy">Deploy the app.</skill>\n\nwith ${TYPED_REF}`
  const start = await h.onBeforeAgentStart({ systemPrompt: "", prompt: expanded, messageType: "user" })
  assert.equal(result.text, `/skill:deploy with ${TYPED_REF}`)
  assert.equal(start.message.customType, SEAL_NOTE_TYPE)
})

test("a saved secret is replaced without a note; nothing to replace continues", async () => {
  const h = handlers(fakeServer())
  const sealed = await h.onInput({ text: `token ${SAVED}`, source: "interactive" }, {})
  assert.deepEqual(sealed, { action: "transform", text: `token ${SAVED_REF}`, images: undefined })
  assert.equal(await h.onBeforeAgentStart({ systemPrompt: "", prompt: sealed.text }).message, undefined)
  assert.deepEqual(await h.onInput({ text: "hello there", source: "interactive" }, {}), { action: "continue" })
})

test("a message queued into a running turn carries its note in the text", async () => {
  const h = handlers(fakeServer())
  const result = await h.onInput({ text: `also ${TYPED}`, streamingBehavior: "steer" }, {})
  assert.ok(result.text.startsWith(`also ${TYPED_REF}\n\nOpenViking stored a secret`))
  assert.equal(await h.onBeforeAgentStart({ systemPrompt: "", prompt: "x" }).message, undefined)
})

test("empty input passes without a request", async () => {
  const server = fakeServer()
  const h = handlers(server)
  assert.deepEqual(await h.onInput({ text: "   " }, {}), { action: "continue" })
  assert.equal(server.calls.length, 0)
})

test("text starting with a slash is sealed: pi sends it to the model", async () => {
  const h = handlers(fakeServer())
  const result = await h.onInput({ text: `/skill:deploy ${SAVED}` }, {})
  assert.equal(result.action, "transform")
  assert.equal(result.text, `/skill:deploy ${SAVED_REF}`)
})

test("a failure to tell the user still stops the input", async () => {
  const server = fakeServer({ fail: async () => { throw new Error("connect ECONNREFUSED") } })
  const h = createSealHandlers({ post: server.post, tell: () => { throw new Error("no ui") } })
  assert.deepEqual(await h.onInput({ text: `use ${SAVED}`, source: "interactive" }, {}), { action: "handled" })
})

test("a sealing failure stops the input and tells the user why and what to do", async () => {
  const server = fakeServer({ fail: async () => { throw new Error("connect ECONNREFUSED") } })
  const h = handlers(server)
  const result = await h.onInput({ text: `use ${SAVED}`, source: "interactive" }, {})
  assert.deepEqual(result, { action: "handled" })
  assert.equal(h.told.length, 1)
  assert.match(h.told[0].message, /not sent.*cannot reach the OpenViking server: connect ECONNREFUSED/)
  assert.match(h.told[0].message, /Try again once the OpenViking server answers, or turn sealing off with sealSecrets: false \(OPENVIKING_SEAL_SECRETS=0\)/)
  assert.ok(!h.told[0].message.includes(SAVED))
})

test("tool_call puts real values in place, in the event's own input", async () => {
  const h = handlers(fakeServer())
  const input = { command: `curl -H 'Authorization: ${SAVED_REF}' https://x`, timeout: 5 }
  const event = { toolName: "bash", input }
  assert.equal(await h.onToolCall(event), undefined)
  assert.equal(event.input, input)
  assert.equal(input.command, `curl -H 'Authorization: ${SAVED}' https://x`)
  assert.equal(input.timeout, 5)
})

test("tool_call leaves OpenViking, own and agent tools alone", async () => {
  const server = fakeServer()
  const h = handlers(server)
  for (const toolName of ["openviking_privacy_upsert", "openviking_read", ...AGENT_TOOLS]) {
    const input = { values: { token: SAVED_REF } }
    await h.onToolCall({ toolName, input })
    assert.equal(input.values.token, SAVED_REF, toolName)
  }
  assert.equal(server.calls.length, 0)
})

test("a resolve failure blocks the call", async () => {
  const broken = handlers(fakeServer({ fail: async () => ({ status: 500, json: { status: "error", error: { message: "boom" } } }) }))
  const decision = await broken.onToolCall({ toolName: "bash", input: { command: `echo ${SAVED_REF}` } })
  assert.equal(decision.block, true)
  assert.match(decision.reason, /could not put the secret values in.*HTTP 500.*did not run.*OPENVIKING_SEAL_SECRETS=0/)
})

test("tool_result seals text, details and structuredContent; images and the rest stay", async () => {
  const server = fakeServer()
  const h = handlers(server)
  const image = { type: "image", data: "aGVsbG8=", mimeType: "image/png" }
  const event = {
    toolName: "bash",
    content: [{ type: "text", text: `out ${SAVED}` }, image],
    details: { truncation: null, fullOutputPath: "/tmp/x", note: `saw ${SAVED}` },
    structuredContent: { token: SAVED },
    isError: false,
  }
  const patch = await h.onToolResult(event)
  assert.deepEqual(patch.content, [{ type: "text", text: `out ${SAVED_REF}` }, image])
  assert.equal(patch.content[1], image)
  assert.deepEqual(patch.details, { truncation: null, fullOutputPath: "/tmp/x", note: `saw ${SAVED_REF}` })
  assert.deepEqual(patch.structuredContent, { token: SAVED_REF })
  assert.equal(patch.isError, undefined)
  // One request for the whole result, enroll off by default.
  assert.equal(server.calls.length, 1)
  assert.ok(server.calls[0].body.texts.every((t) => t.enroll === false))
})

test("tool_result enrolls with enrollToolSecrets and returns nothing when nothing changed", async () => {
  const server = fakeServer()
  const h = handlers(server, { enrollToolSecrets: true })
  const patch = await h.onToolResult({ toolName: "read", content: [{ type: "text", text: `key=${TYPED}` }] })
  assert.deepEqual(patch, { content: [{ type: "text", text: `key=${TYPED_REF}` }] })
  assert.equal(server.calls[0].body.texts[0].enroll, true)
  assert.equal(await h.onToolResult({ toolName: "read", content: [{ type: "text", text: "plain output" }] }), undefined)
})

test("a tool_result that cannot be sealed is withheld and marked as an error", async () => {
  const h = handlers(fakeServer({ fail: async () => ({ status: 200, json: { status: "ok", result: { texts: [] } } }) }))
  const patch = await h.onToolResult({
    toolName: "bash",
    content: [{ type: "text", text: `out ${SAVED}` }],
    details: { note: SAVED },
  })
  assert.equal(patch.isError, true)
  assert.deepEqual(patch.details, {})
  assert.equal(patch.content.length, 1)
  assert.match(patch.content[0].text, /^\[OpenViking withheld this tool result: sealing secrets in it failed \(malformed response/)
  assert.ok(!JSON.stringify(patch).includes(SAVED))
})

test("with the URI notice after it, the notice survives sealing and withholding", async () => {
  // index.ts registers sealing before the notice, and pi hands each tool_result
  // handler the result the previous one left.
  const run = async (server) => {
    const event = { toolName: "bash", input: { command: "cat viking://resources/a.md" }, content: [{ type: "text", text: SAVED }] }
    const patch = await handlers(server).onToolResult(event)
    if (patch?.content) event.content = patch.content
    return noticeVikingUriToolResult(event).content
  }
  const sealed = await run(fakeServer())
  assert.equal(sealed[0].text, SAVED_REF)
  assert.match(sealed[1].text, /viking:\/\//)
  const withheldContent = await run(fakeServer({ fail: async () => { throw new Error("down") } }))
  assert.match(withheldContent[0].text, /withheld/)
  assert.match(withheldContent[1].text, /viking:\/\//)
})

test("the URI guard, run first, still sees the reference the model wrote", async () => {
  const event = { toolName: "read", input: { path: `viking://resources/${SAVED_REF}` } }
  assert.equal(guardVikingUriToolCall(event)?.block, true)
})

test("createSealPost hands the core the envelope and turns no answer into a rejection", async () => {
  const seen = []
  const fetchJSON = async (path, init, options) => {
    seen.push({ path, init, options })
    if (path === "/ok") return { ok: true, status: 200, result: { revision: "r" } }
    if (path === "/err") return { ok: false, status: 403, result: null, error: { message: "denied" } }
    if (path === "/slow") return { ok: false, status: 0, result: null, error: { name: "AbortError", aborted: true, message: "aborted" } }
    return { ok: false, status: 0, result: null, error: { message: "ECONNREFUSED" } }
  }
  const post = createSealPost(fetchJSON)
  assert.deepEqual(await post("/ok", { a: 1 }), { status: 200, json: { status: "ok", result: { revision: "r" } } })
  assert.equal(seen[0].init.method, "POST")
  assert.equal(seen[0].init.body, '{"a":1}')
  assert.equal(seen[0].options.timeoutMs, 30000)
  assert.deepEqual(await post("/err", {}), { status: 403, json: { status: "error", error: { message: "denied" } } })
  await assert.rejects(post("/slow", {}), (error) => error.name === "AbortError" && error.aborted === true)
  await assert.rejects(post("/down", {}), /ECONNREFUSED/)
})

// ── a server that cannot seal ───────────────────────────────────────────

const TOO_OLD = { check: () => ({ supported: false, required: "0.5.7", actual: "0.5.6" }) }
const NO_HEALTH = { check: async () => ({ supported: false, required: "0.5.7", actual: null }) }

test("a server too old or without a readable version stops at each hook point without a call", async () => {
  for (const [options, version, hint] of [
    [TOO_OLD, /is version 0\.5\.6, and sealing secrets needs 0\.5\.7 or newer/, /Upgrade the OpenViking server/],
    [NO_HEALTH, /version could not be read .* needs 0\.5\.7 or newer/, /running and reachable/],
  ]) {
    const server = fakeServer()
    const h = handlers(server, options)
    assert.deepEqual(await h.onInput({ text: `use ${SAVED}`, source: "interactive" }, {}), { action: "handled" })
    assert.match(h.told[0].message, /not sent, because the OpenViking server/)
    assert.match(h.told[0].message, version)
    assert.match(h.told[0].message, hint)
    const decision = await h.onToolCall({ toolName: "bash", input: { command: `echo ${SAVED_REF}` } })
    assert.equal(decision.block, true)
    assert.match(decision.reason, hint)
    const patch = await h.onToolResult({ toolName: "bash", content: [{ type: "text", text: `out ${SAVED}` }] })
    assert.equal(patch.isError, true)
    assert.ok(!JSON.stringify(patch).includes(SAVED))
    assert.equal(server.calls.length, 0)
  }
})
