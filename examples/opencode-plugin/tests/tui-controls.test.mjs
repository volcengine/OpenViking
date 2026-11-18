import test from "node:test"
import assert from "node:assert/strict"
import { startTuiControls, displayText } from "../lib/tui-controls.mjs"
import { inspectV2Recall } from "../lib/v2-recall-inspector.mjs"
import { registerV2Controls } from "../lib/v2-controls.mjs"

function fixture(methods = {}) {
  const calls = [], dialogs = [], slots = [], layers = []
  let close, sessionID = "current", confirmed = true, choice
  const report = { connection: { state: "error" }, installation: { state: "ok" }, mcp: { state: "disabled" },
    skills: { state: "disabled" }, capture: { state: "disabled" }, text: "Doctor: disconnected", effective: { apiKey: "must-not-render" } }
  const rpc = Object.fromEntries(["status", "doctor", "commit", "recall"].map((action) => [action, async (input, options) => {
    calls.push({ action, input, options })
    return methods[action]?.(input, options) ?? (action === "commit" ? { status: "accepted", extraction: "not_verified" } : report)
  }]))
  const context = { options: {}, location: { directory: "/fixture" }, client: { rpc: () => rpc },
    keymap: { layer: (fn) => layers.push(fn()) },
    ui: { router: { current: () => sessionID ? { type: "session", sessionID } : { type: "home" } },
      slot: (slot) => { slots.push(slot); return () => slots.splice(slots.indexOf(slot), 1) },
      dialog: { set: () => {}, show: (render, onClose) => { close?.(); dialogs.push(render()); close = onClose },
        clear: () => { close?.(); close = undefined }, confirm: async () => typeof confirmed === "function" ? confirmed() : confirmed,
        select: async () => { context.ui.dialog.clear(); return choice } } } }
  const start = () => startTuiControls(context, { renderDialog: (title, text) => ({ title, text }) })
  const run = (input) => { choice = input; return layers.at(-1).commands[0].run() }
  return { context, calls, dialogs, slots, layers, run, start,
    session: (id) => { sessionID = id }, confirm: (value) => { confirmed = value }, dismiss: () => context.ui.dialog.clear() }
}

test("TUI reads are native dialogs, disconnected/disabled states stay visible without configuration output", async () => {
  const f = fixture(); const stop = f.start()
  assert.deepEqual(f.layers[0].commands[0].slash, { name: "ov" })
  assert.equal(f.layers[0].mode, "global")
  assert.match(f.layers[0].commands[0].title, /\/ov/)
  await f.run("doctor")
  assert.equal(f.dialogs.at(-1).text, "Doctor: disconnected")
  assert.doesNotMatch(JSON.stringify(f.dialogs), /must-not-render/)
  assert.deepEqual(f.calls.map((c) => c.action), ["doctor"])
  assert.deepEqual(f.calls[0].options.location, f.context.location)
  stop(); assert.equal(f.slots.length, 0)
})

test("commit needs explicit confirmation, stays on current session, and only reports acceptance", async () => {
  const f = fixture(); f.start(); f.confirm(false)
  await f.run("commit"); assert.equal(f.calls.length, 0)
  f.confirm(() => { f.session("changed"); return true })
  await f.run("commit"); assert.equal(f.calls.length, 0)
  f.dismiss()
  f.confirm(true)
  await f.run("commit")
  assert.deepEqual(f.calls[0].input, { sessionID: "changed" })
  assert.match(f.dialogs.at(-1).text, /accepted.*completion has not been verified/s)
  f.dismiss(); f.session(null); await f.run("commit"); f.dismiss(); await f.run("recall")
  assert.equal(f.calls.length, 1)
})

test("reload/disable abort requests, remove slot, and never resurrect dismissed dialogs", async () => {
  let resolve
  const f = fixture({ doctor: () => new Promise((done) => { resolve = done }) })
  const stop = f.start(); const pending = f.run("doctor")
  await Promise.resolve(); f.dismiss()
  assert.equal(f.calls[0].options.signal.aborted, true)
  resolve({ text: "late", connection: { state: "ok" }, capture: { state: "ok" } })
  await pending; assert.equal(f.dialogs.length, 1)
  stop()
  await f.run("status"); assert.equal(f.calls.length, 1)
  // The real host disposes its keymap scope on reload; a new setup gets one layer.
  f.layers.length = 0; const stopAgain = f.start()
  assert.equal(f.layers.length, 1); assert.equal(f.slots.length, 0); stopAgain()
  assert.equal(f.slots.length, 0)
})

