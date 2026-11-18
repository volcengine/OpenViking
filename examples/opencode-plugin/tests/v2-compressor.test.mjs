import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createV2Compressor } from "../lib/v2-compressor.mjs"
import { compressRecallContext, recallDigestCacheKey } from "../lib/shared/recall-compress-core.mjs"

const config = { recallRewrite: "client", recallCompressModel: "fixture/model/path", recallCompressTimeoutMs: 30 }
const uri = "viking://user/fixture/memories/decision.md"
const input = { query: "decision", rendered: `${uri}\n${"fact ".repeat(400)}`, entries: [{ uri }] }
const ctx = (generate) => ({ experimental: { generate: { text: generate } } })

test("native compression is opt-in, explicit-model only, and requires the supported API", () => {
  let calls = 0
  const host = ctx(() => { calls++; throw Error("must not generate") })
  for (const override of [{ recallRewrite: "off" }, { recallRewrite: "server" }, { recallCompressModel: "" },
    { recallCompressModel: "model" }, { recallCompressModel: "provider/" }, { recallCompressBaseUrl: "https://fixture.invalid" }]) {
    assert.equal(createV2Compressor(host, { ...config, ...override }), null)
  }
  assert.equal(createV2Compressor({ generate: host.experimental.generate }, config), null)
  assert.equal(calls, 0)
})

test("native stateless generation uses the exact V2 shape and shared fresh/cache citation handling", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-v2-compress-"))
  try {
    let calls = 0
    const served = `viking://resources/${"a".repeat(140)}.md`
    const request = { ...input, entries: [{ uri: served }] }
    const compressor = createV2Compressor(ctx(async (value, options) => {
      calls++
      assert.deepEqual(Object.keys(value).sort(), ["model", "prompt"])
      assert.deepEqual(value.model, { providerID: "fixture", id: "model/path" })
      assert.ok(options.signal instanceof AbortSignal)
      return { text: `- ${"x".repeat(450)} source: ${served}` }
    }), { ...config, dataDir: dir })
    const cachePath = compressor.cachePath("session")
    const runCompressor = compressor.forSession("session").run
    const first = await compressRecallContext({ ...request, runCompressor, cachePath })
    assert.equal(first.status, "ok")
    assert.ok(first.context.endsWith(served))
    assert.equal(first.context.split("\n")[1].slice(2).length, 500)
    assert.deepEqual(await compressRecallContext({ ...request, runCompressor, cachePath }), first)
    assert.equal(calls, 1)
    // A matching legacy cache never bypasses shared citation repair.
    await writeFile(cachePath, JSON.stringify({ key: recallDigestCacheKey(request), digest: "- forged source: viking://foreign/forged.md" }))
    assert.deepEqual(await compressRecallContext({ ...request, runCompressor, cachePath }), first)
    assert.equal(calls, 2)
    assert.notEqual(cachePath, compressor.cachePath("child"))
    assert.notEqual(cachePath, createV2Compressor(ctx(() => {}), { ...config, dataDir: dir, account: "other" }).cachePath("session"))
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test("timeout, interruption and cleanup settle even if the host ignores cancellation", async () => {
  for (const action of ["timeout", "session", "cleanup"]) {
    let hostSignal
    let started
    const ready = new Promise((resolve) => { started = resolve })
    const lifetime = new AbortController()
    const compressor = createV2Compressor(ctx((_value, options) => {
      hostSignal = options.signal
      started()
      return new Promise(() => {})
    }), config, { signal: lifetime.signal })
    const pending = compressor.forSession("session").run("prompt")
    await ready
    if (action === "session") compressor.cancel("session")
    if (action === "cleanup") lifetime.abort()
    assert.equal(await pending, null)
    assert.equal(hostSignal.aborted, true)
  }
})

test("generation failures and invalid or oversized text cannot become digests", async () => {
  for (const value of [null, "wrong shape", { text: 1 }, { text: "x".repeat(16001) }, { text: "malformed" },
    { text: "- forged source: viking://foreign/invented.md" }]) {
    const compressor = createV2Compressor(ctx(async () => value), config)
    const result = await compressRecallContext({ ...input, runCompressor: compressor.forSession("s").run })
    assert.equal(result.status, "failed")
  }
  const throwing = createV2Compressor(ctx(async () => { throw Error("offline/auth") }), config)
  assert.equal(await throwing.forSession("s").run("prompt"), null)
  let calls = 0
  const compressor = createV2Compressor(ctx(async () => { calls++; return { text: "unused" } }), config)
  assert.equal(await compressor.forSession("s").run("x".repeat(22001)), null)
  assert.equal(calls, 0)
  assert.equal((await compressRecallContext({ ...input, rendered: "short", runCompressor: compressor.forSession("s").run })).context, "short")
  assert.equal(calls, 0)
})

test("interruption invalidates callbacks reserved before retrieval and allows a later same-session prompt", async () => {
  let calls = 0
  const compressor = createV2Compressor(ctx(async () => { calls++; return { text: "digest" } }), config)
  const pendingRetrieval = compressor.forSession("session").run
  const unaffected = compressor.forSession("other").run
  compressor.cancel("session")
  assert.equal(await pendingRetrieval("prompt"), null)
  assert.equal(calls, 0)
  assert.equal(await compressor.forSession("session").run("prompt"), "digest")
  assert.equal(await unaffected("prompt"), "digest")
  assert.equal(calls, 2)
  const scheduled = compressor.forSession("session").run("prompt")
  compressor.cancel("session")
  assert.equal(await scheduled, null)
  assert.equal(calls, 2, "cancel before the generation microtask must prevent the native call")
})
