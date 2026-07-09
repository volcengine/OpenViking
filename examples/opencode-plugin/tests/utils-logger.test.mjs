import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { initLogger, log } from "../lib/utils.mjs"

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