test("disabling while commit confirmation is open prevents a late confirmation from writing", async () => {
  const f = fixture(); let confirm
  f.confirm(() => new Promise((resolve) => { confirm = resolve }))
  const stop = f.start(); const pending = f.run("commit"); await Promise.resolve()
  stop(); confirm(true); await pending
  assert.equal(f.calls.length, 0)
})

test("RPC failures do not echo arbitrary error secrets or terminal escape controls", async () => {
  const f = fixture({ doctor: () => { throw { type: "operation_failed", message: "Bearer secret-value" } } })
  f.start(); await f.run("doctor")
  assert.match(f.dialogs.at(-1).text, /operation_failed/)
  assert.doesNotMatch(JSON.stringify(f.dialogs), /secret-value/)
  assert.equal(displayText("\x1b]52;clipboard\x07\nreadable"), "]52;clipboard\nreadable")
})

test("recall inspector reads exact persisted context with turn/source and explicit live-state limits", async () => {
  const original = "<openviking-context>Source viking://user/fixture/memories/test.md\nactual injected secret-key</openviking-context>"
  const ctx = { storage: { get: async (key) => {
    assert.equal(key, "recall-ledger/current")
    return { version: 1, entries: [["turn-1", original]] }
  } } }
  const view = await inspectV2Recall(ctx, { apiKey: "secret-key" }, "current")
  assert.equal(view.state, "available"); assert.equal(view.pending, null); assert.equal(view.error, null)
  assert.equal(view.turns[0].messageID, "turn-1")
  assert.equal(view.turns[0].text, original.replace("secret-key", "[redacted]"))
  assert.deepEqual(view.turns[0].sources, ["viking://user/fixture/memories/test.md"])
  const f = fixture({ recall: () => view }); f.start(); await f.run("recall")
  assert.match(f.dialogs.at(-1).text, /Turn: turn-1/)
  assert.match(f.dialogs.at(-1).text, /latest unsaved turn may be absent/)
  assert.equal((await inspectV2Recall({}, {}, "current")).state, "unavailable")
  assert.equal((await inspectV2Recall({ storage: { get: async () => ({ version: 2 }) } }, {}, "current")).state, "error")
  const bounded = await inspectV2Recall({ storage: { get: async () => ({ version: 1, entries: Array.from({ length: 40 }, (_, i) => [`turn-${i}`, "x".repeat(15000)]) }) } }, {}, "current")
  assert.equal(bounded.turns[0].messageID, "turn-39")
  assert.ok(bounded.turns.every(({ text }) => text.length > 0))
  assert.ok(bounded.turns.reduce((n, t) => n + t.text.length, 0) <= 60000)
})

test("recall RPC rejects foreign sessions before reading ledger and never captures or commits", async () => {
  let handlers, reads = 0
  const ctx = { location: { directory: "/here", project: { id: "a" } },
    rpc: { register: async (_, value) => { handlers = value } },
    session: { get: async () => ({ location: { directory: "/here" }, projectID: "b" }) },
    storage: { get: async () => { reads++; return undefined } } }
  await registerV2Controls(ctx, { config: {} }, {})
  await assert.rejects(handlers.recall({ sessionID: "foreign" }), /another OpenCode location/)
  assert.equal(reads, 0)
  ctx.session.get = async () => ({ location: ctx.location, projectID: "a" })
  const view = await handlers.recall({ sessionID: "current" })
  assert.equal(view.state, "unavailable"); assert.equal(reads, 1)
})


test("recall inspector uses stored prompt metadata on hosts without a replay ledger", async () => {
  const view = await inspectV2Recall({ session: { context: async ({ sessionID }) => {
    assert.equal(sessionID, "current")
    return [
      { id: "msg_1", type: "user", text: "transcript must not surface", metadata: { openviking: { context: ["saved prompt context"] } } },
      { id: "msg_2", type: "assistant", metadata: { openviking: { context: ["assistant text"] } } },
    ]
  } } }, {}, "current")
  assert.equal(view.source, "stored_prompt_metadata")
  assert.deepEqual(view.turns.map(({ messageID, text }) => [messageID, text]), [["msg_1", "saved prompt context"]])
  assert.doesNotMatch(JSON.stringify(view), /transcript must not surface|assistant text/)
})
