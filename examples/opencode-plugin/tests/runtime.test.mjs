import test from "node:test"
import assert from "node:assert/strict"
import { checkServiceHealth } from "../lib/runtime.mjs"

const CONFIG = {
  endpoint: "http://127.0.0.1:1933/",
  apiKey: "sk-opencode",
  account: "acct-a",
  user: "user-a",
  sendIdentityHeaders: true,
  userAgent: "openviking-opencode/9.9.9",
  timeoutMs: 5000,
}

test("the health probe presents the same credentials as every other call", async (t) => {
  const calls = []
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({ status: "healthy" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })

  assert.equal(await checkServiceHealth(CONFIG), true)
  assert.equal(calls[0].url, "http://127.0.0.1:1933/health")
  assert.equal(calls[0].init.headers["Authorization"], "Bearer sk-opencode")
  assert.equal(calls[0].init.headers["X-OpenViking-Account"], "acct-a")
  assert.equal(calls[0].init.headers["User-Agent"], "openviking-opencode/9.9.9")
})

test("a rejected or unreachable probe is not a healthy server", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    status: "error",
    error: { message: "missing credentials" },
  }), { status: 401, headers: { "Content-Type": "application/json" } }))
  assert.equal(await checkServiceHealth(CONFIG), false)

  t.mock.restoreAll()
  t.mock.method(globalThis, "fetch", async () => {
    throw new Error("fetch failed")
  })
  assert.equal(await checkServiceHealth(CONFIG), false)
})
