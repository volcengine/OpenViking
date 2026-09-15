/**
 * The detached write path must survive a worker that never reads its payload.
 *
 * maybeDetach spawns the hook again with OV_HOOK_WORKER=1, approves the host's
 * hook protocol, and only then forwards the drained stdin payload. When the
 * worker dies before draining it, the pending write surfaces asynchronously as
 * an EPIPE 'error' event on the socket — after approve() has already run. An
 * unhandled 'error' there crashes the hook with a non-zero exit, turning a
 * fire-and-forget write path into a host-visible hook failure.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ASYNC_WRITER = join(HERE, "lib", "async-writer.mjs");

function runDriver(driverPath, payload) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [driverPath], { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
    child.stdin.end(payload);
  });
}

test("a worker that exits before draining its stdin does not crash the parent after approve", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-async-writer-"));
  // The worker exits without ever reading stdin, the way a real worker does
  // when it is killed or crashes during startup.
  const worker = join(dir, "die-without-reading.mjs");
  await writeFile(worker, "process.exit(0);\n");
  const driver = join(dir, "driver.mjs");
  await writeFile(driver, [
    `import { maybeDetach } from ${JSON.stringify(ASYNC_WRITER)};`,
    `process.argv[1] = ${JSON.stringify(worker)};`,
    "const detached = await maybeDetach({ writePathAsync: true }, { approve: () => console.log('APPROVED') });",
    "if (!detached) throw new Error('expected the detach path to run');",
    // Stay alive past the worker's exit so a stray unhandled 'error' still has
    // time to surface and fail this test.
    "setTimeout(() => console.log('SURVIVED'), 500);",
    "",
  ].join("\n"));

  // 256 KiB exceeds the pipe buffer, so the forwarded write is still pending
  // when the worker exits — the EPIPE is guaranteed, not timing-dependent.
  const result = await runDriver(driver, "x".repeat(256 * 1024));

  assert.equal(result.code, 0, `driver crashed (stderr: ${result.stderr})`);
  assert.match(result.stdout, /APPROVED/);
  assert.match(result.stdout, /SURVIVED/);
  assert.ok(!result.stderr.includes("EPIPE"), `unhandled EPIPE leaked to stderr: ${result.stderr}`);
});

test("the worker payload round-trips when the worker reads its stdin", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-async-writer-"));
  // A worker that echoes its stdin back through a file proves the forwarded
  // payload is intact when the write path is healthy.
  const received = join(dir, "received.txt");
  const worker = join(dir, "echo-worker.mjs");
  await writeFile(worker, [
    "import { writeFileSync } from 'node:fs';",
    "const chunks = [];",
    "for await (const chunk of process.stdin) chunks.push(chunk);",
    `writeFileSync(${JSON.stringify(received)}, Buffer.concat(chunks));`,
    "",
  ].join("\n"));
  const driver = join(dir, "driver.mjs");
  await writeFile(driver, [
    `import { maybeDetach } from ${JSON.stringify(ASYNC_WRITER)};`,
    `process.argv[1] = ${JSON.stringify(worker)};`,
    "const detached = await maybeDetach({ writePathAsync: true }, { approve: () => {} });",
    "if (!detached) throw new Error('expected the detach path to run');",
    "",
  ].join("\n"));

  const payload = `{"prompt":"${"y".repeat(96 * 1024)}"}`;
  const result = await runDriver(driver, payload);
  assert.equal(result.code, 0, `driver crashed (stderr: ${result.stderr})`);

  // The detached worker needs a moment after the parent exits; poll briefly.
  const { readFile } = await import("node:fs/promises");
  let forwarded = "";
  for (let i = 0; i < 40; i++) {
    try { forwarded = await readFile(received, "utf8"); break; } catch { await new Promise((r) => setTimeout(r, 100)); }
  }
  assert.equal(forwarded, payload, "the worker did not receive the exact payload");
});
