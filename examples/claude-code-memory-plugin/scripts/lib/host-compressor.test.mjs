import assert from "node:assert/strict";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  clearHostCompressorBlock,
  createHostCompressor,
  loadHostCompressorBlock,
} from "./host-compressor.mjs";

const FAKE_FAIL = `#!/bin/sh
if [ "$1" = "--version" ]; then exit 0; fi
if [ -n "$COUNTER" ]; then echo x >> "$COUNTER"; fi
echo "no such model" >&2
exit 1
`;

const FAKE_OK = `#!/bin/sh
if [ "$1" = "--version" ]; then exit 0; fi
echo "OpenViking memory digest:"
echo "- one point"
`;

const FAKE_HANG = `#!/bin/sh
if [ "$1" = "--version" ]; then exit 0; fi
if [ -n "$COUNTER" ]; then echo x >> "$COUNTER"; fi
sleep 30
`;

/**
 * A hook is a fresh process, so the record has to live on disk under the home
 * that `statePath()` resolves at call time. Each case gets its own home.
 */
function withHome(fn) {
  const dir = mkdtempSync(join(tmpdir(), "cc-hc-"));
  const saved = process.env.HOME;
  process.env.HOME = dir;
  return Promise.resolve(fn(dir)).finally(() => {
    if (saved === undefined) delete process.env.HOME;
    else process.env.HOME = saved;
    rmSync(dir, { recursive: true, force: true });
  });
}

function fakeCli(dir, body, name = "claude") {
  const path = join(dir, name);
  writeFileSync(path, body, { mode: 0o755 });
  return path;
}

function baseCfg(command) {
  return {
    recallRewrite: "auto",
    recallCompressCommand: command,
    recallCompressModel: "haiku",
    recallCompressEffort: "low",
    recallCompressTimeoutMs: 5000,
    recallCompressDetectTtlMs: 604800000,
  };
}

async function waitFor(pred, ms = 3000) {
  const started = Date.now();
  while (Date.now() - started < ms) {
    if (await pred()) return true;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  return false;
}

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}

test("a failed digest is remembered, so the next prompt does not spawn again", () => withHome(async (dir) => {
  const command = fakeCli(dir, FAKE_FAIL);
  const cfg = baseCfg(command);
  const logs = [];
  const blockFile = join(dir, ".openviking", "state", "host-compressor-block.json");

  const first = await createHostCompressor(cfg, (event, data) => logs.push([event, data]));
  assert.equal(typeof first, "function", "the first prompt tries the compressor");
  assert.equal(await first("prompt"), "");

  assert.ok(await waitFor(() => exists(blockFile)), "the failure is written to disk");
  const record = JSON.parse(await readFile(blockFile, "utf8"));
  assert.equal(record.failedModel, "haiku");

  const second = await createHostCompressor(cfg, (event, data) => logs.push([event, data]));
  assert.equal(second, null, "the next prompt does not start the child at all");
  const blocked = logs.find(([event]) => event === "host_compressor_blocked");
  assert.ok(blocked, "the skip is logged as blocked, not as a failure");
  assert.equal(blocked[1].failedModel, "haiku");
}));

test("one hook spends at most one child, even when it asks twice", () => withHome(async (dir) => {
  const command = fakeCli(dir, FAKE_FAIL);
  const counter = join(dir, "runs.log");
  process.env.COUNTER = counter;
  try {
    const run = await createHostCompressor(baseCfg(command));
    assert.equal(await run("first"), "");
    assert.equal(await run("second"), "", "the second pass answers without spawning");
    const runs = (await readFile(counter, "utf8")).trim().split("\n");
    assert.equal(runs.length, 1);
  } finally {
    delete process.env.COUNTER;
  }
}));

test("changing the model earns a fresh attempt", () => withHome(async (dir) => {
  const command = fakeCli(dir, FAKE_FAIL);
  const cfg = baseCfg(command);

  const first = await createHostCompressor(cfg);
  assert.equal(await first("prompt"), "");
  assert.ok(await waitFor(async () => (await loadHostCompressorBlock(cfg, JSON.stringify({
    command, model: "haiku", effort: "low", mode: "auto",
  }))) !== null));

  assert.equal(await createHostCompressor(cfg), null);

  const other = await createHostCompressor({ ...cfg, recallCompressModel: "sonnet" });
  assert.equal(typeof other, "function", "a different model is a different attempt");
}));

test("an expired record no longer blocks", () => withHome(async (dir) => {
  const command = fakeCli(dir, FAKE_FAIL);
  const cfg = baseCfg(command);
  const key = JSON.stringify({ command, model: "haiku", effort: "low", mode: "auto" });
  await mkdir(join(dir, ".openviking", "state"), { recursive: true });
  await writeFile(join(dir, ".openviking", "state", "host-compressor-block.json"), JSON.stringify({
    configKey: key,
    failedModel: "haiku",
    checkedAt: Date.now() - 8 * 24 * 60 * 60 * 1000,
  }));

  assert.equal(await loadHostCompressorBlock(cfg, key), null);
  assert.equal(typeof (await createHostCompressor(cfg)), "function");
}));

test("SessionStart clears the record so a restart re-tries once", () => withHome(async (dir) => {
  const command = fakeCli(dir, FAKE_FAIL);
  const cfg = baseCfg(command);

  const first = await createHostCompressor(cfg);
  assert.equal(await first("prompt"), "");
  const blockFile = join(dir, ".openviking", "state", "host-compressor-block.json");
  assert.ok(await waitFor(() => exists(blockFile)));
  assert.equal(await createHostCompressor(cfg), null);

  await clearHostCompressorBlock();
  assert.equal(typeof (await createHostCompressor(cfg)), "function");
}));

test("a timeout is a failure worth remembering", () => withHome(async (dir) => {
  const command = fakeCli(dir, FAKE_HANG);
  const cfg = { ...baseCfg(command), recallCompressTimeoutMs: 5000 };
  const run = await createHostCompressor(cfg);
  assert.equal(await run("prompt"), "");
  assert.ok(await waitFor(() => exists(join(dir, ".openviking", "state", "host-compressor-block.json"))));
  assert.equal(await createHostCompressor(cfg), null);
}));

test("a working compressor is never recorded as failed", () => withHome(async (dir) => {
  const command = fakeCli(dir, FAKE_OK);
  const cfg = baseCfg(command);
  const blockFile = join(dir, ".openviking", "state", "host-compressor-block.json");

  const run = await createHostCompressor(cfg);
  assert.match(await run("prompt"), /memory digest/);
  assert.equal(await exists(blockFile), false, "success writes no record");
  assert.equal(typeof (await createHostCompressor(cfg)), "function");
}));
