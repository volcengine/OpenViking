import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fetchJSON, initLogger, log } from "../lib/utils.mjs"

test("initLogger creates the OpenViking log file path and log writes JSONL", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-oc-log-"))
  try {
    initLogger(dir)
    log("INFO", "test", "hello", { ok: true })
    const raw = await readFile(join(dir, "openviking-memory.log"), "utf8")
    assert.match(raw, /"tool":"test"/)
    assert.match(raw, /"message":"hello"/)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
})

test("fetchJSON preserves commit trace_id on success and error", async (t) => {
  const responses = [
    new Response(JSON.stringify({
      status: "ok",
      result: { status: "accepted", trace_id: "trace-opencode-success" },
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
    new Response(JSON.stringify({
      status: "error",
      error: {
        code: "INTERNAL",
        message: "commit failed",
        trace_id: "trace-opencode-error",
      },
    }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    }),
  ]
  t.mock.method(globalThis, "fetch", async () => responses.shift())
  const config = {
    endpoint: "http://127.0.0.1:1933",
    timeoutMs: 5000,
  }

  const success = await fetchJSON(config, "/api/v1/sessions/success/commit")
  assert.equal(success.traceId, "trace-opencode-success")
  assert.equal(success.result.trace_id, "trace-opencode-success")

  const failure = await fetchJSON(config, "/api/v1/sessions/failure/commit")
  assert.equal(failure.ok, false)
  assert.equal(failure.traceId, "trace-opencode-error")
  assert.equal(failure.error.trace_id, "trace-opencode-error")
})
